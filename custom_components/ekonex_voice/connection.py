"""Cancellation-safe EVCP connection supervisor."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientError, ClientWebSocketResponse, WSMsgType
from homeassistant.core import HomeAssistant

from .client import EkonexVoiceAuthError, EkonexVoiceCannotConnect, EkonexVoiceProtocolError
from .command_executor import CommandResult, EkonexVoiceCommandExecutor
from .const import BACKOFF_SCHEDULE, HEARTBEAT_TIMEOUT
from .entity_inventory import EntityInventorySynchronizer
from .evcp import envelope, parse_command, parse_message
from .models import ConnectionState

type Sleep = Callable[[float], Coroutine[Any, Any, None]]
type RandomValue = Callable[[], float]
type Connect = Callable[[], Coroutine[Any, Any, ClientWebSocketResponse]]
_LOGGER = logging.getLogger(__name__)
CONNECTOR_CAPABILITIES = {
    "supports_correlation_id": True,
    "supports_command_diagnostics": True,
    "supports_heartbeat_diagnostics": True,
}


class EkonexVoiceConnection:
    """Own exactly one outbound EVCP session task per ConfigEntry."""

    def __init__(
        self,
        hass: HomeAssistant,
        connect: Connect,
        installation_id: str,
        *,
        connector_version: str = "0.1.0",
        ha_version: str = "unknown",
        on_auth_failure: Callable[[], None] | None = None,
        inventory: EntityInventorySynchronizer | None = None,
        command_executor: EkonexVoiceCommandExecutor | None = None,
        sleep: Sleep = asyncio.sleep,
        random_value: RandomValue = random.random,
    ) -> None:
        self._hass, self._connect = hass, connect
        self._installation_id = installation_id
        self._connector_version, self._ha_version = connector_version, ha_version
        self._on_auth_failure, self._sleep, self._random_value = (
            on_auth_failure,
            sleep,
            random_value,
        )
        self._inventory = inventory
        self._command_executor = command_executor
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.state = ConnectionState.STOPPED
        self.retry_count = 0
        self.next_retry_delay: float | None = None
        self.last_connected_at: datetime | None = None
        self.last_error_code: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def async_start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = self._hass.async_create_background_task(
            self._async_run(), "ekonex_voice_connection", eager_start=True
        )

    async def async_stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.state, self.next_retry_delay = ConnectionState.STOPPED, None

    async def _async_run(self) -> None:
        backoff_index = 0
        while not self._stop.is_set():
            self.state = ConnectionState.CONNECTING
            websocket: ClientWebSocketResponse | None = None
            try:
                websocket = await self._connect()
                await self._run_session(websocket)
                if not self._stop.is_set():
                    raise EkonexVoiceCannotConnect("session_closed")
            except asyncio.CancelledError:
                raise
            except EkonexVoiceAuthError:
                self.state, self.last_error_code = ConnectionState.REAUTH_REQUIRED, "invalid_auth"
                if self._on_auth_failure is not None:
                    self._on_auth_failure()
                return
            except EkonexVoiceProtocolError as error:
                error_code = (
                    "CONNECTOR_VERSION_INCOMPATIBLE"
                    if str(error) == "connector_version_incompatible"
                    else "protocol_error"
                )
                self.state, self.last_error_code = ConnectionState.PROTOCOL_ERROR, error_code
                return
            except EkonexVoiceCannotConnect:
                self.state, self.last_error_code = ConnectionState.BACKING_OFF, "cannot_connect"
                self.retry_count += 1
                delay = self._random_value() * BACKOFF_SCHEDULE[backoff_index]
                self.next_retry_delay = delay
                await self._sleep(delay)
                backoff_index = min(backoff_index + 1, len(BACKOFF_SCHEDULE) - 1)
            except (ClientError, OSError, TimeoutError):
                self.state, self.last_error_code = ConnectionState.BACKING_OFF, "transport_error"
                self.retry_count += 1
                delay = self._random_value() * BACKOFF_SCHEDULE[backoff_index]
                self.next_retry_delay = delay
                _LOGGER.warning("Connector transport failed; retrying in %.1f seconds", delay)
                await self._sleep(delay)
                backoff_index = min(backoff_index + 1, len(BACKOFF_SCHEDULE) - 1)
            except ValueError:
                self.state, self.last_error_code = ConnectionState.PROTOCOL_ERROR, "inventory_error"
                _LOGGER.exception("Entity inventory serialization failed")
                return
            finally:
                if self._inventory is not None:
                    await self._inventory.async_stop()
                if websocket is not None and not websocket.closed:
                    await websocket.close()

    async def _run_session(self, websocket: ClientWebSocketResponse) -> None:
        hello = envelope(
            "hello",
            {
                "installation_id": self._installation_id,
                "connector_version": self._connector_version,
                "ha_version": self._ha_version,
                "protocol_versions": [1],
                "capabilities": CONNECTOR_CAPABILITIES,
            },
        )
        await websocket.send_json(hello)
        payload = await self._receive_ack(websocket, "hello_ack", str(hello["id"]))
        if set(payload) != {
            "installation_id",
            "session_id",
            "heartbeat_interval_seconds",
            "sync_revision",
        }:
            raise EkonexVoiceProtocolError("invalid_hello_ack")
        if payload.get("installation_id") != self._installation_id:
            raise EkonexVoiceProtocolError("installation_identity_mismatch")
        session_id, interval = payload.get("session_id"), payload.get("heartbeat_interval_seconds")
        sync_revision = payload.get("sync_revision")
        if (
            not isinstance(session_id, str)
            or not isinstance(interval, int)
            or interval < 1
            or not isinstance(sync_revision, int)
        ):
            raise EkonexVoiceProtocolError("invalid_hello_ack")
        self.state, self.retry_count, self.next_retry_delay = ConnectionState.ONLINE, 0, None
        self.last_error_code, self.last_connected_at = None, datetime.now(UTC)
        _LOGGER.info(
            "evcp_session_acknowledged %s",
            {
                "installation_id": self._installation_id,
                "session_id": session_id,
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
        )
        if self._inventory is not None:
            await self._inventory.async_start(websocket, session_id, sync_revision)
        while not self._stop.is_set():
            sleeper = asyncio.create_task(self._sleep(float(interval)))
            receiver = asyncio.create_task(self._receive_message(websocket, session_id=session_id))
            try:
                done, pending = await asyncio.wait(
                    {sleeper, receiver}, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                sleeper.cancel()
                receiver.cancel()
                await asyncio.gather(sleeper, receiver, return_exceptions=True)
                raise
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if receiver in done:
                message_type, _, inbound = receiver.result()
                if message_type != "command":
                    raise EkonexVoiceProtocolError("unexpected_message")
                await self._handle_command(websocket, session_id, inbound)
                continue
            heartbeat = envelope("heartbeat", {"session_id": session_id})
            _LOGGER.info(
                "heartbeat_sent %s",
                {
                    "installation_id": self._installation_id,
                    "session_id": session_id,
                    "message_id": heartbeat["id"],
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                },
            )
            await websocket.send_json(heartbeat)
            ack = await self._receive_ack(
                websocket, "heartbeat_ack", str(heartbeat["id"]), session_id=session_id
            )
            if set(ack) != {"session_id"} or ack.get("session_id") != session_id:
                raise EkonexVoiceProtocolError("invalid_heartbeat_ack")
            _LOGGER.info(
                "heartbeat_ack %s",
                {
                    "installation_id": self._installation_id,
                    "session_id": session_id,
                    "message_id": heartbeat["id"],
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                },
            )

    async def _receive_ack(
        self,
        websocket: ClientWebSocketResponse,
        message_type: str,
        message_id: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        while True:
            received_type, received_id, payload = await self._receive_message(
                websocket, session_id=session_id
            )
            if received_type == "command" and session_id is not None:
                await self._handle_command(websocket, session_id, payload)
                continue
            if received_type != message_type or received_id != message_id:
                raise EkonexVoiceProtocolError("invalid_message")
            return payload

    async def _receive_message(
        self,
        websocket: ClientWebSocketResponse,
        *,
        session_id: str | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        try:
            async with asyncio.timeout(HEARTBEAT_TIMEOUT):
                message = await websocket.receive()
        except TimeoutError as error:
            _LOGGER.warning(
                "session_timeout %s",
                {
                    "installation_id": self._installation_id,
                    "session_id": session_id,
                    "timeout_seconds": HEARTBEAT_TIMEOUT,
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                },
            )
            raise EkonexVoiceCannotConnect("cloud_timeout") from error
        if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED}:
            if websocket.close_code in {4001, 4004}:
                raise EkonexVoiceAuthError("invalid_auth")
            if websocket.close_code == 4010:
                raise EkonexVoiceProtocolError("connector_version_incompatible")
            if websocket.close_code in {4002, 4003}:
                raise EkonexVoiceProtocolError("protocol_rejected")
            raise EkonexVoiceCannotConnect("cloud_closed")
        if message.type is WSMsgType.ERROR:
            raise EkonexVoiceCannotConnect("cloud_error")
        if message.type is not WSMsgType.TEXT:
            raise EkonexVoiceProtocolError("invalid_message_type")
        try:
            return parse_message(message.data)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise EkonexVoiceProtocolError("invalid_message") from error

    async def _handle_command(
        self, websocket: ClientWebSocketResponse, session_id: str, payload: dict[str, Any]
    ) -> None:
        try:
            requested_session, command_id, registry_id, correlation_id, command = parse_command(
                payload
            )
        except (ValueError, TypeError):
            raise EkonexVoiceProtocolError("invalid_command") from None
        session_match = requested_session == session_id
        session_check: dict[str, object] = {
            "event_type": "connector.command_session_check",
            "installation_id": self._installation_id,
            "requested_session_id": requested_session,
            "local_session_id": session_id,
            "command_id": command_id,
            "registry_id": registry_id,
            "operation": command.get("operation"),
            "session_match": session_match,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        _LOGGER.info("connector_command_session_check %s", session_check)
        if requested_session != session_id:
            result = CommandResult(command_id, "stale_session", "STALE_SESSION", correlation_id)
        elif self._command_executor is None:
            result = CommandResult(
                command_id,
                "unsupported_command",
                "OPERATION_NOT_SUPPORTED",
                correlation_id,
            )
        else:
            result = await self._command_executor.async_execute(
                command_id, registry_id, command, correlation_id=correlation_id
            )
        result = CommandResult(
            result.command_id,
            result.status,
            result.error_code,
            result.correlation_id,
            (session_check, *result.diagnostics[:14]),
        )
        _LOGGER.info(
            "connector_command_result_session %s",
            {
                "local_session_id": session_id,
                "result_session_id": session_id,
                "command_id": result.command_id,
                "status": result.status,
                "error_code": result.error_code,
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
        )
        await websocket.send_json(envelope("command_result", result.payload(session_id)))
