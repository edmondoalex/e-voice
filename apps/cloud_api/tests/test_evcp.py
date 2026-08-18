"""M4 EVCP authentication, protocol and session ownership tests."""

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from apps.cloud_api.app.domain.enums import InstallationStatus
from apps.cloud_api.app.domain.models import ConnectorCredential, Installation
from apps.cloud_api.app.evcp import (
    ConnectorSessionRegistry,
    Heartbeat,
    Hello,
    SessionHandle,
    _authenticate_secret,
    inbound_adapter,
)


def test_evcp_accepts_only_the_closed_m4_vocabulary() -> None:
    installation_id = uuid4()
    message = inbound_adapter.validate_python(
        {
            "version": 1,
            "type": "hello",
            "id": uuid4(),
            "timestamp": datetime.now(UTC),
            "payload": {
                "installation_id": installation_id,
                "connector_version": "0.1.0",
                "ha_version": "2026.8.0",
                "protocol_versions": [1],
            },
        }
    )
    assert isinstance(message, Hello)
    with pytest.raises(ValidationError):
        inbound_adapter.validate_python(
            {
                "version": 1,
                "type": "entity_inventory",
                "id": uuid4(),
                "timestamp": datetime.now(UTC),
                "payload": {},
            }
        )


def test_heartbeat_requires_session_binding() -> None:
    heartbeat = Heartbeat.model_validate(
        {
            "version": 1,
            "type": "heartbeat",
            "id": uuid4(),
            "timestamp": datetime.now(UTC),
            "payload": {"session_id": uuid4()},
        }
    )
    assert heartbeat.version == 1


async def test_connector_auth_is_bound_and_revocable(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation_id = seeded_domain.installation_a_id  # type: ignore[attr-defined]
    installation = await session.get(Installation, installation_id)
    assert installation is not None
    installation.status = InstallationStatus.ACTIVE
    secret = "evc_test-credential-value"
    credential = ConnectorCredential(
        installation_id=installation_id,
        secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
    )
    session.add(credential)
    await session.commit()

    authenticated = await _authenticate_secret(f"Bearer {secret}", session)
    assert authenticated.installation_id == installation_id
    credential.revoked_at = datetime.now(UTC)
    await session.commit()
    with pytest.raises(HTTPException) as error:
        await _authenticate_secret(f"Bearer {secret}", session)
    assert error.value.status_code == 401


async def test_latest_authenticated_session_replaces_previous() -> None:
    registry = ConnectorSessionRegistry()
    installation_id = uuid4()
    first_socket = AsyncMock()
    first_socket.client_state = WebSocketState.CONNECTED
    second_socket = AsyncMock()
    second_socket.client_state = WebSocketState.CONNECTED
    first = SessionHandle(uuid4(), first_socket)
    second = SessionHandle(uuid4(), second_socket)
    await registry.replace(installation_id, first)
    await registry.replace(installation_id, second)
    first_socket.close.assert_awaited_once_with(code=4008, reason="SESSION_REPLACED")
    await registry.remove(installation_id, first.session_id)
    await registry.remove(installation_id, second.session_id)
