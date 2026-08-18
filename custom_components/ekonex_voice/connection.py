"""Cancellation-safe M3 connection supervisor foundation."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant

from .client import (
    EkonexVoiceAuthError,
    EkonexVoiceCannotConnect,
    EkonexVoiceProtocolError,
)
from .const import BACKOFF_INITIAL, BACKOFF_MAXIMUM
from .models import ConnectionState

type Sleep = Callable[[float], Coroutine[Any, Any, None]]
type RandomValue = Callable[[], float]


class EkonexVoiceConnection:
    """Own exactly one future Connector session task per ConfigEntry."""

    def __init__(
        self,
        hass: HomeAssistant,
        authenticate: Callable[[], Coroutine[Any, Any, str]],
        installation_id: str,
        *,
        sleep: Sleep = asyncio.sleep,
        random_value: RandomValue = random.random,
    ) -> None:
        self._hass = hass
        self._authenticate = authenticate
        self._installation_id = installation_id
        self._sleep = sleep
        self._random_value = random_value
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.state = ConnectionState.STOPPED
        self.retry_count = 0
        self.next_retry_delay: float | None = None
        self.last_connected_at: datetime | None = None
        self.last_error_code: str | None = None

    @property
    def running(self) -> bool:
        """Return whether the one owned supervisor task is active."""
        return self._task is not None and not self._task.done()

    def async_start(self) -> None:
        """Start once; repeated reload callbacks cannot duplicate the task."""
        if self.running:
            return
        self._stop.clear()
        self._task = self._hass.async_create_background_task(
            self._async_run(), "ekonex_voice_connection", eager_start=True
        )

    async def async_stop(self) -> None:
        """Cancel backoff/session work deterministically and idempotently."""
        self._stop.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.state = ConnectionState.STOPPED
        self.next_retry_delay = None

    async def _async_run(self) -> None:
        cap = BACKOFF_INITIAL
        while not self._stop.is_set():
            self.state = ConnectionState.CONNECTING
            try:
                authenticated_installation = await self._authenticate()
                if authenticated_installation != self._installation_id:
                    raise EkonexVoiceProtocolError("installation_identity_mismatch")
            except asyncio.CancelledError:
                raise
            except EkonexVoiceAuthError:
                self.state = ConnectionState.REAUTH_REQUIRED
                self.last_error_code = "invalid_auth"
                return
            except EkonexVoiceProtocolError:
                self.state = ConnectionState.PROTOCOL_ERROR
                self.last_error_code = "protocol_error"
                return
            except EkonexVoiceCannotConnect:
                self.state = ConnectionState.BACKING_OFF
                self.last_error_code = "cannot_connect"
                self.retry_count += 1
                delay = self._random_value() * cap
                self.next_retry_delay = delay
                await self._sleep(delay)
                cap = min(cap * 2, BACKOFF_MAXIMUM)
                continue

            self.state = ConnectionState.ONLINE
            self.retry_count = 0
            self.next_retry_delay = None
            self.last_error_code = None
            self.last_connected_at = datetime.now(UTC)
            await self._stop.wait()
