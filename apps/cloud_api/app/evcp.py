"""EVCP v1 WebSocket transport implemented by milestone M4."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect, WebSocketState

from .database import get_database_session
from .domain.models import ConnectorCredential, Installation, OperationalEvent
from .entity_sync import EntitySyncService, StaleSyncError
from .repositories import ConnectorCredentialRepository

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 65_536
HANDSHAKE_TIMEOUT_SECONDS = 10.0
HEARTBEAT_INTERVAL_SECONDS = 30
LIVENESS_TIMEOUT_SECONDS = 75.0

router = APIRouter()
logger = logging.getLogger(__name__)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HelloPayload(StrictModel):
    installation_id: UUID
    connector_version: str = Field(min_length=1, max_length=50)
    ha_version: str = Field(min_length=1, max_length=50)
    protocol_versions: list[Literal[1]] = Field(min_length=1, max_length=1)


class HeartbeatPayload(StrictModel):
    session_id: UUID


JsonScalar = str | int | float | bool | None


class EntityItem(StrictModel):
    registry_id: str = Field(min_length=1, max_length=64)
    entity_id: str = Field(min_length=3, max_length=255)
    domain: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    friendly_name: str | None = Field(default=None, max_length=255)
    area_id: str | None = Field(default=None, max_length=255)
    area_name: str | None = Field(default=None, max_length=255)
    device_id: str | None = Field(default=None, max_length=64)
    device_name: str | None = Field(default=None, max_length=255)
    device_class: str | None = Field(default=None, max_length=100)
    supported_features: int = Field(default=0, ge=0)
    state: str | None = Field(default=None, max_length=255)
    available: bool
    attributes: dict[str, JsonScalar | list[JsonScalar]] = Field(default_factory=dict)
    last_changed_at: datetime | None = None
    removed: bool = False

    @model_validator(mode="after")
    def bounded_attributes(self) -> EntityItem:
        if len(self.attributes) > 16:
            raise ValueError("too many attributes")
        for key, value in self.attributes.items():
            if len(key) > 64:
                raise ValueError("attribute name too long")
            values = value if isinstance(value, list) else [value]
            if len(values) > 8 or any(isinstance(item, str) and len(item) > 255 for item in values):
                raise ValueError("attribute value exceeds bounds")
        return self


class InventoryPayload(StrictModel):
    session_id: UUID
    revision: int = Field(ge=1)
    batch_index: int = Field(ge=0, lt=256)
    batch_count: int = Field(ge=1, le=256)
    entities: list[EntityItem] = Field(max_length=500)

    @model_validator(mode="after")
    def valid_batch_index(self) -> InventoryPayload:
        if self.batch_index >= self.batch_count:
            raise ValueError("batch_index must be below batch_count")
        return self


class Hello(StrictModel):
    version: Literal[1]
    type: Literal["hello"]
    id: UUID
    timestamp: datetime
    payload: HelloPayload


class Heartbeat(StrictModel):
    version: Literal[1]
    type: Literal["heartbeat"]
    id: UUID
    timestamp: datetime
    payload: HeartbeatPayload


class InventoryFull(StrictModel):
    version: Literal[1]
    type: Literal["inventory_full"]
    id: UUID
    timestamp: datetime
    payload: InventoryPayload


class InventoryDelta(StrictModel):
    version: Literal[1]
    type: Literal["inventory_delta"]
    id: UUID
    timestamp: datetime
    payload: InventoryPayload


class StateUpdate(StrictModel):
    version: Literal[1]
    type: Literal["state_update"]
    id: UUID
    timestamp: datetime
    payload: InventoryPayload


CommandStatus = Literal[
    "success",
    "target_not_found",
    "target_not_exposed",
    "unsupported_command",
    "invalid_argument",
    "unavailable",
    "timeout",
    "execution_failed",
    "stale_session",
    "duplicate",
]


class CommandResultPayload(StrictModel):
    session_id: UUID
    command_id: UUID
    status: CommandStatus
    error_code: str | None = Field(default=None, max_length=64, pattern=r"^[A-Z0-9_]+$")


class CommandResultMessage(StrictModel):
    version: Literal[1]
    type: Literal["command_result"]
    id: UUID
    timestamp: datetime
    payload: CommandResultPayload


InboundMessage = Annotated[
    Hello | Heartbeat | InventoryFull | InventoryDelta | StateUpdate | CommandResultMessage,
    Field(discriminator="type"),
]
inbound_adapter: TypeAdapter[InboundMessage] = TypeAdapter(InboundMessage)
database_dependency = Depends(get_database_session)


@dataclass(frozen=True, slots=True)
class SessionHandle:
    session_id: UUID
    websocket: WebSocket


class ConnectorSessionRegistry:
    """Own the current process-local session for each installation."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, SessionHandle] = {}
        self._pending: dict[tuple[UUID, UUID], asyncio.Future[CommandResultPayload]] = {}
        self._completed: dict[tuple[UUID, UUID], CommandResultPayload] = {}
        self._command_fingerprints: dict[tuple[UUID, UUID], str] = {}
        self._lock = asyncio.Lock()

    async def replace(self, installation_id: UUID, handle: SessionHandle) -> None:
        async with self._lock:
            previous = self._sessions.get(installation_id)
            self._sessions[installation_id] = handle
            if previous is not None and previous.session_id != handle.session_id:
                for key, future in self._pending.items():
                    if key[0] == installation_id and not future.done():
                        future.set_result(
                            CommandResultPayload(
                                session_id=previous.session_id,
                                command_id=key[1],
                                status="stale_session",
                                error_code="STALE_SESSION",
                            )
                        )
        if previous is not None and previous.session_id != handle.session_id:
            await _safe_close(previous.websocket, 4008, "SESSION_REPLACED")

    async def remove(self, installation_id: UUID, session_id: UUID) -> None:
        async with self._lock:
            current = self._sessions.get(installation_id)
            if current is not None and current.session_id == session_id:
                self._sessions.pop(installation_id, None)
                for key, future in list(self._pending.items()):
                    if key[0] == installation_id and not future.done():
                        future.set_result(
                            CommandResultPayload(
                                session_id=session_id,
                                command_id=key[1],
                                status="stale_session",
                                error_code="STALE_SESSION",
                            )
                        )

    async def dispatch(
        self,
        installation_id: UUID,
        command_id: UUID,
        registry_id: str,
        command: dict[str, object],
        timeout_seconds: float,
    ) -> CommandResultPayload:
        """Send to the active session and correlate one bounded result."""
        key = (installation_id, command_id)
        fingerprint = json.dumps(
            {"registry_id": registry_id, "command": command}, sort_keys=True, separators=(",", ":")
        )
        async with self._lock:
            previous_fingerprint = self._command_fingerprints.get(key)
            if previous_fingerprint is not None and previous_fingerprint != fingerprint:
                current = self._sessions.get(installation_id)
                return CommandResultPayload(
                    session_id=current.session_id if current else uuid4(),
                    command_id=command_id,
                    status="duplicate",
                    error_code="DUPLICATE_COMMAND",
                )
            if completed := self._completed.get(key):
                return completed
            handle = self._sessions.get(installation_id)
            if handle is None:
                return CommandResultPayload(
                    session_id=uuid4(),
                    command_id=command_id,
                    status="unavailable",
                    error_code="INSTALLATION_OFFLINE",
                )
            future = self._pending.get(key)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._pending[key] = future
                self._command_fingerprints[key] = fingerprint
                should_send = True
            else:
                should_send = False
        if should_send:
            await handle.websocket.send_json(
                _response(
                    "command",
                    command_id,
                    {
                        "session_id": str(handle.session_id),
                        "command_id": str(command_id),
                        "registry_id": registry_id,
                        "command": command,
                    },
                )
            )
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await asyncio.shield(future)
        except TimeoutError:
            result = CommandResultPayload(
                session_id=handle.session_id,
                command_id=command_id,
                status="timeout",
                error_code="COMMAND_TIMEOUT",
            )
        async with self._lock:
            self._pending.pop(key, None)
            self._completed[key] = result
            if len(self._completed) > 1024:
                oldest = next(iter(self._completed))
                self._completed.pop(oldest)
                self._command_fingerprints.pop(oldest, None)
        return result

    async def resolve(
        self, installation_id: UUID, session_id: UUID, result: CommandResultPayload
    ) -> bool:
        """Resolve only a waiter owned by the current authenticated session."""
        async with self._lock:
            current = self._sessions.get(installation_id)
            if (
                current is None
                or current.session_id != session_id
                or result.session_id != session_id
            ):
                return False
            future = self._pending.get((installation_id, result.command_id))
            if future is None and (installation_id, result.command_id) in self._completed:
                return True
            if future is None or future.done():
                return False
            future.set_result(result)
            return True


