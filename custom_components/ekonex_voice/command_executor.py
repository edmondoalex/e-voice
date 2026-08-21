"""Explicit, deny-by-default Home Assistant command mappers for EVCP M6."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.light.const import ColorMode
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from .entity_inventory import EntityInventorySynchronizer

_LOGGER = logging.getLogger(__name__)

COMMAND_TIMEOUT_SECONDS = 8.0
RESULT_CACHE_SIZE = 256
CommandStatus = Literal[
    "success",
    "target_not_found",
    "target_not_exposed",
    "unsupported_command",
    "invalid_argument",
    "unavailable",
    "timeout",
    "execution_failed",
    "stale_session",
    "duplicate",
]


@dataclass(frozen=True, slots=True)
class CommandResult:
    command_id: str
    status: CommandStatus
    error_code: str | None = None

    def payload(self, session_id: str) -> dict[str, object]:
        value: dict[str, object] = {
            "session_id": session_id,
            "command_id": self.command_id,
            "status": self.status,
        }
        if self.error_code is not None:
            value["error_code"] = self.error_code
        return value


class EkonexVoiceCommandExecutor:
    """Resolve an exposed registry entity and invoke only fixed HA actions."""

    def __init__(
        self,
        hass: HomeAssistant,
        inventory: EntityInventorySynchronizer,
        *,
        timeout: float = COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        self._hass, self._inventory, self._timeout = hass, inventory, timeout
        self._results: OrderedDict[str, tuple[str, CommandResult]] = OrderedDict()

    async def async_execute(
        self, command_id: str, registry_id: str, command: dict[str, object]
    ) -> CommandResult:
        fingerprint = json.dumps(
            {"registry_id": registry_id, "command": command}, sort_keys=True, separators=(",", ":")
        )
        if cached := self._results.get(command_id):
            return (
                cached[1]
                if cached[0] == fingerprint
                else CommandResult(command_id, "duplicate", "DUPLICATE_COMMAND")
            )
        result = await self._execute_once(command_id, registry_id, command)
        self._results[command_id] = (fingerprint, result)
        self._results.move_to_end(command_id)
        while len(self._results) > RESULT_CACHE_SIZE:
            self._results.popitem(last=False)
        return result

    async def _execute_once(
        self, command_id: str, registry_id: str, command: dict[str, object]
    ) -> CommandResult:
        registry = er.async_get(self._hass)
        entry = next((item for item in registry.entities.values() if item.id == registry_id), None)
        if entry is None or entry.disabled:
            return CommandResult(command_id, "target_not_found", "ENTITY_NOT_FOUND")
        if not self._inventory.is_exposed(entry):
            return CommandResult(command_id, "target_not_exposed", "ENTITY_NOT_EXPOSED")
        state = self._hass.states.get(entry.entity_id)
        if state is None:
            return CommandResult(command_id, "target_not_found", "ENTITY_NOT_FOUND")
        if state.state == STATE_UNAVAILABLE or (
            state.state == STATE_UNKNOWN
            and not (
                entry.domain == "cover" and command.get("operation") in {"open", "close", "stop"}
            )
        ):
            return CommandResult(command_id, "unavailable", "ENTITY_UNAVAILABLE")
        try:
            domain, service, data = _map_command(entry.domain, state, command)
        except UnsupportedCommand:
            _log_cover_stop(entry.entity_id, state.state, command, None, "unsupported_command")
            return CommandResult(command_id, "unsupported_command", "OPERATION_NOT_SUPPORTED")
        except InvalidArgument:
            _log_cover_stop(entry.entity_id, state.state, command, None, "invalid_argument")
            return CommandResult(command_id, "invalid_argument", "INVALID_PARAMETER")
        _log_cover_stop(entry.entity_id, state.state, command, service, "before_service_call")
        try:
            async with asyncio.timeout(self._timeout):
                await self._hass.services.async_call(
                    domain,
                    service,
                    {"entity_id": entry.entity_id, **data},
                    blocking=True,
                )
        except TimeoutError:
            _log_cover_stop(entry.entity_id, state.state, command, service, "timeout")
            return CommandResult(command_id, "timeout", "COMMAND_TIMEOUT")
        except Exception:  # HA action exceptions must not cross the protocol boundary.
            _log_cover_stop(entry.entity_id, state.state, command, service, "execution_failed")
            return CommandResult(command_id, "execution_failed", "SERVICE_CALL_FAILED")
        _log_cover_stop(entry.entity_id, state.state, command, service, "success")
        return CommandResult(command_id, "success")


def _log_cover_stop(
    entity_id: str,
    state_before: str,
    command: dict[str, object],
    service: str | None,
    outcome: str,
) -> None:
    """Log only allowlisted HA cover STOP execution fields."""
    if command.get("operation") != "stop":
        return
    _LOGGER.info(
        "ha_cover_stop_diagnostic %s",
        json.dumps(
            {
                "entity_id": entity_id,
                "state_before": state_before,
                "evcp_operation": "stop",
                "ha_service": service,
                "dispatch_outcome": outcome,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


class UnsupportedCommand(Exception):
    """The domain does not implement the requested abstract operation."""


class InvalidArgument(Exception):
    """A typed command value violates entity-advertised constraints."""


def _map_command(
    domain: str, state: State, command: dict[str, object]
) -> tuple[str, str, dict[str, object]]:
    operation = command.get("operation")
    if not isinstance(operation, str):
        raise UnsupportedCommand
    arguments = set(command) - {"operation"}
    if domain in {"light", "switch"} and operation in {"power_on", "power_off"}:
        _require_keys(arguments, set())
        return domain, "turn_on" if operation == "power_on" else "turn_off", {}
    if domain == "light":
        return _map_light(state, operation, command, arguments)
    if domain == "cover":
        return _map_cover(state, operation, command, arguments)
    if domain == "climate":
        return _map_climate(state, operation, command, arguments)
    if domain == "fan":
        return _map_fan(state, operation, command, arguments)
    if domain in {"scene", "script", "button"}:
        expected = {"scene": "activate", "script": "activate", "button": "press"}[domain]
        if operation != expected:
            raise UnsupportedCommand
        _require_keys(arguments, set())
        return domain, {"scene": "turn_on", "script": "turn_on", "button": "press"}[domain], {}
    if domain == "number" and operation == "set_value":
        _require_keys(arguments, {"value"})
        value = _number(command.get("value"))
        minimum, maximum = (
            _number(state.attributes.get("min")),
            _number(state.attributes.get("max")),
        )
        if not minimum <= value <= maximum:
            raise InvalidArgument
        return domain, "set_value", {"value": value}
    if domain == "select" and operation == "select_option":
        _require_keys(arguments, {"option"})
        option, options = command.get("option"), state.attributes.get("options")
        if not isinstance(option, str) or not isinstance(options, list) or option not in options:
            raise InvalidArgument
        return domain, "select_option", {"option": option}
    raise UnsupportedCommand


def _map_light(
    state: State, operation: str, command: dict[str, object], arguments: set[str]
) -> tuple[str, str, dict[str, object]]:
    modes = {str(mode) for mode in state.attributes.get("supported_color_modes", [])}
    if operation == "set_brightness":
        _require_keys(arguments, {"brightness"})
        brightness = command.get("brightness")
        if type(brightness) is not int or not 0 <= brightness <= 255:
            raise InvalidArgument
        if not modes.difference({ColorMode.ONOFF, ColorMode.UNKNOWN}):
            raise UnsupportedCommand
        return "light", "turn_on", {"brightness": brightness}
    if operation == "set_color":
        _require_keys(arguments, {"rgb_color"})
        value = command.get("rgb_color")
        if not (
            isinstance(value, list)
            and len(value) == 3
            and all(type(item) is int and 0 <= item <= 255 for item in value)
        ):
            raise InvalidArgument
        if not modes.intersection(
            {ColorMode.RGB, ColorMode.RGBW, ColorMode.RGBWW, ColorMode.HS, ColorMode.XY}
        ):
            raise UnsupportedCommand
        return "light", "turn_on", {"rgb_color": value}
    if operation == "set_color_temperature":
        _require_keys(arguments, {"color_temp_kelvin"})
        value = _number(command.get("color_temp_kelvin"))
        minimum = _number(state.attributes.get("min_color_temp_kelvin"))
        maximum = _number(state.attributes.get("max_color_temp_kelvin"))
        if not minimum <= value <= maximum:
            raise InvalidArgument
        if ColorMode.COLOR_TEMP not in modes:
            raise UnsupportedCommand
        return "light", "turn_on", {"color_temp_kelvin": value}
    raise UnsupportedCommand


def _map_cover(
    state: State, operation: str, command: dict[str, object], arguments: set[str]
) -> tuple[str, str, dict[str, object]]:
    feature = {
        "open": (CoverEntityFeature.OPEN, "open_cover"),
        "close": (CoverEntityFeature.CLOSE, "close_cover"),
        "stop": (CoverEntityFeature.STOP, "stop_cover"),
    }.get(operation)
    supported = int(state.attributes.get("supported_features", 0))
    if feature is not None:
        _require_keys(arguments, set())
        if not supported & feature[0]:
            raise UnsupportedCommand
        return "cover", feature[1], {}
    if operation == "set_position":
        _require_keys(arguments, {"position"})
        position = command.get("position")
        if type(position) is not int or not 0 <= position <= 100:
            raise InvalidArgument
        if not supported & CoverEntityFeature.SET_POSITION:
            raise UnsupportedCommand
        return "cover", "set_cover_position", {"position": position}
    raise UnsupportedCommand


def _map_climate(
    state: State, operation: str, command: dict[str, object], arguments: set[str]
) -> tuple[str, str, dict[str, object]]:
    supported = int(state.attributes.get("supported_features", 0))
    if operation == "set_target_temperature":
        _require_keys(arguments, {"temperature"})
        value = _number(command.get("temperature"))
        minimum, maximum = (
            _number(state.attributes.get("min_temp")),
            _number(state.attributes.get("max_temp")),
        )
        if not minimum <= value <= maximum:
            raise InvalidArgument
        if not supported & ClimateEntityFeature.TARGET_TEMPERATURE:
            raise UnsupportedCommand
        return "climate", "set_temperature", {"temperature": value}
    if operation == "set_hvac_mode":
        _require_keys(arguments, {"hvac_mode"})
        mode, modes = command.get("hvac_mode"), state.attributes.get("hvac_modes")
        if not isinstance(mode, str) or not isinstance(modes, list) or mode not in modes:
            raise InvalidArgument
        return "climate", "set_hvac_mode", {"hvac_mode": mode}
    if operation in {"power_on", "power_off"}:
        _require_keys(arguments, set())
        feature = (
            ClimateEntityFeature.TURN_ON
            if operation == "power_on"
            else ClimateEntityFeature.TURN_OFF
        )
        if not supported & feature:
            raise UnsupportedCommand
        return "climate", "turn_on" if operation == "power_on" else "turn_off", {}
    raise UnsupportedCommand


def _map_fan(
    state: State, operation: str, command: dict[str, object], arguments: set[str]
) -> tuple[str, str, dict[str, object]]:
    supported = int(state.attributes.get("supported_features", 0))
    if operation in {"power_on", "power_off"}:
        _require_keys(arguments, set())
        feature = FanEntityFeature.TURN_ON if operation == "power_on" else FanEntityFeature.TURN_OFF
        if not supported & feature:
            raise UnsupportedCommand
        return "fan", "turn_on" if operation == "power_on" else "turn_off", {}
    if operation == "set_percentage":
        _require_keys(arguments, {"percentage"})
        percentage = command.get("percentage")
        if type(percentage) is not int or not 0 <= percentage <= 100:
            raise InvalidArgument
        if not supported & FanEntityFeature.SET_SPEED:
            raise UnsupportedCommand
        return "fan", "set_percentage", {"percentage": percentage}
    raise UnsupportedCommand


def _require_keys(actual: set[str], expected: set[str]) -> None:
    if actual != expected:
        raise InvalidArgument


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidArgument
    return float(value)
