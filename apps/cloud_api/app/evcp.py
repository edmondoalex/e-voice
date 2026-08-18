"""EVCP v1 WebSocket transport implemented by milestone M4."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect, WebSocketState

from .database import get_database_session
from .domain.models import ConnectorCredential
from .repositories import ConnectorCredentialRepository

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 65_536
HANDSHAKE_TIMEOUT_SECONDS = 10.0
HEARTBEAT_INTERVAL_SECONDS = 30
LIVENESS_TIMEOUT_SECONDS = 75.0

router = APIRouter()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HelloPayload(StrictModel):
    installation_id: UUID
    connector_version: str = Field(min_length=1, max_length=50)
    ha_version: str = Field(min_length=1, max_length=50)
    protocol_versions: list[Literal[1]] = Field(min_length=1, max_length=1)


class HeartbeatPayload(StrictModel):
    session_id: UUID


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


InboundMessage = Annotated[Hello | Heartbeat, Field(discriminator="type")]
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
        self._lock = asyncio.Lock()

    async def replace(self, installation_id: UUID, handle: SessionHandle) -> None:
        async with self._lock:
            previous = self._sessions.get(installation_id)
            self._sessions[installation_id] = handle
        if previous is not None and previous.session_id != handle.session_id:
            await _safe_close(previous.websocket, 4008, "SESSION_REPLACED")

    async def remove(self, installation_id: UUID, session_id: UUID) -> None:
        async with self._lock:
            current = self._sessions.get(installation_id)
            if current is not None and current.session_id == session_id:
                self._sessions.pop(installation_id, None)


sessions = ConnectorSessionRegistry()


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
        await websocket.send_json(
            _response(
                "hello_ack",
                hello.id,
                {
                    "installation_id": str(installation_id),
                    "session_id": str(session_id),
                    "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
                },
            )
        )
        while True:
            message = await _receive(websocket, LIVENESS_TIMEOUT_SECONDS)
            if not isinstance(message, Heartbeat) or message.payload.session_id != session_id:
                await _safe_close(websocket, 4002, "INVALID_MESSAGE")
                return
            installation.last_seen_at = datetime.now(UTC)
            await database.commit()
            await websocket.send_json(
                _response("heartbeat_ack", message.id, {"session_id": str(session_id)})
            )
    except TimeoutError:
        await _safe_close(websocket, 4005 if not registered else 4006, "TIMEOUT")
    except (ValidationError, ValueError):
        await _safe_close(websocket, 4002, "INVALID_MESSAGE")
    except WebSocketDisconnect:
        pass
    finally:
        if registered:
            await sessions.remove(installation_id, session_id)