sessions = ConnectorSessionRegistry()


class InventoryAccumulator:
    """Bound in-flight full snapshots by authenticated session and revision."""

    def __init__(self) -> None:
        self._batches: dict[tuple[UUID, UUID, int], dict[int, list[EntityItem]]] = {}
        self._counts: dict[tuple[UUID, UUID, int], int] = {}

    def add(
        self, installation_id: UUID, payload: InventoryPayload
    ) -> list[dict[str, object]] | None:
        key = (installation_id, payload.session_id, payload.revision)
        for stale_key in [
            candidate
            for candidate in self._batches
            if candidate[:2] == key[:2] and candidate != key
        ]:
            self._batches.pop(stale_key, None)
            self._counts.pop(stale_key, None)
        batches = self._batches.setdefault(key, {})
        expected_count = self._counts.setdefault(key, payload.batch_count)
        if expected_count != payload.batch_count:
            raise ValueError("inconsistent batch count")
        previous = batches.get(payload.batch_index)
        if previous is not None and previous != payload.entities:
            raise ValueError("conflicting duplicate batch")
        if previous is None and payload.batch_index != len(batches):
            raise ValueError("out-of-order batch")
        batches.setdefault(payload.batch_index, payload.entities)
        if len(batches) != payload.batch_count:
            return None
        self._batches.pop(key, None)
        self._counts.pop(key, None)
        return [
            item.model_dump(mode="json")
            for index in range(payload.batch_count)
            for item in batches[index]
        ]

    def clear_session(self, installation_id: UUID, session_id: UUID) -> None:
        for key in [key for key in self._batches if key[:2] == (installation_id, session_id)]:
            self._batches.pop(key, None)
            self._counts.pop(key, None)


