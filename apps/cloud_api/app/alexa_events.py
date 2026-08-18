"""Amazon LWA authorization and bounded Alexa Event Gateway reporting."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
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
    AlexaEventAuthorization,
    AlexaReportedState,
    Entity,
    Installation,
)

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
RETRYABLE_STATUS = {401, 429, 503}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


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

    async def _send(self, authorization: AlexaEventAuthorization, event: dict[str, Any]) -> bool:
        access = self._cipher.decrypt(authorization.access_token_encrypted).decode()
        if _utc(authorization.expires_at) <= datetime.now(UTC) + timedelta(seconds=30):
            access = await self._refresh(authorization)
        for attempt in range(3):
            event["event"]["endpoint"]["scope"]["token"] = access
            response = await self._client.post(
                self._settings.alexa_event_gateway_url,
                json=event,
                headers={"Authorization": f"Bearer {access}"},
            )
            if response.is_success:
                return True
            if response.status_code not in RETRYABLE_STATUS:
                return False
            if response.status_code == 401:
                access = await self._refresh(authorization)
            if attempt < 2:
                await asyncio.sleep(attempt + 1)
        return False
