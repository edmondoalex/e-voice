"""Amazon LWA authorization and bounded Alexa Event Gateway reporting."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .domain.models import (
    AlexaAccountLink,
    AlexaDiscoveryDelivery,
    AlexaEventAuthorization,
    AlexaReportedState,
    AuditEvent,
    Entity,
    Installation,
)

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
logger = logging.getLogger(__name__)
REDACTED = "[REDACTED]"
SENSITIVE_DIAGNOSTIC_KEYS = {
    "access_token",
    "authorization",
    "client_secret",
    "refresh_token",
    "token",
}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _safe_diagnostic_value(value: Any, secrets: tuple[str, ...]) -> Any:
    """Return complete diagnostic data with credentials recursively redacted."""
    if isinstance(value, dict):
        return {
            key: REDACTED
            if key.lower() in SENSITIVE_DIAGNOSTIC_KEYS
            else _safe_diagnostic_value(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_diagnostic_value(item, secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if secret:
                result = result.replace(secret, REDACTED)
        return result
    return value


class AlexaEventGateway:
    """Store LWA credentials encrypted and publish idempotent ChangeReports."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        key = base64.urlsafe_b64encode(
            hashlib.sha256(self._settings.alexa_token_encryption_key.encode()).digest()
        )
        self._cipher = Fernet(key)
        self._client = client or httpx.AsyncClient(timeout=3.0)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def accept_grant(self, link: AlexaAccountLink, code: str) -> None:
        response = await self._client.post(
            LWA_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self._settings.alexa_lwa_client_id,
                "client_secret": self._settings.alexa_lwa_client_secret,
            },
        )
        response.raise_for_status()
        await self._store_tokens(link, response.json())

    async def _store_tokens(self, link: AlexaAccountLink, payload: dict[str, Any]) -> None:
        access, refresh = str(payload["access_token"]), str(payload["refresh_token"])
        expires_in = min(max(int(payload.get("expires_in", 3600)), 60), 86400)
        authorization = await self._session.scalar(
            select(AlexaEventAuthorization).where(AlexaEventAuthorization.link_id == link.id)
        )
        if authorization is None:
            authorization = AlexaEventAuthorization(
                link_id=link.id,
                access_token_encrypted=b"",
                refresh_token_encrypted=b"",
                expires_at=datetime.now(UTC),
            )
            self._session.add(authorization)
        authorization.access_token_encrypted = self._cipher.encrypt(access.encode())
        authorization.refresh_token_encrypted = self._cipher.encrypt(refresh.encode())
        authorization.expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        authorization.revoked_at = None
        await self._session.commit()

    async def _refresh(self, authorization: AlexaEventAuthorization) -> str:
        refresh = self._cipher.decrypt(authorization.refresh_token_encrypted).decode()
        response = await self._client.post(
            LWA_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": self._settings.alexa_lwa_client_id,
                "client_secret": self._settings.alexa_lwa_client_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()
        access = str(payload["access_token"])
        authorization.access_token_encrypted = self._cipher.encrypt(access.encode())
        if payload.get("refresh_token"):
            authorization.refresh_token_encrypted = self._cipher.encrypt(
                str(payload["refresh_token"]).encode()
            )
        authorization.expires_at = datetime.now(UTC) + timedelta(
            seconds=min(max(int(payload.get("expires_in", 3600)), 60), 86400)
        )
        await self._session.commit()
        return access

    async def report_entity(self, entity: Entity, *, cause: str = "PHYSICAL_INTERACTION") -> int:
        """Report one M5 state to every active linked account, once per value."""
        from .alexa import endpoint_id, state_properties

        installation = await self._session.get(Installation, entity.installation_id)
        if installation is None or entity.deleted_at is not None:
            return 0
        links = (
            await self._session.scalars(
                select(AlexaAccountLink).where(
                    AlexaAccountLink.tenant_id == installation.tenant_id,
                    AlexaAccountLink.status == "active",
                )
            )
        ).all()
        properties = state_properties(entity)
        canonical = [
            {key: item[key] for key in ("namespace", "name", "instance", "value") if key in item}
            for item in properties
        ]
        fingerprint = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        sent = 0
        for link in links:
            authorization = await self._session.scalar(
                select(AlexaEventAuthorization).where(
                    AlexaEventAuthorization.link_id == link.id,
                    AlexaEventAuthorization.revoked_at.is_(None),
                )
            )
            if authorization is None:
                continue
            previous = await self._session.scalar(
                select(AlexaReportedState).where(
                    AlexaReportedState.link_id == link.id,
                    AlexaReportedState.entity_id == entity.id,
                )
            )
            if previous is not None and previous.property_fingerprint == fingerprint:
                continue
            old = {
                (item["namespace"], item["name"], item.get("instance")): item.get("value")
                for item in (previous.properties_json if previous else [])
            }
            changed = [
                item
                for item, value in zip(properties, canonical, strict=True)
                if old.get((value["namespace"], value["name"], value.get("instance")))
                != value["value"]
            ]
            changed_keys = {
                (item["namespace"], item["name"], item.get("instance")) for item in changed
            }
            context = [
                item
                for item in properties
                if (item["namespace"], item["name"], item.get("instance")) not in changed_keys
            ]
            event = {
                "context": {"properties": context},
                "event": {
                    "header": {
                        "namespace": "Alexa",
                        "name": "ChangeReport",
                        "payloadVersion": "3",
                        "messageId": str(uuid4()),
                    },
                    "endpoint": {
                        "scope": {"type": "BearerToken", "token": ""},
                        "endpointId": endpoint_id(entity),
                    },
                    "payload": {
                        "change": {
                            "cause": {"type": cause},
                            "properties": changed,
                        }
                    },
                },
            }
            if await self._send(authorization, event):
                if previous is None:
                    previous = AlexaReportedState(
                        link_id=link.id,
                        entity_id=entity.id,
                        property_fingerprint=fingerprint,
                        properties_json=canonical,
                    )
                    self._session.add(previous)
                previous.property_fingerprint = fingerprint
                previous.properties_json = canonical
                previous.reported_at = datetime.now(UTC)
                await self._session.commit()
                sent += 1
        return sent

    async def reconcile_discovery(self, installation: Installation, *, force: bool = False) -> int:
        """Publish changed, or explicitly forced, endpoints for one installation."""
        from .alexa import SUPPORTED_DOMAINS, alexa_entity_eligible, discovery_endpoint
        from .entity_names import unambiguous_voice_entities

        entities = list(
            (
                await self._session.scalars(
                    select(Entity).where(
                        Entity.installation_id == installation.id,
                        Entity.deleted_at.is_(None),
                        Entity.ha_domain.in_(SUPPORTED_DOMAINS),
                    )
                )
            ).all()
        )
        entities = unambiguous_voice_entities(
            [entity for entity in entities if alexa_entity_eligible(entity)]
        )
        current: dict[str, tuple[Entity, dict[str, Any], str]] = {}
        for entity in entities:
            endpoint = discovery_endpoint(entity)
            fingerprint = hashlib.sha256(
                json.dumps(endpoint, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            current[str(endpoint["endpointId"])] = (entity, endpoint, fingerprint)
        links = list(
            (
                await self._session.scalars(
                    select(AlexaAccountLink).where(
                        AlexaAccountLink.tenant_id == installation.tenant_id,
                        AlexaAccountLink.status == "active",
                    )
                )
            ).all()
        )
        sent = 0
        for link in links:
            authorization = await self._session.scalar(
                select(AlexaEventAuthorization).where(
                    AlexaEventAuthorization.link_id == link.id,
                    AlexaEventAuthorization.revoked_at.is_(None),
                )
            )
            deliveries = list(
                (
                    await self._session.scalars(
                        select(AlexaDiscoveryDelivery).where(
                            AlexaDiscoveryDelivery.link_id == link.id,
                            AlexaDiscoveryDelivery.installation_id == installation.id,
                        )
                    )
                ).all()
            )
            previous = {item.alexa_endpoint_id: item for item in deliveries}
            updates = (
                list(current.values())
                if force
                else [
                    value
                    for endpoint_id, value in current.items()
                    if endpoint_id not in previous
                    or previous[endpoint_id].removed_at is not None
                    or previous[endpoint_id].representation_fingerprint != value[2]
                ]
            )
            deletions = [
                item
                for endpoint_id, item in previous.items()
                if item.removed_at is None and endpoint_id not in current
            ]
            if authorization is None:
                await self._audit_discovery(
                    installation,
                    "alexa.discovery.authorization_missing",
                    None,
                    None,
                    "skipped",
                )
                await self._session.commit()
                continue
            if updates:
                event = self._discovery_event(
                    "AddOrUpdateReport", [endpoint for _, endpoint, _ in updates]
                )
                success = await self._send(
                    authorization,
                    event,
                    diagnostic_installation=installation,
                )
                now = datetime.now(UTC)
                for entity, endpoint, fingerprint in updates:
                    endpoint_value = str(endpoint["endpointId"])
                    delivery = previous.get(endpoint_value)
                    if success:
                        if delivery is None:
                            delivery = AlexaDiscoveryDelivery(
                                link_id=link.id,
                                installation_id=installation.id,
                                entity_id=entity.id,
                                alexa_endpoint_id=endpoint_value,
                                representation_fingerprint=fingerprint,
                                published_at=now,
                            )
                            self._session.add(delivery)
                        delivery.entity_id = entity.id
                        delivery.representation_fingerprint = fingerprint
                        delivery.published_at = now
                        delivery.removed_at = None
                        sent += 1
                    await self._audit_discovery(
                        installation,
                        "alexa.discovery.add_or_update",
                        entity.id,
                        endpoint_value,
                        "success" if success else "error",
                    )
            if deletions:
                event = self._discovery_event(
                    "DeleteReport",
                    [{"endpointId": item.alexa_endpoint_id} for item in deletions],
                )
                success = await self._send(authorization, event)
                now = datetime.now(UTC)
                for delivery in deletions:
                    if success:
                        delivery.removed_at = now
                        sent += 1
                    await self._audit_discovery(
                        installation,
                        "alexa.discovery.delete",
                        delivery.entity_id,
                        delivery.alexa_endpoint_id,
                        "success" if success else "error",
                    )
            await self._session.commit()
        return sent

    async def send_positioned_cover_diagnostic(
        self, installation: Installation, entity: Entity
    ) -> int:
        """Publish only the isolated clean RangeController diagnostic endpoint."""
        from .alexa import (
            POSITIONED_COVER_DIAGNOSTIC_ENTITY_ID,
            positioned_cover_diagnostic_endpoint,
        )

        if (
            entity.installation_id != installation.id
            or entity.ha_entity_id != POSITIONED_COVER_DIAGNOSTIC_ENTITY_ID
            or entity.deleted_at is not None
        ):
            raise ValueError("invalid positioned-cover diagnostic entity")
        endpoint = positioned_cover_diagnostic_endpoint(entity)
        event = self._discovery_event("AddOrUpdateReport", [endpoint])
        links = list(
            (
                await self._session.scalars(
                    select(AlexaAccountLink).where(
                        AlexaAccountLink.tenant_id == installation.tenant_id,
                        AlexaAccountLink.status == "active",
                    )
                )
            ).all()
        )
        accepted = 0
        for link in links:
            authorization = await self._session.scalar(
                select(AlexaEventAuthorization).where(
                    AlexaEventAuthorization.link_id == link.id,
                    AlexaEventAuthorization.revoked_at.is_(None),
                )
            )
            if authorization is not None and await self._send(
                authorization, event, diagnostic_installation=installation
            ):
                accepted += 1
        await self._session.commit()
        return accepted

    @staticmethod
    def _discovery_event(name: str, endpoints: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "event": {
                "header": {
                    "namespace": "Alexa.Discovery",
                    "name": name,
                    "payloadVersion": "3",
                    "messageId": str(uuid4()),
                },
                "payload": {
                    "scope": {"type": "BearerToken", "token": ""},
                    "endpoints": endpoints,
                },
            }
        }

    async def _audit_discovery(
        self,
        installation: Installation,
        event_type: str,
        entity_id: object | None,
        endpoint_value: str | None,
        result: str,
    ) -> None:
        payload = {"endpoint_id": endpoint_value} if endpoint_value else {}
        if entity_id is not None:
            payload["entity_id"] = str(entity_id)
        self._session.add(
            AuditEvent(
                tenant_id=installation.tenant_id,
                installation_id=installation.id,
                source="alexa_event_gateway",
                event_type=event_type,
                payload_redacted_json=payload,
                result=result,
            )
        )

    async def _send(
        self,
        authorization: AlexaEventAuthorization,
        event: dict[str, Any],
        *,
        diagnostic_installation: Installation | None = None,
    ) -> bool:
        access = self._cipher.decrypt(authorization.access_token_encrypted).decode()
        if _utc(authorization.expires_at) <= datetime.now(UTC) + timedelta(seconds=30):
            try:
                access = await self._refresh(authorization)
            except httpx.HTTPError as error:
                logger.warning("Alexa LWA token refresh failed before event delivery")
                request_event = json.loads(json.dumps(event))
                await self._audit_add_or_update_delivery(
                    diagnostic_installation, request_event, None, error, 0, access
                )
                return False
        response: httpx.Response | None = None
        transport_error: httpx.HTTPError | None = None
        for attempt in range(3):
            event_body = event["event"]
            request_event = json.loads(json.dumps(event))
            request_event_body = request_event["event"]
            scope_parent = request_event_body.get("endpoint") or request_event_body.get("payload")
            scope_parent["scope"]["token"] = access
            serialized = json.dumps(
                request_event, ensure_ascii=False, separators=(",", ":")
            ).encode()
            try:
                response = await self._client.post(
                    self._settings.alexa_event_gateway_url,
                    content=serialized,
                    headers={
                        "Authorization": f"Bearer {access}",
                        "Content-Type": "application/json",
                    },
                )
                transport_error = None
            except httpx.HTTPError as error:
                transport_error = error
                response = None
                logger.warning(
                    "Alexa Event Gateway transport error event=%s attempt=%d",
                    event_body["header"]["name"],
                    attempt + 1,
                )
                if attempt < 2:
                    await asyncio.sleep(attempt + 1)
                continue
            if response.is_success:
                await self._audit_add_or_update_delivery(
                    diagnostic_installation, request_event, response, None, attempt + 1, access
                )
                return True
            if response.status_code not in {401, 429} and response.status_code < 500:
                await self._audit_add_or_update_delivery(
                    diagnostic_installation, request_event, response, None, attempt + 1, access
                )
                return False
            if response.status_code == 401:
                try:
                    access = await self._refresh(authorization)
                except httpx.HTTPError as error:
                    logger.warning("Alexa LWA token refresh failed after gateway HTTP 401")
                    await self._audit_add_or_update_delivery(
                        diagnostic_installation,
                        request_event,
                        response,
                        error,
                        attempt + 1,
                        access,
                    )
                    return False
            if attempt < 2:
                await asyncio.sleep(attempt + 1)
        await self._audit_add_or_update_delivery(
            diagnostic_installation,
            request_event,
            response,
            transport_error,
            3,
            access,
        )
        return False

    async def _audit_add_or_update_delivery(
        self,
        installation: Installation | None,
        request_event: dict[str, Any],
        response: httpx.Response | None,
        error: httpx.HTTPError | None,
        attempts: int,
        access_token: str,
    ) -> None:
        """Persist one secret-free diagnostic for an AddOrUpdateReport delivery."""
        event = request_event["event"]
        header = event["header"]
        if installation is None or header.get("name") != "AddOrUpdateReport":
            return
        endpoint_ids = [
            str(endpoint["endpointId"])
            for endpoint in event.get("payload", {}).get("endpoints", [])
            if endpoint.get("endpointId") is not None
        ]
        message_id = str(header.get("messageId", ""))
        response_body: Any = None
        if response is not None and response.content:
            try:
                response_body = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                response_body = response.text
        secrets = (access_token,)
        payload = {
            "correlation_id": message_id,
            "message_id": message_id,
            "endpoint_id": endpoint_ids[0] if len(endpoint_ids) == 1 else None,
            "endpoint_ids": endpoint_ids,
            "request_payload": _safe_diagnostic_value(request_event, secrets),
            "http_status": response.status_code if response is not None else None,
            "response_body": _safe_diagnostic_value(response_body, secrets),
            "error": _safe_diagnostic_value(str(error), secrets) if error is not None else None,
            "error_type": type(error).__name__ if error is not None else None,
            "attempts": attempts,
        }
        self._session.add(
            AuditEvent(
                tenant_id=installation.tenant_id,
                installation_id=installation.id,
                source="alexa_event_gateway",
                event_type="alexa.event_gateway.add_or_update",
                request_id=message_id,
                payload_redacted_json=payload,
                result="success" if response is not None and response.is_success else "error",
            )
        )


async def reconcile_discovery_safely(
    session: AsyncSession, installation: Installation, *, force: bool = False
) -> int | None:
    """Run bounded proactive discovery without failing the authoritative sync."""
    gateway = AlexaEventGateway(session)
    try:
        return await gateway.reconcile_discovery(installation, force=force)
    except Exception:  # The external observability path must never fail entity synchronization.
        await session.rollback()
        logger.exception("Alexa proactive discovery failed installation_id=%s", installation.id)
        return None
    finally:
        await gateway.close()


async def send_positioned_cover_diagnostic_safely(
    session: AsyncSession, installation: Installation, entity: Entity
) -> int | None:
    """Run the isolated positioned-cover diagnostic without affecting normal reconciliation."""
    gateway = AlexaEventGateway(session)
    try:
        return await gateway.send_positioned_cover_diagnostic(installation, entity)
    except Exception:
        await session.rollback()
        logger.exception(
            "Alexa positioned-cover diagnostic failed installation_id=%s entity_id=%s",
            installation.id,
            entity.id,
        )
        return None
    finally:
        await gateway.close()
