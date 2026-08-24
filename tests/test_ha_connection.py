"""Tests for the M4 cancellation-safe EVCP supervisor."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import WSMessage, WSMsgType
from homeassistant.core import HomeAssistant

from custom_components.ekonex_voice.client import EkonexVoiceAuthError, EkonexVoiceCannotConnect
from custom_components.ekonex_voice.command_executor import CommandResult
from custom_components.ekonex_voice.connection import EkonexVoiceConnection
from custom_components.ekonex_voice.models import ConnectionState


class FakeWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self.close_code: int | None = None
        self.messages: asyncio.Queue[WSMessage] = asyncio.Queue()

    async def send_json(self, message: dict[str, object]) -> None:
        message_type = str(message["type"])
        payload = message["payload"]
        assert isinstance(payload, dict)
        response_payload: dict[str, object]
        if message_type == "hello":
            response_payload = {
                "installation_id": payload["installation_id"],
                "session_id": "75a8dd73-7645-4e13-81c6-d90d75d8c261",
                "heartbeat_interval_seconds": 30,
                "sync_revision": 0,
            }
            response_type = "hello_ack"
        else:
            response_payload = {"session_id": payload["session_id"]}
            response_type = "heartbeat_ack"
        response = {
            "version": 1,
            "type": response_type,
            "id": message["id"],
            "timestamp": "2026-08-18T10:00:00Z",
            "payload": response_payload,
        }
        await self.messages.put(WSMessage(WSMsgType.TEXT, json.dumps(response), None))

    async def receive(self) -> WSMessage:
        return await self.messages.get()

    async def close(self) -> None:
        self.closed = True


async def test_transient_failure_uses_bounded_jitter_then_connects(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    websocket = FakeWebSocket()
    connect = AsyncMock(side_effect=[EkonexVoiceCannotConnect("safe"), websocket])
    delays: list[float] = []
    online = asyncio.Event()

    async def record_sleep(delay: float) -> None:
        delays.append(delay)
        if delay == 30:
            online.set()
            await asyncio.Event().wait()

    connection = EkonexVoiceConnection(
        hass, connect, "installation-1", sleep=record_sleep, random_value=lambda: 0.5
    )
    info_log = MagicMock()
    monkeypatch.setattr("custom_components.ekonex_voice.connection._LOGGER.info", info_log)
    connection.async_start()
    await online.wait()
    assert connection.state is ConnectionState.ONLINE
    assert delays[:2] == [0.5, 30.0]
    assert connection.retry_count == 0
    acknowledged = next(
        call for call in info_log.call_args_list if call.args[0] == "evcp_session_acknowledged %s"
    )
    assert acknowledged.args[1]["installation_id"] == "installation-1"
    assert acknowledged.args[1]["session_id"] == "75a8dd73-7645-4e13-81c6-d90d75d8c261"
    await connection.async_stop()
    assert websocket.closed


async def test_invalid_auth_stops_and_requests_reauth(hass: HomeAssistant) -> None:
    reauth = MagicMock()
    connection = EkonexVoiceConnection(
        hass,
        AsyncMock(side_effect=EkonexVoiceAuthError("safe")),
        "installation-1",
        on_auth_failure=reauth,
    )
    connection.async_start()
    await asyncio.sleep(0)
    assert connection.state is ConnectionState.REAUTH_REQUIRED
    reauth.assert_called_once_with()
    await connection.async_stop()


async def test_start_is_idempotent_and_stop_cancels_backoff(hass: HomeAssistant) -> None:
    sleeping = asyncio.Event()

    async def cancellable_sleep(delay: float) -> None:
        sleeping.set()
        await asyncio.Event().wait()

    connect = AsyncMock(side_effect=EkonexVoiceCannotConnect("safe"))
    connection = EkonexVoiceConnection(hass, connect, "installation-1", sleep=cancellable_sleep)
    connection.async_start()
    connection.async_start()
    await sleeping.wait()
    assert connect.await_count == 1
    await connection.async_stop()
    assert not connection.running


async def test_command_result_is_correlated_and_stale_session_is_rejected(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = AsyncMock()
    executor.async_execute.return_value = CommandResult("command-1", "success")
    connection = EkonexVoiceConnection(
        hass, AsyncMock(), "installation-1", command_executor=executor
    )
    websocket = AsyncMock()
    info_log = MagicMock()
    monkeypatch.setattr("custom_components.ekonex_voice.connection._LOGGER.info", info_log)
    payload = {
        "session_id": "75a8dd73-7645-4e13-81c6-d90d75d8c261",
        "command_id": "6d6e299a-93cb-471f-9d1a-fe2855a665ea",
        "correlation_id": "cbb498ed-ad8e-4686-8f44-69c6bec37c1a",
        "registry_id": "stable-light",
        "command": {"operation": "power_on"},
    }
    executor.async_execute.return_value = CommandResult(
        payload["command_id"], "success", correlation_id=payload["correlation_id"]
    )
    await connection._handle_command(websocket, payload["session_id"], payload)
    executor.async_execute.assert_awaited_once_with(
        payload["command_id"],
        "stable-light",
        {"operation": "power_on"},
        correlation_id=payload["correlation_id"],
    )
    sent = websocket.send_json.await_args.args[0]
    assert sent["type"] == "command_result"
    assert sent["payload"]["command_id"] == payload["command_id"]
    assert sent["payload"]["correlation_id"] == payload["correlation_id"]
    assert sent["payload"]["status"] == "success"
    session_check = sent["payload"]["diagnostics"][0]
    assert session_check == {
        "event_type": "connector.command_session_check",
        "installation_id": "installation-1",
        "requested_session_id": payload["session_id"],
        "local_session_id": payload["session_id"],
        "command_id": payload["command_id"],
        "registry_id": "stable-light",
        "operation": "power_on",
        "session_match": True,
        "timestamp": session_check["timestamp"],
    }
    formats = [call.args[0] for call in info_log.call_args_list]
    assert "connector_command_session_check %s" in formats
    assert "connector_command_result_session %s" in formats
    serialized_logs = repr(info_log.call_args_list).casefold()
    assert "authorization" not in serialized_logs
    assert "token" not in serialized_logs

    websocket.reset_mock()
    executor.reset_mock()
    await connection._handle_command(websocket, "different-session", payload)
    executor.async_execute.assert_not_awaited()
    assert websocket.send_json.await_args.args[0]["payload"]["status"] == "stale_session"
    stale_check = websocket.send_json.await_args.args[0]["payload"]["diagnostics"][0]
    assert stale_check["requested_session_id"] == payload["session_id"]
    assert stale_check["local_session_id"] == "different-session"
    assert stale_check["session_match"] is False
    assert (
        websocket.send_json.await_args.args[0]["payload"]["correlation_id"]
        == payload["correlation_id"]
    )
