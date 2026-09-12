"""Tenant-scoped internal Media API for e-Face X4."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .command_dispatch import CommandDispatchService, command_adapter
from .database import get_database_session
from .domain.models import AuditEvent, Entity, Installation, MediaApiCredential
from .evcp import sessions
from .media_realtime import media_events
from .pairing_api import identity_dependency
from .portal_auth import PortalIdentity

router = APIRouter(prefix="/api/media/v1", tags=["ekonex-media"])
session_dependency = Depends(get_database_session)


@router.get("")
async def media_api_status() -> dict[str, object]:
    """Expose a secret-free readiness document for deployment checks."""
    return {
        "service": "ekonex_media",
        "api_version": "v1",
        "status": "active",
        "authentication": ["portal_cookie", "bearer"],
    }


@dataclass(frozen=True, slots=True)
class MediaAccessContext:
    tenant_id: UUID
    user_id: UUID | None
    installation_id: UUID | None


async def _media_context(
    identity: Annotated[PortalIdentity | None, identity_dependency],
    session: Annotated[AsyncSession, session_dependency],
    authorization: Annotated[str | None, Header()] = None,
) -> MediaAccessContext:
    if authorization is not None:
        if not authorization.startswith("Bearer emf_"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenziale Media non valida")
        digest = hashlib.sha256(authorization.removeprefix("Bearer ").encode()).hexdigest()
        credential = await session.scalar(
            select(MediaApiCredential).where(
                MediaApiCredential.secret_hash == digest,
                MediaApiCredential.revoked_at.is_(None),
            )
        )
        if credential is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenziale Media non valida")
        credential.last_used_at = datetime.now(UTC)
        await session.commit()
        return MediaAccessContext(credential.tenant_id, None, credential.installation_id)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")
    if identity.context is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Seleziona un tenant")
    return MediaAccessContext(identity.context.tenant_id, identity.context.user_id, None)


media_context_dependency = Depends(_media_context)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PlayerCommandRequest(StrictModel):
    request_id: UUID
    operation: Literal[
        "media_play",
        "media_pause",
        "media_stop",
        "media_next",
        "media_previous",
        "volume_mute",
        "volume_unmute",
        "media_unjoin",
        "set_volume",
        "select_source",
        "media_join",
        "tts",
        "set_dnd",
    ]
    arguments: dict[str, object]
    expected_resource_revision: int | None = Field(default=None, ge=0)


class GroupCommandRequest(StrictModel):
    request_id: UUID
    operation: Literal["set_group_volume", "media_play", "media_pause", "media_stop"]
    arguments: dict[str, object]
    expected_resource_revision: int | None = Field(default=None, ge=0)


class FavoritePlayRequest(StrictModel):
    request_id: UUID


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _resource_revision(entity: Entity) -> int:
    value = entity.updated_at or entity.last_seen_at or datetime.fromtimestamp(0, UTC)
    return int(value.timestamp() * 1_000_000)


async def _installation(
    session: AsyncSession, context: MediaAccessContext, value: UUID
) -> Installation:
    if context.installation_id is not None and context.installation_id != value:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Impianto non trovato")
    installation = await session.scalar(
        select(Installation).where(
            Installation.id == value, Installation.tenant_id == context.tenant_id
        )
    )
    if installation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Impianto non trovato")
    return installation


async def _entities(session: AsyncSession, installation_id: UUID) -> list[Entity]:
    values = list(
        (
            await session.scalars(
                select(Entity)
                .where(
                    Entity.installation_id == installation_id,
                    Entity.ha_domain == "media_player",
                    Entity.deleted_at.is_(None),
                )
                .order_by(Entity.ha_registry_id)
            )
        ).all()
    )
    return values


def _fingerprint(entity: Entity) -> str | None:
    attributes = entity.attributes_json or {}
    text = "\n".join(
        str(attributes.get(key) or "").strip()
        for key in (
            "media_content_id",
            "media_title",
            "media_artist",
            "media_album_name",
            "media_duration",
        )
    )
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest() if text.strip() else None


def _player(entity: Entity, installation: Installation) -> dict[str, Any]:
    attrs, features = entity.attributes_json or {}, int(entity.supported_features or 0)
    volume = attrs.get("volume_level")
    return {
        "installation_id": str(installation.id),
        "registry_id": entity.ha_registry_id,
        "entity_id": entity.ha_entity_id,
        "name": entity.display_name or entity.friendly_name or entity.ha_entity_id,
        "room_id": entity.ha_registry_id,
        "room_name": entity.display_name or entity.friendly_name or entity.ha_entity_id,
        "experiences": _experiences(entity),
        "device_class": entity.device_class,
        "manufacturer": attrs.get("_manufacturer"),
        "model": attrs.get("_model"),
        "dnd": None,
        "state": entity.state,
        "availability": "available" if entity.available else "unavailable",
        "connection_status": "online" if installation.last_seen_at else "offline",
        "media": {
            "title": attrs.get("media_title"),
            "artist": attrs.get("media_artist"),
            "album": attrs.get("media_album_name"),
            "duration_seconds": attrs.get("media_duration"),
            "content_fingerprint": _fingerprint(entity),
        },
        "source": attrs.get("source"),
        "source_list": attrs.get("source_list")
        if isinstance(attrs.get("source_list"), list)
        else [],
        "volume_percent": round(float(volume) * 100)
        if isinstance(volume, (int, float)) and not isinstance(volume, bool)
        else None,
        "muted": attrs.get("is_volume_muted")
        if isinstance(attrs.get("is_volume_muted"), bool)
        else None,
        "capabilities": {
            "play": bool(features & 16384),
            "pause": bool(features & 1),
            "stop": bool(features & 4096),
            "next": bool(features & 32),
            "previous": bool(features & 16),
            "set_volume": bool(features & 4),
            "mute": bool(features & 8),
            "select_source": bool(features & 2048),
            "grouping": bool(features & 524288),
            "artwork": True,
            "tts": False,
            "do_not_disturb": False,
        },
        "supported_features_raw": features,
        "last_changed_at": entity.last_changed_at.isoformat().replace("+00:00", "Z")
        if entity.last_changed_at
        else None,
        "last_updated_at": attrs.get("_last_updated_at"),
        "observed_at": entity.last_seen_at.isoformat().replace("+00:00", "Z")
        if entity.last_seen_at
        else None,
        "resource_revision": _resource_revision(entity),
    }


def _experiences(entity: Entity) -> list[str]:
    values = (entity.attributes_json or {}).get("_experiences", [])
    if not isinstance(values, list):
        return []
    return [value for value in ("watch", "listen") if value in values]


def _media_rooms(values: list[Entity]) -> dict[str, list[dict[str, str]]]:
    rooms: dict[str, dict[str, str]] = {"watch": {}, "listen": {}}
    for entity in values:
        if not entity.ha_registry_id:
            continue
        experiences = _experiences(entity)
        room_name = entity.display_name or entity.friendly_name or entity.ha_entity_id
        if experiences:
            rooms["watch"][entity.ha_registry_id] = room_name
        if "listen" in experiences:
            rooms["listen"][entity.ha_registry_id] = room_name
    return {
        experience: [
            {"room_id": room_id, "room_name": name}
            for room_id, name in sorted(areas.items(), key=lambda item: item[1].casefold())
        ]
        for experience, areas in rooms.items()
    }


def _groups(values: list[Entity], installation_id: UUID) -> list[dict[str, Any]]:
    by_entity = {item.ha_entity_id: item for item in values}
    declarations: dict[tuple[str, ...], list[Entity]] = {}
    incomplete: set[tuple[str, ...]] = set()
    for player in values:
        raw = (player.attributes_json or {}).get("group_members")
        if not isinstance(raw, list) or not 2 <= len(raw) <= 64:
            continue
        members, missing = [], False
        for entity_id in dict.fromkeys(raw):
            member = by_entity.get(entity_id) if isinstance(entity_id, str) else None
            if member is None or member.ha_registry_id is None:
                missing = True
            else:
                members.append(member.ha_registry_id)
        key = tuple(sorted(set(members)))
        if len(key) < 2:
            continue
        declarations.setdefault(key, []).append(player)
        if missing:
            incomplete.add(key)
    groups = []
    for member_ids, declarers in declarations.items():
        canonical = "v1\n" + str(installation_id) + "\n" + "\n".join(member_ids)
        group_id = "grp_v1_" + base64.urlsafe_b64encode(
            hashlib.sha256(canonical.encode()).digest()
        ).decode().rstrip("=")
        declared = {item.ha_registry_id for item in declarers}
        completeness = (
            "incomplete"
            if member_ids in incomplete
            else ("complete" if declared == set(member_ids) else "inconsistent")
        )
        groups.append(
            {
                "installation_id": str(installation_id),
                "group_id": group_id,
                "member_registry_ids": list(member_ids),
                "coordinator_registry_id": None,
                "display_name": "Gruppo multimediale",
                "completeness": completeness,
                "resource_revision": max(_resource_revision(item) for item in declarers),
                "observed_at": _now(),
            }
        )
    return groups


def _arguments(operation: str, values: dict[str, object]) -> dict[str, object]:
    if operation in {"set_volume", "set_group_volume"}:
        if (
            set(values) != {"volume_percent"}
            or type(values["volume_percent"]) is not int
            or not 0 <= int(values["volume_percent"]) <= 100
        ):
            raise ValueError
    elif operation == "select_source":
        if (
            set(values) != {"source"}
            or not isinstance(values["source"], str)
            or not 1 <= len(values["source"]) <= 255
        ):
            raise ValueError
    elif operation == "media_join":
        members = values.get("member_registry_ids")
        if (
            set(values) != {"member_registry_ids"}
            or not isinstance(members, list)
            or not 1 <= len(members) <= 63
            or len(set(members)) != len(members)
            or not all(isinstance(item, str) and item for item in members)
        ):
            raise ValueError
    elif operation == "tts":
        text = values.get("text")
        if (
            set(values) != {"text"}
            or not isinstance(text, str)
            or not 1 <= len(text.strip()) <= 500
            or "<" in text
            or ">" in text
        ):
            raise ValueError
    elif operation == "set_dnd":
        if set(values) != {"enabled"} or type(values.get("enabled")) is not bool:
            raise ValueError
    elif values:
        raise ValueError
    return values


def _echo_siblings(
    player: Entity, entities: list[Entity]
) -> tuple[Entity | None, Entity | None, bool]:
    attrs = player.attributes_json or {}
    platform = str(attrs.get("_platform") or "").lower()
    manufacturer = str(attrs.get("_manufacturer") or "").lower()
    related = [item for item in entities if player.device_id and item.device_id == player.device_id]
    speech = next(
        (
            item
            for item in related
            if item.ha_domain == "notify"
            and item.ha_entity_id.endswith(("_speak", "_parla"))
        ),
        None,
    )
    dnd = next(
        (
            item
            for item in related
            if item.ha_domain == "switch"
            and item.ha_entity_id.endswith(("_do_not_disturb", "_non_disturbare"))
        ),
        None,
    )
    is_echo = manufacturer == "amazon" or platform in {"alexa_media", "alexa_devices"}
    return speech, dnd, is_echo


def _enrich_echo_players(players: list[dict[str, Any]], values: list[Entity]) -> None:
    by_registry = {item.ha_registry_id: item for item in values}
    for payload in players:
        entity = by_registry.get(payload["registry_id"])
        if entity is None:
            continue
        speech, dnd, is_echo = _echo_siblings(entity, values)
        if not is_echo:
            continue
        payload["device_class"] = "echo"
        payload["manufacturer"] = (entity.attributes_json or {}).get("_manufacturer") or "Amazon"
        payload["model"] = (entity.attributes_json or {}).get("_model")
        payload["capabilities"]["tts"] = speech is not None and speech.available
        payload["capabilities"]["do_not_disturb"] = dnd is not None and dnd.available
        payload["dnd"] = dnd.state == "on" if dnd is not None else None


def _public_status(value: str, response: dict[str, object] | None) -> str:
    if value == "success":
        return "success"
    if value == "timeout":
        return "timeout"
    if value == "unavailable":
        return "offline"
    if value == "unsupported_command":
        return "unsupported"
    if response and isinstance(response.get("members"), list):
        return "partial_failure"
    return "rejected"


def _request_fingerprint(payload: BaseModel) -> str:
    value = payload.model_dump(mode="json", exclude={"request_id"})
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _existing_result(
    session: AsyncSession,
    context: MediaAccessContext,
    installation_id: UUID,
    request_id: UUID,
    fingerprint: str,
) -> dict[str, Any] | None:
    record = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.tenant_id == context.tenant_id,
            AuditEvent.installation_id == installation_id,
            AuditEvent.event_type == "media.command_result",
            AuditEvent.request_id == str(request_id),
        )
    )
    if record is None:
        return None
    stored = dict(record.payload_redacted_json or {})
    if stored.pop("_request_fingerprint", None) != fingerprint:
        raise HTTPException(status.HTTP_409_CONFLICT, "IDEMPOTENCY_KEY_REUSED")
    return stored


async def _persist_result(
    session: AsyncSession,
    context: MediaAccessContext,
    installation_id: UUID,
    result: dict[str, Any],
) -> None:
    existing = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.tenant_id == context.tenant_id,
            AuditEvent.installation_id == installation_id,
            AuditEvent.event_type == "media.command_result",
            AuditEvent.request_id == result["request_id"],
        )
    )
    if existing is None:
        session.add(
            AuditEvent(
                tenant_id=context.tenant_id,
                installation_id=installation_id,
                user_id=context.user_id,
                source="ekonex_media",
                event_type="media.command_result",
                request_id=result["request_id"],
                payload_redacted_json=result,
                result=result["status"],
            )
        )
        await session.commit()


@router.get("/installations/{installation_id}/snapshot")
async def snapshot(
    installation_id: UUID,
    context: Annotated[MediaAccessContext, media_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> dict[str, Any]:
    installation = await _installation(session, context, installation_id)
    values = await _entities(session, installation_id)
    groups = _groups(values, installation_id)
    group_by_member = {member: group for group in groups for member in group["member_registry_ids"]}
    serialized_players = [_player(item, installation) for item in values]
    all_entities = list(
        (
            await session.scalars(
                select(Entity).where(
                    Entity.installation_id == installation_id,
                    Entity.deleted_at.is_(None),
                )
            )
        ).all()
    )
    _enrich_echo_players(serialized_players, all_entities)
    for item in serialized_players:
        item["group"] = group_by_member.get(item["registry_id"])
    return {
        "installation_id": str(installation_id),
        "installation_revision": media_events.current_revision(
            installation_id, int(installation.sync_revision)
        ),
        "observed_at": _now(),
        "connection_status": "online" if installation.last_seen_at else "offline",
        "connector_capabilities": installation.connector_capabilities_json or {},
        "players": serialized_players,
        "groups": groups,
        "media_rooms": _media_rooms(values),
    }


@router.get("/installations/{installation_id}/players")
async def list_players(
    installation_id: UUID,
    context: Annotated[MediaAccessContext, media_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> dict[str, Any]:
    data = await snapshot(installation_id, context, session)
    return {
        key: data[key]
        for key in ("installation_id", "installation_revision", "players", "media_rooms")
    }


@router.get("/installations/{installation_id}/groups")
async def list_groups(
    installation_id: UUID,
    context: Annotated[MediaAccessContext, media_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> dict[str, Any]:
    data = await snapshot(installation_id, context, session)
    return {key: data[key] for key in ("installation_id", "installation_revision", "groups")}


@router.post("/installations/{installation_id}/players/{registry_id}/commands")
async def player_command(
    installation_id: UUID,
    registry_id: str,
    payload: PlayerCommandRequest,
    context: Annotated[MediaAccessContext, media_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> dict[str, Any]:
    installation = await _installation(session, context, installation_id)
    fingerprint = _request_fingerprint(payload)
    if existing := await _existing_result(
        session, context, installation_id, payload.request_id, fingerprint
    ):
        return existing
    entity = await session.scalar(
        select(Entity).where(
            Entity.installation_id == installation_id,
            Entity.ha_registry_id == registry_id,
            Entity.ha_domain == "media_player",
            Entity.deleted_at.is_(None),
        )
    )
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Player non trovato")
    sensitive = payload.operation in {"media_join", "media_unjoin"}
    if sensitive and payload.expected_resource_revision is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Revisione obbligatoria")
    if (
        payload.expected_resource_revision is not None
        and payload.expected_resource_revision != _resource_revision(entity)
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "REVISION_CONFLICT")
    try:
        arguments = _arguments(payload.operation, payload.arguments)
        if payload.operation == "media_join":
            member_ids = cast(list[str], arguments["member_registry_ids"])
            if registry_id in member_ids:
                raise ValueError
            found = list(
                (
                    await session.scalars(
                        select(Entity).where(
                            Entity.installation_id == installation_id,
                            Entity.ha_registry_id.in_(member_ids),
                            Entity.ha_domain == "media_player",
                            Entity.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
            if len(found) != len(member_ids) or any(not item.available for item in found):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Membro non valido")
        dispatch_registry_id = registry_id
        dispatch_operation = payload.operation
        dispatch_arguments = arguments
        if payload.operation in {"tts", "set_dnd"}:
            related = list(
                (
                    await session.scalars(
                        select(Entity).where(
                            Entity.installation_id == installation_id,
                            Entity.deleted_at.is_(None),
                            Entity.device_id == entity.device_id,
                        )
                    )
                ).all()
            )
            speech, dnd, is_echo = _echo_siblings(entity, related)
            target = speech if payload.operation == "tts" else dnd
            if not is_echo or target is None or not target.available or not target.ha_registry_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Funzione Echo non supportata o entità associata non esposta",
                )
            dispatch_registry_id = target.ha_registry_id
            if payload.operation == "tts":
                dispatch_operation = "speak"
                dispatch_arguments = {"message": str(arguments["text"]).strip()}
            else:
                dispatch_operation = "power_on" if arguments["enabled"] else "power_off"
                dispatch_arguments = {}
        command = command_adapter.validate_python(
            {"operation": dispatch_operation, **dispatch_arguments}
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Argomenti non validi") from error
    outcome = await CommandDispatchService(session, sessions).dispatch(
        installation_id, dispatch_registry_id, command, command_id=payload.request_id
    )
    result = {
        "request_id": str(payload.request_id),
        "kind": "player_command_result",
        "status": _public_status(outcome.status, outcome.response_data),
        "operation": payload.operation,
        "installation_id": str(installation_id),
        "registry_id": registry_id,
        "accepted_at": _now(),
        "completed_at": _now(),
        "resulting_resource_revision": _resource_revision(entity),
        "response": outcome.response_data,
        "error": {"code": outcome.error_code} if outcome.error_code else None,
        "_request_fingerprint": fingerprint,
    }
    await _persist_result(session, context, installation_id, result)
    result.pop("_request_fingerprint", None)
    media_events.publish(
        installation_id,
        int(installation.sync_revision),
        "command.completed",
        {"request_id": str(payload.request_id), "result": result},
    )
    return result


@router.post("/installations/{installation_id}/groups/{group_id}/commands")
async def group_command(
    installation_id: UUID,
    group_id: str,
    payload: GroupCommandRequest,
    context: Annotated[MediaAccessContext, media_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> dict[str, Any]:
    installation = await _installation(session, context, installation_id)
    fingerprint = _request_fingerprint(payload)
    if existing := await _existing_result(
        session, context, installation_id, payload.request_id, fingerprint
    ):
        return existing
    values = await _entities(session, installation_id)
    group = next(
        (item for item in _groups(values, installation_id) if item["group_id"] == group_id), None
    )
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gruppo non trovato")
    if group["completeness"] != "complete":
        raise HTTPException(status.HTTP_409_CONFLICT, "GROUP_INCONSISTENT")
    if (
        payload.expected_resource_revision is None
        or payload.expected_resource_revision != group["resource_revision"]
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "REVISION_CONFLICT")
    try:
        command = command_adapter.validate_python(
            {"operation": payload.operation, **_arguments(payload.operation, payload.arguments)}
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Argomenti non validi") from error
    target = str(group["member_registry_ids"][0])
    outcome = await CommandDispatchService(session, sessions).dispatch(
        installation_id, target, command, command_id=payload.request_id
    )
    response = outcome.response_data or {}
    result = {
        "request_id": str(payload.request_id),
        "kind": "group_command_result",
        "status": _public_status(outcome.status, response),
        "operation": payload.operation,
        "installation_id": str(installation_id),
        "group_id": group_id,
        "accepted_at": _now(),
        "completed_at": _now(),
        "resulting_resource_revision": group["resource_revision"],
        "requested_percent": payload.arguments.get("volume_percent"),
        "members": response.get("members", []),
        "error": {"code": outcome.error_code} if outcome.error_code else None,
        "_request_fingerprint": fingerprint,
    }
    await _persist_result(session, context, installation_id, result)
    result.pop("_request_fingerprint", None)
    media_events.publish(
        installation_id,
        int(installation.sync_revision),
        "command.completed",
        {"request_id": str(payload.request_id), "result": result},
    )
    return result


@router.get("/installations/{installation_id}/commands/{request_id}")
async def get_command_result(
    installation_id: UUID,
    request_id: UUID,
    context: Annotated[MediaAccessContext, media_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> dict[str, Any]:
    await _installation(session, context, installation_id)
    record = await session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.tenant_id == context.tenant_id,
            AuditEvent.installation_id == installation_id,
            AuditEvent.event_type == "media.command_result",
            AuditEvent.request_id == str(request_id),
        )
        .order_by(AuditEvent.created_at.desc())
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Risultato non trovato")
    result = dict(record.payload_redacted_json or {})
    result.pop("_request_fingerprint", None)
    return result


def _valid_image(mime: str, content: bytes) -> bool:
    mime = mime.lower().split(";", 1)[0].strip()
    signatures = {
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/gif": (b"GIF87a", b"GIF89a"),
        "image/webp": (b"RIFF",),
    }
    return (
        mime in signatures
        and any(content.startswith(item) for item in signatures[mime])
        and (mime != "image/webp" or len(content) >= 12 and content[8:12] == b"WEBP")
    )


@router.get("/installations/{installation_id}/players/{registry_id}/artwork")
async def artwork(
    installation_id: UUID,
    registry_id: str,
    fingerprint: str,
    context: Annotated[MediaAccessContext, media_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    await _installation(session, context, installation_id)
    entity = await session.scalar(
        select(Entity).where(
            Entity.installation_id == installation_id,
            Entity.ha_registry_id == registry_id,
            Entity.ha_domain == "media_player",
            Entity.deleted_at.is_(None),
        )
    )
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if fingerprint != _fingerprint(entity):
        raise HTTPException(status.HTTP_409_CONFLICT, "ARTWORK_STALE")
    outcome = await CommandDispatchService(session, sessions).dispatch(
        installation_id,
        registry_id,
        command_adapter.validate_python({"operation": "media_artwork"}),
    )
    data = outcome.response_data or {}
    if outcome.error_code == "MEDIA_ARTWORK_NOT_FOUND":
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if outcome.error_code == "MEDIA_ARTWORK_TOO_LARGE":
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    if outcome.status == "timeout":
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT)
    encoded, mime = data.get("image_base64"), data.get("content_type")
    if not isinstance(encoded, str) or not isinstance(mime, str):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY)
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE) from error
    if len(content) > 700_000:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    if not _valid_image(mime, content):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    await session.refresh(entity)
    if fingerprint != _fingerprint(entity):
        raise HTTPException(status.HTTP_409_CONFLICT, "ARTWORK_STALE")
    etag = '"' + hashlib.sha256(fingerprint.encode() + content).hexdigest() + '"'
    if if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    return Response(
        content, media_type=mime, headers={"ETag": etag, "Cache-Control": "private, max-age=300"}
    )


@router.get("/installations/{installation_id}/favorites")
async def favorites(
    installation_id: UUID,
    context: Annotated[MediaAccessContext, media_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> dict[str, Any]:
    installation = await _installation(session, context, installation_id)
    source = installation.control4_favorites_json or {}
    raw_values = source.get("favorites")
    values: list[Any] = raw_values if isinstance(raw_values, list) else []
    return {
        "installation_id": str(installation_id),
        "favorites": [
            {
                "favorite_id": item.get("id"),
                "name": item.get("name"),
                "available": bool(item.get("command")),
                "default_target": None,
            }
            for item in values
            if isinstance(item, dict)
        ],
    }


@router.post("/installations/{installation_id}/favorites/{favorite_id}/play")
async def play_favorite(
    installation_id: UUID,
    favorite_id: UUID,
    payload: FavoritePlayRequest,
    context: Annotated[MediaAccessContext, media_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
) -> dict[str, Any]:
    installation = await _installation(session, context, installation_id)
    fingerprint = _request_fingerprint(payload)
    if existing := await _existing_result(
        session, context, installation_id, payload.request_id, fingerprint
    ):
        return existing
    config = installation.control4_favorites_json or {}
    raw_values = config.get("favorites")
    values: list[Any] = raw_values if isinstance(raw_values, list) else []
    item = next(
        (
            value
            for value in values
            if isinstance(value, dict) and value.get("id") == str(favorite_id)
        ),
        None,
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preferito non trovato")
    try:
        address = ipaddress.ip_address(str(config.get("host")))
    except ValueError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Configurazione non valida"
        ) from error
    port = config.get("port")
    if (
        not address.is_private
        or address.is_loopback
        or type(port) is not int
        or not 1 <= port <= 65535
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Configurazione non valida")
    command = command_adapter.validate_python(
        {
            "operation": "control4_favorite",
            "host": str(config["host"]),
            "port": port,
            "command": item.get("command"),
        }
    )
    outcome = await sessions.dispatch(
        installation_id,
        payload.request_id,
        "local:control4",
        command.model_dump(mode="json"),
        8,
    )
    result = {
        "request_id": str(payload.request_id),
        "kind": "player_command_result",
        "status": _public_status(outcome.status, outcome.response_data),
        "operation": "favorite_play",
        "installation_id": str(installation_id),
        "registry_id": None,
        "accepted_at": _now(),
        "completed_at": _now(),
        "resulting_resource_revision": None,
        "response": None,
        "error": {"code": outcome.error_code} if outcome.error_code else None,
        "_request_fingerprint": fingerprint,
    }
    await _persist_result(session, context, installation_id, result)
    result.pop("_request_fingerprint", None)
    return result


@router.get("/installations/{installation_id}/events")
async def events(
    installation_id: UUID,
    context: Annotated[MediaAccessContext, media_context_dependency],
    session: Annotated[AsyncSession, session_dependency],
    after_revision: Annotated[int, Header(alias="X-Media-After-Revision")] = 0,
) -> StreamingResponse:
    installation = await _installation(session, context, installation_id)
    recovery, queue = (
        media_events.events_after(installation_id, after_revision),
        media_events.subscribe(installation_id),
    )

    async def stream() -> AsyncIterator[str]:
        try:
            if recovery is None:
                yield _sse(
                    media_events.publish(
                        installation_id,
                        int(installation.sync_revision),
                        "snapshot.required",
                        {
                            "reason": "recovery_window_expired",
                            "current_installation_revision": int(installation.sync_revision),
                        },
                    )
                )
            else:
                for item in recovery:
                    yield _sse(item)
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), 15)
                except TimeoutError:
                    item = {
                        "event_id": str(UUID(int=0)),
                        "installation_id": str(installation_id),
                        "installation_revision": media_events.current_revision(
                            installation_id, int(installation.sync_revision)
                        ),
                        "type": "heartbeat",
                        "occurred_at": _now(),
                        "observed_at": _now(),
                        "data": {"connection_status": "online", "server_time": _now()},
                    }
                yield _sse(item)
        finally:
            media_events.unsubscribe(installation_id, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _sse(value: dict[str, Any]) -> str:
    payload = json.dumps(value, separators=(",", ":"))
    return f"id: {value['event_id']}\nevent: {value['type']}\ndata: {payload}\n\n"