inventory_batches = InventoryAccumulator()


def _bearer_secret(websocket: WebSocket) -> str | None:
    scheme, _, value = websocket.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.startswith("evc_"):
        return None
    return value


async def _authenticate(websocket: WebSocket, session: AsyncSession) -> ConnectorCredential | None:
    secret = _bearer_secret(websocket)
    if secret is None:
        return None
    digest = hashlib.sha256(secret.encode()).hexdigest()
    credential = await ConnectorCredentialRepository(session).authenticate(secret_hash=digest)
    if credential is None or not hmac.compare_digest(credential.secret_hash, digest):
        return None
    credential.last_used_at = datetime.now(UTC)
    await session.commit()
    return credential


async def _authenticate_secret(secret: str | None, session: AsyncSession) -> ConnectorCredential:
    if secret is None or not secret.startswith("Bearer evc_"):
        raise HTTPException(status_code=401, detail="AUTH_FAILED")
    digest = hashlib.sha256(secret.removeprefix("Bearer ").encode()).hexdigest()
    credential = await ConnectorCredentialRepository(session).authenticate(secret_hash=digest)
    if credential is None or not hmac.compare_digest(credential.secret_hash, digest):
        raise HTTPException(status_code=401, detail="AUTH_FAILED")
    credential.last_used_at = datetime.now(UTC)
    await session.commit()
    return credential


@router.post("/connector/v1/auth/validate")
async def validate_connector_auth(
    authorization: str | None = Header(default=None),
    database: AsyncSession = database_dependency,
) -> dict[str, str]:
    """Validate the credential used by the HA ConfigEntry lifecycle."""
    credential = await _authenticate_secret(authorization, database)
    return {"installation_id": str(credential.installation_id)}


async def _receive(websocket: WebSocket, duration: float) -> InboundMessage:
    async with asyncio.timeout(duration):
        raw = await websocket.receive_text()
    if len(raw.encode()) > MAX_MESSAGE_BYTES:
        raise ValueError("MESSAGE_TOO_LARGE")
    return inbound_adapter.validate_json(raw)


def _response(message_type: str, message_id: UUID, payload: dict[str, object]) -> dict[str, object]:
    return {
        "version": PROTOCOL_VERSION,
        "type": message_type,
        "id": str(message_id),
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "payload": payload,
    }


async def _safe_close(websocket: WebSocket, code: int, reason: str) -> None:
    if websocket.client_state is WebSocketState.CONNECTED:
        try:
            await websocket.close(code=code, reason=reason)
        except RuntimeError:
            pass


