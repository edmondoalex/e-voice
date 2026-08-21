"""M6 cloud command authorization, routing and correlation tests."""

import asyncio
import io
import logging
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
    _log_dispatch,
    command_adapter,
)
from apps.cloud_api.app.domain.models import AuditEvent, Entity
from apps.cloud_api.app.evcp import (
    CommandResultPayload,
    ConnectorSessionRegistry,
    SessionHandle,
)
from apps.cloud_api.app.logging_config import configure_info_logger


def test_dispatch_info_is_emitted_without_root_handler() -> None:
    root = logging.getLogger()
    diagnostic = logging.getLogger("apps.cloud_api.app.command_dispatch")
    uvicorn = logging.getLogger("uvicorn.error")
    original = (
        root.level,
        list(root.handlers),
        diagnostic.level,
        diagnostic.propagate,
        list(diagnostic.handlers),
        list(uvicorn.handlers),
    )
    stream = io.StringIO()
    try:
        root.setLevel(logging.WARNING)
        root.handlers.clear()
        uvicorn.handlers.clear()
        diagnostic.handlers.clear()
        configure_info_logger(diagnostic)
        assert len(diagnostic.handlers) == 1
        diagnostic.handlers[0].setStream(stream)

        _log_dispatch(
            uuid4(), uuid4(), "dry-cover-registry-id", "stop", "connector_result", status="success"
        )

        assert "evcp_command_dispatch" in stream.getvalue()
        assert '"operation":"stop"' in stream.getvalue()
        assert '"stage":"connector_result"' in stream.getvalue()
        assert root.level == logging.WARNING
        assert root.handlers == []
        configure_info_logger(diagnostic)
        assert len(diagnostic.handlers) == 1
    finally:
        root.setLevel(original[0])
        root.handlers[:] = original[1]
        diagnostic.setLevel(original[2])
        diagnostic.propagate = original[3]
        diagnostic.handlers[:] = original[4]
        uvicorn.handlers[:] = original[5]


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
    audit = (await session.scalars(select(AuditEvent))).one()
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
    assert (await pending).status == "success"
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
    websocket.application_state = WebSocketState.CONNECTED
    await registry.replace(installation_id, SessionHandle(session_id, websocket))
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    evcp_logger = logging.getLogger("apps.cloud_api.app.evcp")
    evcp_logger.addHandler(handler)
    try:
        outcome = await registry.dispatch(
            installation_id, uuid4(), "stable-light", {"operation": "power_on"}, 0.001
        )
    finally:
        evcp_logger.removeHandler(handler)
    diagnostics = stream.getvalue()
    assert outcome.status == "timeout"
    assert outcome.error_code == "COMMAND_TIMEOUT"
    assert '"stage":"sending"' in diagnostics
    assert '"transport_ready":true' in diagnostics
    assert '"stage":"timeout"' in diagnostics


async def test_dispatch_diagnostics_distinguish_offline_lookup_and_timeout(
    session: AsyncSession,
    seeded_domain: object,
) -> None:
    installation_id = seeded_domain.installation_a_id  # type: ignore[attr-defined]
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_registry_id = "dry-cover-registry-id"
    entity.ha_domain = "cover"
    entity.available = True
    entity.state = None
    entity.attributes_json = {}
    await session.commit()
    registry = ConnectorSessionRegistry()

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    diagnostic_loggers = [
        logging.getLogger("apps.cloud_api.app.evcp"),
        logging.getLogger("apps.cloud_api.app.command_dispatch"),
    ]
    for diagnostic_logger in diagnostic_loggers:
        diagnostic_logger.addHandler(handler)
    try:
        outcome = await CommandDispatchService(session, registry).dispatch(
            installation_id,
            entity.ha_registry_id,
            command_adapter.validate_python({"operation": "open"}),
        )
    finally:
        for diagnostic_logger in diagnostic_loggers:
            diagnostic_logger.removeHandler(handler)
    diagnostics = stream.getvalue()

    assert outcome.status == "unavailable"
    assert outcome.error_code == "INSTALLATION_OFFLINE"
    assert '"stage":"session_unavailable"' in diagnostics
    assert '"stage":"connector_result"' in diagnostics
    assert '"status":"unavailable"' in diagnostics
    assert "token" not in diagnostics.lower()
    assert "authorization" not in diagnostics.lower()


async def test_reconnect_marks_old_session_command_stale_without_waiter_leak() -> None:
    registry = ConnectorSessionRegistry()
    installation_id, command_id = uuid4(), uuid4()
    first, second = AsyncMock(), AsyncMock()
    first.client_state = second.client_state = WebSocketState.CONNECTED
    await registry.replace(installation_id, SessionHandle(uuid4(), first))
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
    await registry.replace(installation_id, SessionHandle(uuid4(), second))
    assert (await pending).status == "stale_session"
    assert not registry._pending


def test_typed_cloud_schema_rejects_malformed_values_and_service_injection() -> None:
    for value in (
        {"operation": "set_brightness", "brightness": "255"},
        {"operation": "set_color", "rgb_color": [0, 0, 999]},
        {"operation": "power_on", "service": "lock.unlock"},
        {"operation": "call_service", "domain": "shell_command"},
    ):
        with pytest.raises(ValidationError):
            command_adapter.validate_python(value)
