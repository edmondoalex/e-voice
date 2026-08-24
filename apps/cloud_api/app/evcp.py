"""EVCP v1 WebSocket transport implemented by milestone M4."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect, WebSocketState

from .database import get_database_session
from .domain.models import AuditEvent, ConnectorCredential, Installation, OperationalEvent
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
    icon: str | None = Field(default=None, max_length=255)
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
    correlation_id: UUID | None = None
    diagnostics: list[dict[str, object]] = Field(default_factory=list, max_length=16)


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


@dataclass(slots=True)
class SessionHandle:
    session_id: UUID
    websocket: WebSocket
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class PendingCommand:
    """A command bound to the exact session on which it was sent."""

    future: asyncio.Future[CommandResultPayload]
    session_id: UUID
    diagnostic: dict[str, object]
    sent: bool = False


class ConnectorSessionRegistry:
    """Own the current process-local session for each installation."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, SessionHandle] = {}
        self._pending: dict[tuple[UUID, UUID], PendingCommand] = {}
        self._completed: dict[tuple[UUID, UUID], CommandResultPayload] = {}
        self._command_fingerprints: dict[tuple[UUID, UUID], str] = {}
        self._lock = asyncio.Lock()
        self._replacement_locks: dict[UUID, asyncio.Lock] = {}
        self._transitioning: set[UUID] = set()

    async def replace(self, installation_id: UUID, handle: SessionHandle) -> dict[str, object]:
        replacement_lock = self._replacement_locks.setdefault(installation_id, asyncio.Lock())
        async with replacement_lock:
            async with self._lock:
                previous = self._sessions.get(installation_id)
                if previous is None or previous.session_id == handle.session_id:
                    self._sessions[installation_id] = handle
                    diagnostic = _session_diagnostic(
                        installation_id,
                        handle,
                        "new",
                        event_type="evcp.session_registered",
                    )
                    diagnostic["new_session_id"] = str(handle.session_id)
                    diagnostic["previous_session_id"] = (
                        str(previous.session_id) if previous else None
                    )
                    return diagnostic
                self._transitioning.add(installation_id)
                draining = [
                    pending
                    for key, pending in self._pending.items()
                    if key[0] == installation_id
                    and pending.session_id == previous.session_id
                    and pending.sent
                    and not pending.future.done()
                ]
            try:
                if draining:
                    await asyncio.gather(
                        *(asyncio.shield(pending.future) for pending in draining),
                        return_exceptions=True,
                    )
            except asyncio.CancelledError:
                async with self._lock:
                    self._transitioning.discard(installation_id)
                raise
            async with self._lock:
                if self._sessions.get(installation_id) is previous:
                    self._sessions.pop(installation_id, None)
                self._sessions[installation_id] = handle
                self._transitioning.discard(installation_id)
            await _safe_close(previous.websocket, 4008, "SESSION_REPLACED")
            diagnostic = _session_diagnostic(
                installation_id,
                handle,
                "replaced",
                event_type="evcp.session_registered",
            )
            diagnostic["new_session_id"] = str(handle.session_id)
            diagnostic["previous_session_id"] = str(previous.session_id)
            return diagnostic

    async def touch(self, installation_id: UUID, session_id: UUID) -> bool:
        """Refresh liveness only for the currently owned session."""
        async with self._lock:
            current = self._sessions.get(installation_id)
            if current is None or current.session_id != session_id:
                return False
            current.last_seen = datetime.now(UTC)
            return True

    async def remove(self, installation_id: UUID, session_id: UUID) -> dict[str, object]:
        async with self._lock:
            current = self._sessions.get(installation_id)
            removed = current is not None and current.session_id == session_id
            reason = (
                "current_session_removed"
                if removed
                else "no_current_session"
                if current is None
                else "requested_session_not_current"
            )
            diagnostic = _session_diagnostic(
                installation_id,
                current,
                reason,
                event_type="evcp.session_removed",
            )
            diagnostic["requested_session_id"] = str(session_id)
            diagnostic["removed"] = removed
            if removed:
                self._sessions.pop(installation_id, None)
                for key, pending in list(self._pending.items()):
                    if (
                        key[0] == installation_id
                        and pending.session_id == session_id
                        and not pending.future.done()
                    ):
                        pending.future.set_result(
                            CommandResultPayload(
                                session_id=session_id,
                                command_id=key[1],
                                status="stale_session",
                                error_code="STALE_SESSION",
                                diagnostics=[
                                    _session_diagnostic(
                                        installation_id,
                                        current,
                                        "session_removed_before_result",
                                    )
                                ],
                            )
                        )
            return diagnostic

    async def dispatch(
        self,
        installation_id: UUID,
        command_id: UUID,
        registry_id: str,
        command: dict[str, object],
        timeout_seconds: float,
        correlation_id: UUID | None = None,
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
                    diagnostics=[
                        _session_diagnostic(
                            installation_id,
                            None,
                            "no_registered_session",
                            event_type="evcp.dispatch_session_selected",
                            command_id=command_id,
                            pending_key=key,
                        )
                    ],
                )
            diagnostic = _session_diagnostic(
                installation_id,
                handle,
                "session_transitioning"
                if installation_id in self._transitioning
                else _stale_reason(handle),
                event_type="evcp.dispatch_session_selected",
                command_id=command_id,
                pending_key=key,
                payload_session_id=handle.session_id,
            )
            stale_reason = str(diagnostic["reason"])
            if stale_reason != "active_session_ready":
                return CommandResultPayload(
                    session_id=handle.session_id,
                    command_id=command_id,
                    status="stale_session",
                    error_code="STALE_SESSION",
                    correlation_id=correlation_id,
                    diagnostics=[diagnostic],
                )
            pending = self._pending.get(key)
            if pending is None:
                future = asyncio.get_running_loop().create_future()
                pending = PendingCommand(
                    future=future,
                    session_id=handle.session_id,
                    diagnostic=diagnostic,
                )
                self._pending[key] = pending
                self._command_fingerprints[key] = fingerprint
                should_send = True
            else:
                future = pending.future
                should_send = False
            if should_send:
                try:
                    await handle.websocket.send_json(
                        _response(
                            "command",
                            command_id,
                            {
                                "session_id": str(handle.session_id),
                                "command_id": str(command_id),
                                "registry_id": registry_id,
                                "correlation_id": str(correlation_id) if correlation_id else None,
                                "command": command,
                            },
                        )
                    )
                    pending.sent = True
                except Exception:
                    self._pending.pop(key, None)
                    self._command_fingerprints.pop(key, None)
                    failed = _session_diagnostic(
                        installation_id,
                        handle,
                        "websocket_send_failed",
                        event_type="evcp.dispatch_session_selected",
                        command_id=command_id,
                        pending_key=key,
                        payload_session_id=handle.session_id,
                    )
                    return CommandResultPayload(
                        session_id=handle.session_id,
                        command_id=command_id,
                        status="stale_session",
                        error_code="STALE_SESSION",
                        correlation_id=correlation_id,
                        diagnostics=[failed],
                    )
        result: CommandResultPayload
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await asyncio.shield(future)
        except TimeoutError:
            result = CommandResultPayload(
                session_id=handle.session_id,
                command_id=command_id,
                status="timeout",
                error_code="COMMAND_TIMEOUT",
                correlation_id=correlation_id,
                diagnostics=[pending.diagnostic],
            )
            if not future.done():
                future.set_result(result)
        async with self._lock:
            if self._pending.get(key) is pending:
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
        matched, _ = await self.resolve_with_diagnostic(installation_id, session_id, result)
        return matched

    async def resolve_with_diagnostic(
        self, installation_id: UUID, session_id: UUID, result: CommandResultPayload
    ) -> tuple[bool, dict[str, object]]:
        """Resolve a result and return its allowlisted session ownership decision."""
        async with self._lock:
            current = self._sessions.get(installation_id)
            pending = self._pending.get((installation_id, result.command_id))
            reason = (
                "no_current_session"
                if current is None
                else "websocket_session_not_current"
                if current.session_id != session_id
                else "result_session_mismatch"
                if result.session_id != session_id
                else "already_completed"
                if pending is None and (installation_id, result.command_id) in self._completed
                else "no_pending_command"
                if pending is None
                else "pending_owned_by_other_session"
                if pending.session_id != session_id
                else "pending_already_done"
                if pending.future.done()
                else "matched"
            )
            diagnostic = _session_diagnostic(
                installation_id,
                current,
                reason,
                event_type="evcp.command_result_session_check",
                command_id=result.command_id,
                pending_key=(installation_id, result.command_id),
                payload_session_id=result.session_id,
            )
            diagnostic.update(
                {
                    "websocket_session_id": str(session_id),
                    "result_session_id": str(result.session_id),
                    "match": reason in {"matched", "already_completed"},
                }
            )
            if reason not in {"matched", "already_completed"}:
                return False, diagnostic
            if pending is None and (installation_id, result.command_id) in self._completed:
                return True, diagnostic
            assert pending is not None
            result.diagnostics = [pending.diagnostic, *result.diagnostics[:14], diagnostic]
            pending.future.set_result(result)
            return True, diagnostic