async def _apply_entity_sync(
    database: AsyncSession,
    installation: Installation,
    installation_id: UUID,
    message: InventoryFull | InventoryDelta | StateUpdate,
) -> None:
    """Accumulate and persist one validated, session-bound EVCP sync message."""
    items = inventory_batches.add(installation_id, message.payload)
    logger.debug(
        "Entity synchronization batch received: type=%s revision=%d batch=%d/%d entities=%d",
        message.type,
        message.payload.revision,
        message.payload.batch_index + 1,
        message.payload.batch_count,
        len(message.payload.entities),
    )
    if items is None:
        return
    service = EntitySyncService(database, installation)
    if isinstance(message, InventoryFull):
        await service.apply_full(message.payload.revision, items)
    elif isinstance(message, StateUpdate):
        await service.apply_state(message.payload.revision, items)
    else:
        await service.apply_delta(message.payload.revision, items)
    logger.info(
        "Entity synchronization applied: type=%s revision=%d entities=%d",
        message.type,
        message.payload.revision,
        len(items),
    )


@router.websocket("/connector/v1/ws")
async def connector_websocket(
    websocket: WebSocket,
    database: AsyncSession = database_dependency,
) -> None:
    """Authenticate, bind and supervise one M4 EVCP session."""
    credential = await _authenticate(websocket, database)
    if credential is None:
        await websocket.close(code=4001, reason="AUTH_FAILED")
        return
    await websocket.accept()
    session_id = uuid4()
    installation_id = credential.installation_id
    registered = False
    try:
        hello = await _receive(websocket, HANDSHAKE_TIMEOUT_SECONDS)
        if not isinstance(hello, Hello) or hello.payload.installation_id != installation_id:
            await _safe_close(websocket, 4004, "INSTALLATION_MISMATCH")
            return
        installation = credential.installation
        installation.connector_version = hello.payload.connector_version
        installation.ha_version = hello.payload.ha_version
        installation.last_seen_at = datetime.now(UTC)
        await database.commit()
        await sessions.replace(
            installation_id, SessionHandle(session_id=session_id, websocket=websocket)
        )
        registered = True
        database.add(
            OperationalEvent(
                tenant_id=installation.tenant_id,
                installation_id=installation_id,
                event_type="connector_session",
                source="connector",
                outcome="connected",
                metadata_json={},
            )
        )
        await database.commit()
        await websocket.send_json(
            _response(
                "hello_ack",
                hello.id,
                {
                    "installation_id": str(installation_id),
                    "session_id": str(session_id),
                    "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
                    "sync_revision": installation.sync_revision,
                },
            )
        )
        logger.info("Connector EVCP session established; awaiting entity inventory")
        while True:
            message = await _receive(websocket, LIVENESS_TIMEOUT_SECONDS)
            if isinstance(message, Heartbeat):
                if message.payload.session_id != session_id:
                    await _safe_close(websocket, 4002, "INVALID_MESSAGE")
                    return
                installation.last_seen_at = datetime.now(UTC)
                await database.commit()
                await websocket.send_json(
                    _response("heartbeat_ack", message.id, {"session_id": str(session_id)})
                )
                continue
            if isinstance(message, CommandResultMessage):
                if not await sessions.resolve(installation_id, session_id, message.payload):
                    await _safe_close(websocket, 4002, "INVALID_MESSAGE")
                    return
                continue
            if (
                not isinstance(message, (InventoryFull, InventoryDelta, StateUpdate))
                or message.payload.session_id != session_id
            ):
                await _safe_close(websocket, 4002, "INVALID_MESSAGE")
                return
            try:
                await _apply_entity_sync(database, installation, installation_id, message)
            except StaleSyncError:
                logger.warning(
                    "Entity synchronization rejected: stale revision=%d", message.payload.revision
                )
                await _safe_close(websocket, 4009, "STALE_REVISION")
                return
    except TimeoutError:
        await _safe_close(websocket, 4005 if not registered else 4006, "TIMEOUT")
    except (ValidationError, ValueError):
        logger.warning("Connector EVCP message rejected: invalid schema or batch sequence")
        await _safe_close(websocket, 4002, "INVALID_MESSAGE")
    except WebSocketDisconnect:
        pass
    finally:
        if registered:
            inventory_batches.clear_session(installation_id, session_id)
            await sessions.remove(installation_id, session_id)
            database.add(
                OperationalEvent(
                    tenant_id=credential.installation.tenant_id,
                    installation_id=installation_id,
                    event_type="connector_session",
                    source="connector",
                    outcome="disconnected",
                    metadata_json={},
                )
            )
            await database.commit()
