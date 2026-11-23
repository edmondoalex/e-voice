"""CHANGE083 staged service receiver; disabled unless a key provider is installed.

No browser credential is accepted. Activation and legacy-engine fencing are
separate work: this endpoint is not eligible for deployment by itself.
"""

import hashlib
import hmac
import json
import re
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import BigInteger, ForeignKey, Integer, String, Uuid, delete, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .config import get_settings
from .database import Base, get_database_session
from .domain.enums import InstallationStatus
from .domain.models import Entity, Installation, WearDevice, WearNotification
from .wear_notifications import SEVERITIES, SEVERITY_LEVEL, _preferences

router = APIRouter(tags=["wear-bridge"])

# Set from a read-only schema check at startup; never infer it from user input.
_bridge_schema_present = False


class WearBridgeAuthority(Base):
    __tablename__ = "wear_bridge_authority"
    installation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("installations.id"), primary_key=True
    )
    owner: Mapped[str] = mapped_column(String(16))
    generation: Mapped[int] = mapped_column(BigInteger)
    import_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WearBridgeNonce(Base):
    __tablename__ = "wear_bridge_nonce"
    installation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("installations.id"), primary_key=True
    )
    nonce: Mapped[str] = mapped_column(String(128), primary_key=True)
    expires: Mapped[int] = mapped_column(BigInteger)


class WearBridgeInbox(Base):
    __tablename__ = "wear_bridge_inbox"
    installation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("installations.id"), primary_key=True
    )
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    body_hash: Mapped[str] = mapped_column(String(64))


class WearBridgeWake(Base):
    __tablename__ = "wear_bridge_wake"
    device_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("wear_devices.id"), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


