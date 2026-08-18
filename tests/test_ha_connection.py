"""Tests for the M3 cancellation-safe connection supervisor."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant

from custom_components.ekonex_voice.client import (
    EkonexVoiceAuthError,
    EkonexVoiceCannotConnect,
)
from custom_components.ekonex_voice.connection import EkonexVoiceConnection
from custom_components.ekonex_voice.models import ConnectionState


async def test_transient_failure_uses_bounded_jitter_then_recovers(
    hass: HomeAssistant,
) -> None:
    """One supervisor retries transient errors and resets after authentication."""
    authenticate = AsyncMock(side_effect=[EkonexVoiceCannotConnect("safe"), "installation-1"])
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    connection = EkonexVoiceConnection(
        hass,
        authenticate,
        "installation-1",
        sleep=record_sleep,
        random_value=lambda: 0.5,
    )
    connection.async_start()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert connection.state is ConnectionState.ONLINE
    assert delays == [0.5]
    assert connection.retry_count == 0
    assert connection.running
    await connection.async_stop()
    assert connection.state is ConnectionState.STOPPED
    assert not connection.running


async def test_invalid_auth_stops_without_retry(hass: HomeAssistant) -> None:
    """Revoked credentials never enter an infinite reconnect loop."""
    authenticate = AsyncMock(side_effect=EkonexVoiceAuthError("safe"))
    connection = EkonexVoiceConnection(hass, authenticate, "installation-1")
    connection.async_start()
    await asyncio.sleep(0)

    assert connection.state is ConnectionState.REAUTH_REQUIRED
    assert authenticate.await_count == 1
    await connection.async_stop()


async def test_start_is_idempotent_and_stop_cancels_backoff(hass: HomeAssistant) -> None:
    """Concurrent start signals cannot create a second task."""
    sleeping = asyncio.Event()

    async def cancellable_sleep(delay: float) -> None:
        sleeping.set()
        await asyncio.Event().wait()

    authenticate = AsyncMock(side_effect=EkonexVoiceCannotConnect("safe"))
    connection = EkonexVoiceConnection(
        hass, authenticate, "installation-1", sleep=cancellable_sleep
    )
    connection.async_start()
    connection.async_start()
    await sleeping.wait()

    assert authenticate.await_count == 1
    assert connection.running
    await connection.async_stop()
    assert not connection.running
