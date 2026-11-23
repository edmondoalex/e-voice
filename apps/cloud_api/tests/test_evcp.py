"""M4 EVCP authentication, protocol and session ownership tests."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from apps.cloud_api.app.domain.enums import InstallationStatus
from apps.cloud_api.app.domain.models import AuditEvent, ConnectorCredential, Installation
from apps.cloud_api.app.evcp import (
    CommandResultPayload,
    ConnectorSessionRegistry,
    EntityItem,
    Heartbeat,
    Hello,
    SessionHandle,
    _apply_entity_sync,
    _authenticate_secret,
    connector_websocket,
    inbound_adapter,
    sessions,
)

CONNECTOR_CAPABILITIES = {
    "supports_correlation_id": True,
    "supports_command_diagnostics": True,
    "supports_heartbeat_diagnostics": True,
}


class FakeConnectorWebSocket:
    """Queue-backed WebSocket exercising the real EVCP endpoint handler."""

    def __init__(self, secret: str) -> None:
        self.headers = {"authorization": f"Bearer {secret}"}
        self.client_state = WebSocketState.CONNECTING
        self.application_state = WebSocketState.CONNECTING
        self.inbound: asyncio.Queue[str | BaseException] = asyncio.Queue()
        self.outbound: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.close_code: int | None = None
        self.close_reason: str | None = None

    async def accept(self) -> None:
        self.client_state = WebSocketState.CONNECTED
        self.application_state = WebSocketState.CONNECTED

    async def receive_text(self) -> str:
        value = await self.inbound.get()
        if isinstance(value, BaseException):
            raise value
        return value

    async def send_json(self, message: dict[str, object]) -> None:
        await self.outbound.put(message)

    async def close(self, *, code: int, reason: str) -> None:
        self.close_code = code
        self.close_reason = reason
        self.application_state = WebSocketState.DISCONNECTED


def message(message_type: str, payload: dict[str, object]) -> str:
    return json.dumps(
        {
            "version": 1,
            "type": message_type,
            "id": str(uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
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
                "connector_version": "0.1.8-beta.5",
                "ha_version": "2026.8.0",
                "protocol_versions": [1],
                "capabilities": CONNECTOR_CAPABILITIES,
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


def test_entity_schema_accepts_only_unique_media_experiences() -> None:
    base = {
        "registry_id": "media-registry",
        "entity_id": "media_player.sala",
        "domain": "media_player",
        "available": True,
    }
    validated = EntityItem.model_validate({**base, "experiences": ["watch", "listen"]})
    assert validated.experiences == ["watch", "listen"]
    with pytest.raises(ValidationError):
        EntityItem.model_validate({**base, "experiences": ["other"]})
    with pytest.raises(ValidationError):
        EntityItem.model_validate({**base, "experiences": ["listen", "listen"]})


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


async def test_incompatible_connector_is_persisted_and_rejected_during_hello(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation_id = seeded_domain.installation_a_id  # type: ignore[attr-defined]
    installation = await session.get(Installation, installation_id)
    assert installation is not None
    installation.status = InstallationStatus.ACTIVE
    secret = "evc_incompatible-connector-secret"
    session.add(
        ConnectorCredential(
            installation_id=installation_id,
            secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
        )
    )
    await session.commit()
    websocket = FakeConnectorWebSocket(secret)
    await websocket.inbound.put(
        message(
            "hello",
            {
                "installation_id": str(installation_id),
                "connector_version": "0.1.7",
                "ha_version": "2026.8.0",
                "protocol_versions": [1],
            },
        )
    )

    await connector_websocket(websocket, session)  # type: ignore[arg-type]

    await session.refresh(installation)
    assert installation.connector_version == "0.1.7"
    assert installation.connector_compatibility_status == "INCOMPATIBLE"
    assert installation.connector_compatibility_reason == (
        "required_connector_capabilities_missing"
    )
    assert websocket.close_code == 4010
    assert websocket.close_reason == "CONNECTOR_VERSION_INCOMPATIBLE"
    assert installation_id not in sessions._sessions
    events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.installation_id == installation_id)
                .order_by(AuditEvent.created_at)
            )
        ).all()
    )
    assert [event.event_type for event in events[-2:]] == [
        "connector.version_detected",
        "connector.version_incompatible",
    ]


async def test_negotiated_selection_snapshot_stored_separately(session,seeded_domain,monkeypatch):
    from types import SimpleNamespace
    from apps.cloud_api.app.voice_selection_store import VoiceSelectionSnapshot
    from apps.cloud_api.app import evcp
    installation_id=seeded_domain.installation_a_id
    installation=await session.get(Installation,installation_id);installation.status=InstallationStatus.ACTIVE
    secret='evc_synthetic-selection-secret'
    session.add(ConnectorCredential(installation_id=installation_id,secret_hash=hashlib.sha256(secret.encode()).hexdigest()))
    await session.commit()
    monkeypatch.setattr(evcp,'get_settings',lambda:SimpleNamespace(voice_selection_v1_enabled=True))
    websocket=FakeConnectorWebSocket(secret)
    if installation_id in sessions._sessions:await sessions.remove(installation_id,sessions._sessions[installation_id].session_id)
    endpoint=asyncio.create_task(connector_websocket(websocket,session))
    try:
        await websocket.inbound.put(message('hello',{'installation_id':str(installation_id),'connector_version':'0.1.8-beta.5',
            'ha_version':'2026.8.0','protocol_versions':[1],'capabilities':{'supports_voice_selection_v1':True,
            'supports_alexa_announcements':True,'supports_alexa_volume':True,'supports_correlation_id':True,
            'supports_command_diagnostics':True,'supports_heartbeat_diagnostics':True}}))
        ack=await asyncio.wait_for(websocket.outbound.get(),2)
        assert ack['payload']['voice_selection_v1'] is True
        sid=ack['payload']['session_id']
        await websocket.inbound.put(message('voice_selection_v1',{'session_id':sid,'revision':1,
            'batch_index':0,'batch_count':2,'registry_ids':['with-state']}))
        await websocket.inbound.put(message('heartbeat',{'session_id':sid}))
        assert (await asyncio.wait_for(websocket.outbound.get(),2))['type']=='heartbeat_ack'
        assert await session.get(VoiceSelectionSnapshot,installation_id) is None
        await websocket.inbound.put(message('voice_selection_v1',{'session_id':sid,'revision':1,
            'batch_index':1,'batch_count':2,'registry_ids':['without-state']}))
        await websocket.inbound.put(message('heartbeat',{'session_id':sid}))
        heartbeat=await asyncio.wait_for(websocket.outbound.get(),2)
        assert heartbeat['type']=='heartbeat_ack'
        row=await session.get(VoiceSelectionSnapshot,installation_id)
        assert row.registry_ids==['with-state','without-state'] and row.tenant_id==seeded_domain.tenant_a_id
        assert (await session.get(Installation,installation_id)).sync_revision==0
    finally:
        endpoint.cancel()
        with pytest.raises(asyncio.CancelledError):await endpoint


async def test_failed_session_registration_commit_does_not_commit_fence(session,seeded_domain,monkeypatch,caplog):
    from types import SimpleNamespace
    from apps.cloud_api.app import evcp
    from apps.cloud_api.app.voice_selection_store import VoiceSelectionFence,register_selection_session
    installation_id=seeded_domain.installation_a_id;tenant_id=seeded_domain.tenant_a_id;old_session=uuid4()
    installation=await session.get(Installation,installation_id);installation.status=InstallationStatus.ACTIVE
    secret='evc_synthetic-commit-failure-secret'
    session.add(ConnectorCredential(installation_id=installation_id,secret_hash=hashlib.sha256(secret.encode()).hexdigest()))
    await register_selection_session(session,installation_id,tenant_id,old_session);await session.commit()
    original_commit=session.commit;count=0
    async def failing_commit():
        nonlocal count
        count+=1
        if count==3:raise RuntimeError('synthetic_commit_failure')
        await original_commit()
    monkeypatch.setattr(session,'commit',failing_commit)
    monkeypatch.setattr(evcp,'get_settings',lambda:SimpleNamespace(voice_selection_v1_enabled=True))
    websocket=FakeConnectorWebSocket(secret)
    if installation_id in sessions._sessions:await sessions.remove(installation_id,sessions._sessions[installation_id].session_id)
    await websocket.inbound.put(message('hello',{'installation_id':str(installation_id),'connector_version':'0.1.8-beta.5',
        'ha_version':'2026.8.0','protocol_versions':[1],'capabilities':{**CONNECTOR_CAPABILITIES,'supports_voice_selection_v1':True}}))
    await connector_websocket(websocket,session)
    assert count>=3 and 'synthetic_commit_failure' in caplog.text
    fence=await session.get(VoiceSelectionFence,installation_id,populate_existing=True)
    assert fence.session_id==old_session
    assert installation_id not in sessions._sessions
    assert websocket.outbound.empty() and websocket.close_code==1011


async def test_cancel_during_replacement_rolls_back_fence_and_removes_session(session,seeded_domain,monkeypatch):
    from types import SimpleNamespace
    from apps.cloud_api.app import evcp
    from apps.cloud_api.app.voice_selection_store import VoiceSelectionFence,register_selection_session
    installation_id=seeded_domain.installation_a_id;tenant_id=seeded_domain.tenant_a_id;old_session=uuid4()
    installation=await session.get(Installation,installation_id);installation.status=InstallationStatus.ACTIVE
    secret='evc_synthetic-cancel-secret'
    session.add(ConnectorCredential(installation_id=installation_id,secret_hash=hashlib.sha256(secret.encode()).hexdigest()))
    await register_selection_session(session,installation_id,tenant_id,old_session);await session.commit()
    monkeypatch.setattr(evcp,'get_settings',lambda:SimpleNamespace(voice_selection_v1_enabled=True))
    if installation_id in sessions._sessions:await sessions.remove(installation_id,sessions._sessions[installation_id].session_id)
    arrived=asyncio.Event();original=sessions.replace
    async def suspended(*args,**kwargs):
        diagnostic=await original(*args,**kwargs);arrived.set();await asyncio.Event().wait();return diagnostic
    monkeypatch.setattr(sessions,'replace',suspended)
    websocket=FakeConnectorWebSocket(secret)
    await websocket.inbound.put(message('hello',{'installation_id':str(installation_id),'connector_version':'0.1.8-beta.5',
        'ha_version':'2026.8.0','protocol_versions':[1],'capabilities':{**CONNECTOR_CAPABILITIES,'supports_voice_selection_v1':True}}))
    task=asyncio.create_task(connector_websocket(websocket,session));await asyncio.wait_for(arrived.wait(),2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    fence=await session.get(VoiceSelectionFence,installation_id,populate_existing=True)
    assert fence.session_id==old_session and installation_id not in sessions._sessions
    assert websocket.outbound.empty()


async def test_websocket_inventory_session_remains_routable_through_command_result(
    session: AsyncSession,
    seeded_domain: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    monkeypatch.setattr('apps.cloud_api.app.evcp.get_settings',lambda:SimpleNamespace(voice_selection_v1_enabled=True))
    installation_id = seeded_domain.installation_a_id  # type: ignore[attr-defined]
    secret = "evc_end-to-end-session-secret"
    installation = await session.get(Installation, installation_id)
    assert installation is not None
    installation.status = InstallationStatus.ACTIVE
    session.add(
        ConnectorCredential(
            installation_id=installation_id,
            secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
        )
    )
    await session.commit()
    websocket = FakeConnectorWebSocket(secret)
    inventory_applied = asyncio.Event()

    async def apply_inventory(*args: object, **kwargs: object) -> None:
        await _apply_entity_sync(*args, **kwargs)  # type: ignore[arg-type]
        inventory_applied.set()

    monkeypatch.setattr("apps.cloud_api.app.evcp._apply_entity_sync", apply_inventory)
    if installation_id in sessions._sessions:
        await sessions.remove(installation_id, sessions._sessions[installation_id].session_id)
    endpoint = asyncio.create_task(connector_websocket(websocket, session))  # type: ignore[arg-type]
    await websocket.inbound.put(
        message(
            "hello",
            {
                "installation_id": str(installation_id),
                "connector_version": "0.1.8-beta.5",
                "ha_version": "2026.8.0",
                "protocol_versions": [1],
            },
        )
    )
    hello_ack = await asyncio.wait_for(websocket.outbound.get(), 2.0)
    assert hello_ack["type"] == "hello_ack"
    hello_payload = hello_ack["payload"]
    assert isinstance(hello_payload, dict)
    assert 'voice_selection_v1' not in hello_payload
    session_id = hello_payload["session_id"]
    assert session_id is not None

    registered_last_seen = sessions._sessions[installation_id].last_seen
    await websocket.inbound.put(message("heartbeat", {"session_id": session_id}))
    heartbeat_ack = await asyncio.wait_for(websocket.outbound.get(), 2.0)
    assert heartbeat_ack["type"] == "heartbeat_ack"
    assert sessions._sessions[installation_id].last_seen >= registered_last_seen

    await websocket.inbound.put(
        message(
            "inventory_full",
            {
                "session_id": session_id,
                "revision": 1,
                "batch_index": 0,
                "batch_count": 1,
                "entities": [
                    {
                        "registry_id": "registry-cover-dry",
                        "entity_id": "cover.dry_contact",
                        "domain": "cover",
                        "supported_features": 15,
                        "state": None,
                        "available": True,
                        "attributes": {},
                    }
                ],
            },
        )
    )
    await asyncio.wait_for(inventory_applied.wait(), 2.0)
    assert sessions._sessions[installation_id].session_id == UUID(str(session_id))

    command_id = uuid4()
    dispatch = asyncio.create_task(
        sessions.dispatch(
            installation_id,
            command_id,
            "registry-cover-dry",
            {"operation": "open"},
            1.0,
        )
    )
    outbound_command = await asyncio.wait_for(websocket.outbound.get(), 2.0)
    assert outbound_command["type"] == "command"
    command_payload = outbound_command["payload"]
    assert isinstance(command_payload, dict)
    assert command_payload["session_id"] == session_id
    assert command_payload["session_id"] is not None
    await websocket.inbound.put(
        message(
            "command_result",
            {
                "session_id": session_id,
                "command_id": str(command_id),
                "status": "success",
            },
        )
    )
    result = await asyncio.wait_for(dispatch, 2.0)
    assert isinstance(result, CommandResultPayload)
    assert result.session_id == UUID(str(session_id))
    assert result.status == "success"
    event_types = [item["event_type"] for item in result.diagnostics]
    assert "evcp.dispatch_session_selected" in event_types
    assert "evcp.command_result_session_check" in event_types

    await websocket.inbound.put(TimeoutError())
    await asyncio.wait_for(endpoint, 2.0)
    assert installation_id not in sessions._sessions
    activity = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.installation_id == installation_id,
                    AuditEvent.event_type.in_(
                        [
                            "evcp.session_registered",
                            "evcp.heartbeat_received",
                            "evcp.heartbeat_ack",
                            "evcp.session_timeout",
                            "evcp.session_removed",
                        ]
                    ),
                )
                .order_by(AuditEvent.created_at)
            )
        ).all()
    )
    assert [event.event_type for event in activity] == [
        "evcp.session_registered",
        "evcp.heartbeat_received",
        "evcp.heartbeat_ack",
        "evcp.session_timeout",
        "evcp.session_removed",
    ]
    assert activity[0].payload_redacted_json["new_session_id"] == session_id
    assert activity[1].payload_redacted_json["payload_session_id"] == session_id
    assert activity[1].payload_redacted_json["message_id"] == activity[2].request_id
    assert activity[2].payload_redacted_json["payload_session_id"] == session_id
    assert activity[3].payload_redacted_json["reason"] == "liveness_timeout"
    assert activity[3].payload_redacted_json["timeout_seconds"] == 75.0
    assert activity[4].payload_redacted_json["requested_session_id"] == session_id
    assert activity[4].payload_redacted_json["removed"] is True