def canonical(claims, body):
    return json.dumps(
        {"claims": claims, "body": body},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def derive_installation_key(master: bytes, installation: UUID) -> bytes:
    """Derive a scoped key from a dedicated server-held master (never browser credentials)."""
    if not isinstance(master, bytes) or len(master) < 32 or not isinstance(installation, UUID):
        raise ValueError("invalid_bridge_master")
    return hmac.new(
        master, b"ekonex-wear-bridge-v1\0" + installation.bytes, hashlib.sha256
    ).digest()


def verify_service_key(provided, supplied, payload):
    """Accept current and optional previous key during a controlled overlap."""
    keys = (provided,) if isinstance(provided, bytes) else provided
    if (
        not isinstance(keys, (tuple, list))
        or not 1 <= len(keys) <= 2
        or not isinstance(supplied, str)
    ):
        raise ValueError("invalid_bridge_keyring")
    if any(not isinstance(key, bytes) or not 32 <= len(key) <= 128 for key in keys):
        raise ValueError("invalid_bridge_keyring")
    matches = [
        hmac.compare_digest(hmac.new(key, payload, hashlib.sha256).hexdigest(), supplied)
        for key in keys
    ]
    if not any(matches):
        raise ValueError("invalid_bridge_signature")
    return keys[matches.index(True)]


async def verify_bridge_startup(session_factory, *, fence_enabled: bool) -> None:
    """Detect incompatible disabled restart; a missing pre-feature schema stays legacy.

    No writes or migrations are performed. Database failures abort startup rather
    than guessing that previously transferred authority is absent.
    """
    global _bridge_schema_present
    _bridge_schema_present = False
    async with session_factory() as session:
        connection = await session.connection()
        present = await connection.run_sync(
            lambda conn: inspect(conn).has_table("wear_bridge_authority")
        )
        if not present:
            await session.rollback()
            if fence_enabled:
                raise RuntimeError("wear_bridge_schema_missing")
            return
        _bridge_schema_present = True
        unsafe = await session.scalar(
            select(WearBridgeAuthority.installation_id)
            .where(WearBridgeAuthority.owner != "legacy")
            .limit(1)
        )
        await session.rollback()
        if unsafe is not None and not fence_enabled:
            raise RuntimeError("wear_bridge_disable_requires_rollback")


def _in_scope(settings, installation: Installation) -> bool:
    """Explicit tenant?installation allowlist; missing configuration enrolls nobody."""
    scope = getattr(settings, "wear_bridge_scope", {})
    if not isinstance(scope, dict):
        raise HTTPException(503, "wear_scope_invalid")
    values = scope.get(str(installation.tenant_id), [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise HTTPException(503, "wear_scope_invalid")
    return str(installation.id) in values


async def lock_wear_writer(
    session: AsyncSession, installation: UUID, *, allow_econtrol: bool = False
):
    """Serialize writers and bootstrap; unscoped installations stay legacy.

    Existing authority is enforced even if scope is later removed, so deleting
    configuration cannot silently restore legacy writes after cutover.
    """
    global _bridge_schema_present
    settings = get_settings()
    # This row lock is shared with bootstrap. Even a legacy process that started
    # before the migration must recheck schema under that lock, so bootstrap
    # cannot race past an in-flight legacy write on PostgreSQL.
    instance = await session.scalar(
        select(Installation)
        .where(Installation.id == installation)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if instance is None:
        raise HTTPException(503, "wear_installation_unavailable")
    if not _bridge_schema_present:
        connection = await session.connection()
        present = await connection.run_sync(
            lambda conn: inspect(conn).has_table("wear_bridge_authority")
        )
        if not present:
            if settings.wear_bridge_fence_enabled:
                raise HTTPException(503, "wear_bridge_schema_missing")
            return None
        _bridge_schema_present = True
    authority = await session.scalar(
        select(WearBridgeAuthority)
        .where(WearBridgeAuthority.installation_id == installation)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if authority is None:
        if settings.wear_bridge_fence_enabled and _in_scope(settings, instance):
            raise HTTPException(503, "wear_authority_unavailable")
        return None
    if authority.owner not in {"legacy", "econtrol"}:
        raise HTTPException(503, "wear_authority_unavailable")
    if not settings.wear_bridge_fence_enabled and authority.owner == "legacy":
        return None
    if authority.owner != "legacy" and not allow_econtrol:
        raise HTTPException(409, "wear_owned_by_econtrol")
    return authority


async def bootstrap_wear_authority(
    session: AsyncSession, tenant: UUID, installation: UUID, *, qualified: bool = False
):
    """Provision legacy authority atomically; unexposed and disabled by default.

    All callers must independently authenticate tenant, administrative role and
    approved provisioning. The caller controls commit and may safely roll back.
    """
    settings = get_settings()
    if qualified is not True or not settings.wear_bridge_fence_enabled:
        raise HTTPException(503, "wear_bootstrap_disabled")
    if not isinstance(tenant, UUID) or not isinstance(installation, UUID):
        raise HTTPException(403, "wear_bootstrap_denied")
    instance = await session.scalar(
        select(Installation)
        .where(Installation.id == installation)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        instance is None
        or instance.tenant_id != tenant
        or instance.status != InstallationStatus.ACTIVE
        or not _in_scope(settings, instance)
    ):
        raise HTTPException(403, "wear_bootstrap_denied")
    authority = await session.scalar(
        select(WearBridgeAuthority)
        .where(WearBridgeAuthority.installation_id == installation)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if authority is not None:
        if authority.owner not in {"legacy", "econtrol"}:
            raise HTTPException(503, "wear_authority_unavailable")
        return authority
    authority = WearBridgeAuthority(installation_id=installation, owner="legacy", generation=0)
    session.add(authority)
    await session.flush()
    return authority


@router.post("/service/wear/notifications")
async def receive_notification(
    request: Request, session: Annotated[AsyncSession, Depends(get_database_session)]
):
    provider = getattr(request.app.state, "wear_bridge_key_provider", None)
    if provider is None or not get_settings().wear_bridge_fence_enabled:
        raise HTTPException(503, "wear_bridge_disabled")
    raw = await request.body()
    if len(raw) > 8192:
        raise HTTPException(413, "request_too_large")
    try:
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {"claims", "body", "signature"}:
            raise ValueError
        claims, body, sig = envelope["claims"], envelope["body"], envelope["signature"]
        keys = {
            "issuer",
            "audience",
            "installation",
            "scope",
            "method",
            "path",
            "issued",
            "expires",
            "nonce",
        }
        if not isinstance(claims, dict) or set(claims) != keys or not isinstance(body, dict):
            raise ValueError
        installation = UUID(claims["installation"])
        now = int(time.time())
        if (
            claims["issuer"] != "econtrol"
            or claims["audience"] != "evoice-wear-service"
            or claims["scope"] != "wear.notifications.enqueue"
            or claims["method"] != "POST"
            or claims["path"] != "/service/wear/notifications"
            or type(claims["issued"]) is not int
            or type(claims["expires"]) is not int
            or claims["issued"] > now + 30
            or claims["expires"] <= now
            or not 0 < claims["expires"] - claims["issued"] <= 60
            or not isinstance(claims["nonce"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", claims["nonce"])
        ):
            raise ValueError
        verify_service_key(await provider(installation), sig, canonical(claims, body))
        if set(body) not in (
            {"event_id", "generation", "device_id", "category", "severity", "title", "body"},
            {
                "event_id",
                "generation",
                "device_id",
                "category",
                "severity",
                "title",
                "body",
                "render",
            },
        ):
            raise ValueError
        if (
            not isinstance(body["event_id"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", body["event_id"])
            or type(body["generation"]) is not int
        ):
            raise ValueError
        device_id = UUID(body["device_id"])
        render = body.get("render")
        if "render" in body and not getattr(get_settings(), "wear_render_v2_enabled", False):
            raise ValueError
        if "render" in body:
            if not isinstance(render, dict) or set(render) != {
                "entity_id",
                "ha_entity_id",
                "registry_id",
                "previous_state",
                "state",
                "available",
                "title_template",
                "body_template",
            }:
                raise ValueError
            entity_id = UUID(render["entity_id"])
            if (
                not isinstance(render["registry_id"], str)
                or not 1 <= len(render["registry_id"]) <= 255
                or not isinstance(render["ha_entity_id"], str)
                or not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", render["ha_entity_id"])
                or not isinstance(render["previous_state"], str)
                or len(render["previous_state"]) > 1024
                or not isinstance(render["state"], str)
                or len(render["state"]) > 1024
                or type(render["available"]) is not bool
                or any(
                    v is not None and (not isinstance(v, str) or len(v) > limit)
                    for v, limit in (
                        (render["title_template"], 120),
                        (render["body_template"], 500),
                    )
                )
            ):
                raise ValueError
        if (
            body["severity"] not in SEVERITIES
            or not isinstance(body["category"], str)
            or not 1 <= len(body["category"]) <= 64
            or not isinstance(body["title"], str)
            or not 1 <= len(body["title"]) <= 120
            or not isinstance(body["body"], str)
            or not 1 <= len(body["body"]) <= 500
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(403, "wear_bridge_denied") from None
    # Use the writer/revoke lock order before inserts referencing Installation.
    instance = await session.scalar(
        select(Installation)
        .where(Installation.id == installation)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if instance is None:
        await session.rollback()
        raise HTTPException(409, "wear_bridge_fence")
    authority = await session.scalar(
        select(WearBridgeAuthority)
        .where(WearBridgeAuthority.installation_id == installation)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if claims["expires"] <= int(time.time()):
        await session.rollback()
        raise HTTPException(403, "wear_bridge_expired")
    if (
        authority is None
        or authority.owner != "econtrol"
        or authority.generation != body["generation"]
    ):
        await session.rollback()
        raise HTTPException(409, "wear_bridge_fence")
    await session.execute(
        delete(WearBridgeNonce).where(
            WearBridgeNonce.installation_id == installation,
            WearBridgeNonce.expires <= int(time.time()),
        )
    )
    if await session.get(WearBridgeNonce, (installation, claims["nonce"])) is not None:
        await session.rollback()
        raise HTTPException(403, "wear_bridge_replay")
    digest = hashlib.sha256(canonical({}, body)).hexdigest()
    prior = await session.get(WearBridgeInbox, (installation, body["event_id"]))
    if prior is not None and prior.body_hash != digest:
        await session.rollback()
        raise HTTPException(409, "wear_bridge_conflict")
    if prior is not None:
        # The original transaction already decided delivery eligibility. Return
        # its receipt even if the watch was later revoked; never create again.
        session.add(
            WearBridgeNonce(
                installation_id=installation, nonce=claims["nonce"], expires=claims["expires"]
            )
        )
        await session.commit()
        return {"event_id": body["event_id"], "accepted": True}
    device = await session.scalar(
        select(WearDevice)
        .where(
            WearDevice.id == device_id,
            WearDevice.installation_id == installation,
            WearDevice.revoked_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if device is None or "notification.receive" not in device.capabilities_json:
        await session.rollback()
        raise HTTPException(404, "wear_bridge_device")
    session.add(
        WearBridgeNonce(
            installation_id=installation, nonce=claims["nonce"], expires=claims["expires"]
        )
    )
    title, notification_body = body["title"], body["body"]
    if render is not None:
        # Resolve the current vocal name only at first acceptance. The signed
        # event contains the captured state, so retry never rewrites a receipt.
        entity = await session.scalar(
            select(Entity)
            .where(
                Entity.id == entity_id,
                Entity.installation_id == installation,
                Entity.deleted_at.is_(None),
                Entity.ha_registry_id == render["registry_id"],
                Entity.ha_entity_id == render["ha_entity_id"],
            )
            .execution_options(populate_existing=True)
        )
        if entity is None:
            await session.rollback()
            raise HTTPException(409, "wear_bridge_entity_changed")
        from types import SimpleNamespace

        from .wear_notification_rules import render_wear_rule_message

        view = SimpleNamespace(
            voice_name=entity.voice_name,
            display_name=entity.display_name,
            friendly_name=entity.friendly_name,
            ha_entity_id=render["ha_entity_id"],
            state=render["state"],
            available=render["available"],
        )
        title, notification_body = render_wear_rule_message(
            {"title": render["title_template"], "body": render["body_template"]},
            view,
            render["previous_state"],
        )
    prefs = _preferences(device)
    if (
        prefs["enabled"]
        and body["category"] in prefs["categories"]
        and SEVERITY_LEVEL[body["severity"]]
        >= SEVERITY_LEVEL.get(str(prefs["minimum_severity"]), 0)
    ):
        event_key = "econtrol:" + body["event_id"]
        session.add(
            WearNotification(
                device_id=device.id,
                tenant_id=device.tenant_id,
                installation_id=installation,
                event_key=event_key,
                category=body["category"],
                severity=body["severity"],
                title=title,
                body=notification_body,
            )
        )
        if device.fcm_token_encrypted:
            session.add(WearBridgeWake(device_id=device.id, event_key=event_key, attempts=0))
    session.add(
        WearBridgeInbox(installation_id=installation, event_id=body["event_id"], body_hash=digest)
    )
    await session.commit()
    # Poll-based delivery is sufficient here; FCM wake must be dispatched
    # after commit by a durable worker.
    return {"event_id": body["event_id"], "accepted": True}
