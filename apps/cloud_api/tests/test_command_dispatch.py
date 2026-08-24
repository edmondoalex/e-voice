"""M6 cloud command authorization, routing and correlation tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from apps.cloud_api.app.command_dispatch import (
    CommandDispatchService,
    PowerCommand,
    command_adapter,
)
from apps.cloud_api.app.domain.models import AuditEvent, Entity
from apps.cloud_api.app.evcp import (
    CommandResultPayload,
    ConnectorSessionRegistry,
    SessionHandle,
)


async def test_dispatch_requires_active_installation_scoped_exposed_entity(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation_id = seeded_domain.installation_a_id  # type: ignore[attr-defined]
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_registry_id = "stable-light"
    router = AsyncMock()
    command_id = uuid4()
    router.dispatch.return_value = CommandResultPayload(
        session_id=uuid4(), command_id=command_id, status="success"
    )
    await session.commit()

    outcome = await CommandDispatchService(session, router).dispatch(
        installation_id,
        "stable-light",
        PowerCommand(operation="power_on"),
        command_id=command_id,
    )
    assert outcome.status == "success"
    router.dispatch.assert_awaited_once()
    audit = await session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "command_dispatch")
    )
    assert audit is not None
    assert audit.request_id == str(command_id)
    assert audit.payload_redacted_json == {
        "registry_id": "stable-light",
        "operation": "power_on",
    }


async def test_tombstoned_and_cross_installation_targets_never_route(
    session: AsyncSession, seeded_domain: object
) -> None:
    entity_a = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    entity_b = await session.get(Entity, seeded_domain.entity_b_id)  # type: ignore[attr-defined]
    assert entity_a is not None and entity_b is not None
    entity_a.ha_registry_id = "removed"
    entity_a.deleted_at = entity_a.updated_at
    entity_b.ha_registry_id = "other-tenant"
    await session.commit()
    router = AsyncMock()
    service = CommandDispatchService(session, router)

    removed = await service.dispatch(
        seeded_domain.installation_a_id,  # type: ignore[attr-defined]
        "removed",
        PowerCommand(operation="power_on"),
    )
    cross_installation = await service.dispatch(
        seeded_domain.installation_a_id,  # type: ignore[attr-defined]
        "other-tenant",
        PowerCommand(operation="power_on"),
    )
    assert removed.status == "target_not_exposed"
    assert cross_installation.status == "target_not_found"
    router.dispatch.assert_not_awaited()


async def test_session_registry_correlates_result_and_deduplicates_command_id() -> None:
    registry = ConnectorSessionRegistry()
    installation_id, session_id, command_id = uuid4(), uuid4(), uuid4()
    websocket = AsyncMock()
    websocket.client_state = WebSocketState.CONNECTED
    await registry.replace(installation_id, SessionHandle(session_id, websocket))
    command: dict[str, object] = {"operation": "power_on"}
    pending = asyncio.create_task(
        registry.dispatch(installation_id, command_id, "stable-light", command, 1.0)
    )
    await asyncio.sleep(0)
    result = CommandResultPayload(session_id=session_id, command_id=command_id, status="success")
    assert await registry.resolve(installation_id, session_id, result)
    outcome = await pending
    assert outcome.status == "success"
    selected = outcome.diagnostics[0]
    assert selected["event_type"] == "evcp.dispatch_session_selected"
    assert selected["installation_id"] == str(installation_id)
    assert selected["command_id"] == str(command_id)
    assert selected["current_session_id"] == str(session_id)
    assert selected["payload_session_id"] == str(session_id)
    assert selected["pending_key"] == f"{installation_id}:{command_id}"
    resolved = outcome.diagnostics[-1]
    assert resolved["event_type"] == "evcp.command_result_session_check"
    assert resolved["match"] is True
    replay = await registry.dispatch(installation_id, command_id, "stable-light", command, 1.0)
    conflict = await registry.dispatch(installation_id, command_id, "different", command, 1.0)
    assert replay.status == "success"
    assert conflict.status == "duplicate"
    websocket.send_json.assert_awaited_once()


async def test_session_registry_has_bounded_cloud_timeout() -> None:
    registry = ConnectorSessionRegistry()
    installation_id, session_id = uuid4(), uuid4()
    websocket = AsyncMock()
    websocket.client_state = WebSocketState.CONNECTED
    await registry.replace(installation_id, SessionHandle(session_id, websocket))
    outcome = await registry.dispatch(
        installation_id, uuid4(), "stable-light", {"operation": "power_on"}, 0.001
    )
    assert outcome.status == "timeout"
    assert outcome.error_code == "COMMAND_TIMEOUT"


async def test_diagnostics_distinguish_evcp_sent_from_connector_received(
    session: AsyncSession, seeded_domain: object
) -> None:
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_registry_id = "stable-light"
    await session.commit()
    command_id, correlation_id, session_id = uuid4(), uuid4(), uuid4()
    router = AsyncMock()
    router.dispatch.return_value = CommandResultPayload(
        session_id=session_id,
        command_id=command_id,
        correlation_id=correlation_id,
        status="timeout",
        error_code="COMMAND_TIMEOUT",
    )

    outcome = await CommandDispatchService(session, router).dispatch(
        seeded_domain.installation_a_id,  # type: ignore[attr-defined]
        "stable-light",
        PowerCommand(operation="power_on"),
        command_id=command_id,
        correlation_id=correlation_id,
    )

    assert outcome.status == "timeout"
    events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.payload_redacted_json["correlation_id"].as_string()
                    == str(correlation_id)
                )
                .order_by(AuditEvent.created_at)
            )
        ).all()
    )
    sent = next(event for event in events if event.event_type == "evcp.command_sent")
    summary = next(event for event in events if event.event_type == "command.final_summary")
    assert sent.result == "sent"
    assert summary.payload_redacted_json["connector_received"] is False
    assert summary.payload_redacted_json["service_result"] is None


async def test_reconnect_drains_old_command_and_old_cleanup_preserves_new_session() -> None:
    registry = ConnectorSessionRegistry()
    installation_id, command_id = uuid4(), uuid4()
    first_session, second_session = uuid4(), uuid4()
    first, second = AsyncMock(), AsyncMock()
    first.client_state = second.client_state = WebSocketState.CONNECTED
    await registry.replace(installation_id, SessionHandle(first_session, first))
    pending = asyncio.create_task(
        registry.dispatch(
            installation_id,
            command_id,
            "stable-light",
            {"operation": "power_on"},
            1.0,
        )
    )
    await asyncio.sleep(0)
    reconnect = asyncio.create_task(
        registry.replace(installation_id, SessionHandle(second_session, second))
    )
    await asyncio.sleep(0)
    assert not reconnect.done()
    result = CommandResultPayload(
        session_id=first_session,
        command_id=command_id,
        status="success",
    )
    assert await registry.resolve(installation_id, first_session, result)
    assert (await pending).status == "success"
    await reconnect
    await registry.remove(installation_id, first_session)
    assert registry._sessions[installation_id].session_id == second_session
    assert not registry._pending


async def test_heartbeat_keeps_session_valid_and_expired_session_is_stale() -> None:
    registry = ConnectorSessionRegistry()
    installation_id, session_id = uuid4(), uuid4()
    websocket = AsyncMock()
    websocket.client_state = WebSocketState.CONNECTED
    handle = SessionHandle(
        session_id,
        websocket,
        last_seen=datetime.now(UTC) - timedelta(seconds=74),
    )
    await registry.replace(installation_id, handle)
    assert await registry.touch(installation_id, session_id)
    command_id = uuid4()
    command = asyncio.create_task(
        registry.dispatch(
            installation_id,
            command_id,
            "stable-cover",
            {"operation": "open"},
            1.0,
        )
    )
    await asyncio.sleep(0)
    assert await registry.resolve(
        installation_id,
        session_id,
        CommandResultPayload(
            session_id=session_id,
            command_id=command_id,
            status="success",
        ),
    )
    assert (await command).status == "success"

    expired_installation, expired_session = uuid4(), uuid4()
    expired_socket = AsyncMock()
    expired_socket.client_state = WebSocketState.CONNECTED
    await registry.replace(
        expired_installation,
        SessionHandle(
            expired_session,
            expired_socket,
            last_seen=datetime.now(UTC) - timedelta(seconds=76),
        ),
    )
    for _ in range(2):
        expired = await registry.dispatch(
            expired_installation,
            uuid4(),
            "stable-cover",
            {"operation": "open"},
            1.0,
        )
        assert expired.status == "stale_session"
        assert expired.error_code == "STALE_SESSION"
        diagnostic = expired.diagnostics[0]
        assert diagnostic["reason"] == "heartbeat_expired"
        assert diagnostic["active_session_id"] == str(expired_session)
        assert diagnostic["requested_installation_id"] == str(expired_installation)
        assert diagnostic["stale_threshold_seconds"] == 75.0
    expired_socket.send_json.assert_not_awaited()


async def test_session_registry_isolates_two_installations() -> None:
    registry = ConnectorSessionRegistry()
    installation_a, installation_b = uuid4(), uuid4()
    session_a, session_b = uuid4(), uuid4()
    command_a, command_b = uuid4(), uuid4()
    socket_a, socket_b = AsyncMock(), AsyncMock()
    socket_a.client_state = socket_b.client_state = WebSocketState.CONNECTED
    await registry.replace(installation_a, SessionHandle(session_a, socket_a))
    await registry.replace(installation_b, SessionHandle(session_b, socket_b))

    pending_a = asyncio.create_task(
        registry.dispatch(installation_a, command_a, "cover-a", {"operation": "open"}, 1.0)
    )
    pending_b = asyncio.create_task(
        registry.dispatch(installation_b, command_b, "cover-b", {"operation": "close"}, 1.0)
    )
    await asyncio.sleep(0)
    assert socket_a.send_json.await_args.args[0]["payload"]["session_id"] == str(session_a)
    assert socket_b.send_json.await_args.args[0]["payload"]["session_id"] == str(session_b)
    assert await registry.resolve(
        installation_a,
        session_a,
        CommandResultPayload(session_id=session_a, command_id=command_a, status="success"),
    )
    assert not pending_b.done()
    assert await registry.resolve(
        installation_b,
        session_b,
        CommandResultPayload(session_id=session_b, command_id=command_b, status="success"),
    )
    assert (await pending_a).status == "success"
    assert (await pending_b).status == "success"


async def test_replace_resolve_mismatch_and_remove_report_exact_session_ids() -> None:
    registry = ConnectorSessionRegistry()
    installation_id = uuid4()
    first_session, second_session, unrelated_session = uuid4(), uuid4(), uuid4()
    first_socket, second_socket = AsyncMock(), AsyncMock()
    first_socket.client_state = second_socket.client_state = WebSocketState.CONNECTED

    registered = await registry.replace(installation_id, SessionHandle(first_session, first_socket))
    assert registered["event_type"] == "evcp.session_registered"
    assert registered["reason"] == "new"
    assert registered["new_session_id"] == str(first_session)
    assert registered["previous_session_id"] is None
    replaced = await registry.replace(installation_id, SessionHandle(second_session, second_socket))
    assert replaced["reason"] == "replaced"
    assert replaced["new_session_id"] == str(second_session)
    assert replaced["previous_session_id"] == str(first_session)

    command_id = uuid4()
    matched, mismatch = await registry.resolve_with_diagnostic(
        installation_id,
        unrelated_session,
        CommandResultPayload(
            session_id=unrelated_session,
            command_id=command_id,
            status="stale_session",
            error_code="STALE_SESSION",
        ),
    )
    assert matched is False
    assert mismatch["registry_session_id"] == str(second_session)
    assert mismatch["websocket_session_id"] == str(unrelated_session)
    assert mismatch["result_session_id"] == str(unrelated_session)
    assert mismatch["reason"] == "websocket_session_not_current"

    preserved = await registry.remove(installation_id, first_session)
    assert preserved["event_type"] == "evcp.session_removed"
    assert preserved["requested_session_id"] == str(first_session)
    assert preserved["current_session_id"] == str(second_session)
    assert preserved["removed"] is False
    assert preserved["reason"] == "requested_session_not_current"
    removed = await registry.remove(installation_id, second_session)
    assert removed["removed"] is True
    assert removed["reason"] == "current_session_removed"


def test_typed_cloud_schema_rejects_malformed_values_and_service_injection() -> None:
    for value in (
        {"operation": "set_brightness", "brightness": "255"},
        {"operation": "set_color", "rgb_color": [0, 0, 999]},
        {"operation": "power_on", "service": "lock.unlock"},
        {"operation": "call_service", "domain": "shell_command"},
    ):
        with pytest.raises(ValidationError):
            command_adapter.validate_python(value)