def _stale_reason(handle: SessionHandle) -> str:
    now = datetime.now(UTC)
    last_seen = handle.last_seen
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    if (now - last_seen).total_seconds() > LIVENESS_TIMEOUT_SECONDS:
        return "heartbeat_expired"
    client_state = getattr(handle.websocket, "client_state", None)
    application_state = getattr(handle.websocket, "application_state", None)
    if client_state != WebSocketState.CONNECTED:
        return "websocket_client_not_connected"
    if isinstance(application_state, WebSocketState) and (
        application_state != WebSocketState.CONNECTED
    ):
        return "websocket_application_not_connected"
    return "active_session_ready"


def _session_diagnostic(
    installation_id: UUID,
    handle: SessionHandle | None,
    reason: str,
    *,
    event_type: str = "evcp.session_decision",
    command_id: UUID | None = None,
    pending_key: tuple[UUID, UUID] | None = None,
    payload_session_id: UUID | None = None,
) -> dict[str, object]:
    websocket = handle.websocket if handle else None
    client = getattr(websocket, "client", None)
    diagnostic: dict[str, object] = {
        "event_type": event_type,
        "requested_installation_id": str(installation_id),
        "installation_id": str(installation_id),
        "command_id": str(command_id) if command_id else None,
        "active_session_id": str(handle.session_id) if handle else None,
        "registry_session_id": str(handle.session_id) if handle else None,
        "current_session_id": str(handle.session_id) if handle else None,
        "payload_session_id": str(payload_session_id) if payload_session_id else None,
        "pending_key": (f"{pending_key[0]}:{pending_key[1]}" if pending_key is not None else None),
        "connection_id": id(websocket) if websocket else None,
        "websocket_state": {
            "client": str(getattr(websocket, "client_state", None)),
            "application": str(getattr(websocket, "application_state", None)),
        }
        if websocket
        else None,
        "websocket_client": {
            "host": getattr(client, "host", None),
            "port": getattr(client, "port", None),
        }
        if client is not None
        else None,
        "connected_at": handle.connected_at.isoformat() if handle else None,
        "last_seen": handle.last_seen.isoformat() if handle else None,
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "stale_threshold_seconds": LIVENESS_TIMEOUT_SECONDS,
        "reason": reason,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    logger.info("%s %s", event_type.replace(".", "_"), diagnostic)
    return diagnostic


sessions = ConnectorSessionRegistry()


def _add_session_activity(
    database: AsyncSession,
    *,
    tenant_id: UUID,
    installation_id: UUID,
    diagnostic: dict[str, object],
    request_id: UUID | None = None,
    result: str = "observed",
) -> None:
    event_type = str(diagnostic.get("event_type", "evcp.session_diagnostic"))
    database.add(
        AuditEvent(
            tenant_id=tenant_id,
            installation_id=installation_id,
            source="connector" if event_type.startswith("connector.") else "evcp",
            event_type=event_type,
            request_id=str(request_id) if request_id else None,
            payload_redacted_json=diagnostic,
            result=result,
        )
    )


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
    session_handle: SessionHandle | None = None
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
        session_handle = SessionHandle(session_id=session_id, websocket=websocket)
        registration_diagnostic = await sessions.replace(installation_id, session_handle)
        registered = True
        _add_session_activity(
            database,
            tenant_id=installation.tenant_id,
            installation_id=installation_id,
            diagnostic=registration_diagnostic,
            result="registered",
        )
        database.add(
            OperationalEvent(
                tenant_id=installation.tenant_id,
                installation_id=installation_id,
                event_type="connector_session",
                source="connector",
                outcome="connected",
                metadata_json=registration_diagnostic,
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
            if not await sessions.touch(installation_id, session_id):
                await _safe_close(websocket, 4008, "SESSION_REPLACED")
                return
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
                matched, resolution_diagnostic = await sessions.resolve_with_diagnostic(
                    installation_id, session_id, message.payload
                )
                if not matched:
                    _add_session_activity(
                        database,
                        tenant_id=installation.tenant_id,
                        installation_id=installation_id,
                        diagnostic=resolution_diagnostic,
                        request_id=message.payload.command_id,
                        result="mismatch",
                    )
                    for connector_diagnostic in message.payload.diagnostics:
                        if connector_diagnostic.get("event_type") == (
                            "connector.command_session_check"
                        ):
                            _add_session_activity(
                                database,
                                tenant_id=installation.tenant_id,
                                installation_id=installation_id,
                                diagnostic=connector_diagnostic,
                                request_id=message.payload.command_id,
                                result="mismatch",
                            )
                    await database.commit()
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
            assert session_handle is not None
            inventory_batches.clear_session(installation_id, session_id)
            removal_diagnostic = await sessions.remove(installation_id, session_id)
            _add_session_activity(
                database,
                tenant_id=credential.installation.tenant_id,
                installation_id=installation_id,
                diagnostic=removal_diagnostic,
                result="removed" if removal_diagnostic["removed"] else "preserved",
            )
            database.add(
                OperationalEvent(
                    tenant_id=credential.installation.tenant_id,
                    installation_id=installation_id,
                    event_type="connector_session",
                    source="connector",
                    outcome="disconnected",
                    metadata_json=removal_diagnostic,
                )
            )
            await database.commit()
