"""Tests for the M4 cancellation-safe EVCP supervisor."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from aiohttp import WSMessage, WSMsgType
from homeassistant.core import HomeAssistant

from custom_components.ekonex_voice.client import EkonexVoiceAuthError, EkonexVoiceCannotConnect
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


async def test_transient_failure_uses_bounded_jitter_then_connects(hass: HomeAssistant) -> None:
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
    connection.async_start()
    await online.wait()
    assert connection.state is ConnectionState.ONLINE
    assert delays[:2] == [0.5, 30.0]
    assert connection.retry_count == 0
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
