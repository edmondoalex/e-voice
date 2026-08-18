"""M6 explicit Home Assistant command mapper tests."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.ekonex_voice.command_executor import EkonexVoiceCommandExecutor
from custom_components.ekonex_voice.entity_inventory import EntityInventorySynchronizer


def exposed_entity(
    hass: HomeAssistant, domain: str, attributes: dict[str, object] | None = None
) -> tuple[er.RegistryEntry, EkonexVoiceCommandExecutor]:
    entry = er.async_get(hass).async_get_or_create(domain, "test", f"stable-{domain}")
    hass.states.async_set(entry.entity_id, "on", attributes or {})
    inventory = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    return entry, EkonexVoiceCommandExecutor(hass, inventory)


@pytest.mark.parametrize(
    ("domain", "attributes", "command", "expected_service", "expected_data"),
    [
        ("light", {}, {"operation": "power_on"}, "turn_on", {}),
        (
            "light",
            {"supported_color_modes": ["brightness"]},
            {"operation": "set_brightness", "brightness": 128},
            "turn_on",
            {"brightness": 128},
        ),
        ("switch", {}, {"operation": "power_off"}, "turn_off", {}),
        (
            "cover",
            {"supported_features": 1},
            {"operation": "open"},
            "open_cover",
            {},
        ),
        (
            "climate",
            {"supported_features": 1, "min_temp": 7, "max_temp": 35},
            {"operation": "set_target_temperature", "temperature": 21.5},
            "set_temperature",
            {"temperature": 21.5},
        ),
        (
            "fan",
            {"supported_features": 1},
            {"operation": "set_percentage", "percentage": 50},
            "set_percentage",
            {"percentage": 50},
        ),
        ("scene", {}, {"operation": "activate"}, "turn_on", {}),
        ("script", {}, {"operation": "activate"}, "turn_on", {}),
        ("button", {}, {"operation": "press"}, "press", {}),
        (
            "number",
            {"min": 0, "max": 10},
            {"operation": "set_value", "value": 4.5},
            "set_value",
            {"value": 4.5},
        ),
        (
            "select",
            {"options": ["eco", "comfort"]},
            {"operation": "select_option", "option": "eco"},
            "select_option",
            {"option": "eco"},
        ),
    ],
)
async def test_explicit_mapper_success(
    hass: HomeAssistant,
    domain: str,
    attributes: dict[str, object],
    command: dict[str, object],
    expected_service: str,
    expected_data: dict[str, object],
) -> None:
    entry, executor = exposed_entity(hass, domain, attributes)
    call = AsyncMock()
    with patch.object(hass.services, "async_call", call):
        result = await executor.async_execute(
            "00000000-0000-0000-0000-000000000001", entry.id, command
        )
    assert result.status == "success"
    call.assert_awaited_once_with(
        domain, expected_service, {"entity_id": entry.entity_id, **expected_data}, blocking=True
    )


@pytest.mark.parametrize(
    ("domain", "attributes", "command"),
    [
        (
            "light",
            {"supported_color_modes": ["onoff"]},
            {"operation": "set_brightness", "brightness": 999},
        ),
        (
            "light",
            {"supported_color_modes": ["brightness"]},
            {"operation": "set_color", "rgb_color": [1, 2, 3]},
        ),
        ("cover", {"supported_features": 0}, {"operation": "set_position", "position": 50}),
        (
            "climate",
            {"supported_features": 1, "min_temp": 10, "max_temp": 30},
            {"operation": "set_target_temperature", "temperature": 40},
        ),
        ("fan", {"supported_features": 0}, {"operation": "set_percentage", "percentage": 50}),
        ("number", {"min": 0, "max": 10}, {"operation": "set_value", "value": 11}),
        ("select", {"options": ["eco"]}, {"operation": "select_option", "option": "other"}),
    ],
)
async def test_invalid_or_unsupported_capability_has_no_side_effect(
    hass: HomeAssistant,
    domain: str,
    attributes: dict[str, object],
    command: dict[str, object],
) -> None:
    entry, executor = exposed_entity(hass, domain, attributes)
    call = AsyncMock()
    with patch.object(hass.services, "async_call", call):
        result = await executor.async_execute(
            "00000000-0000-0000-0000-000000000002", entry.id, command
        )
    assert result.status in {"invalid_argument", "unsupported_command"}
    call.assert_not_awaited()


async def test_arbitrary_service_injection_and_unexposed_target_are_rejected(
    hass: HomeAssistant,
) -> None:
    entry, executor = exposed_entity(hass, "lock")
    call = AsyncMock()
    with patch.object(hass.services, "async_call", call):
        result = await executor.async_execute(
            "00000000-0000-0000-0000-000000000003",
            entry.id,
            {"operation": "call_service", "service": "unlock"},
        )
    assert result.status == "unsupported_command"
    call.assert_not_awaited()

    other = er.async_get(hass).async_get_or_create("switch", "test", "not-exposed")
    hass.states.async_set(other.entity_id, "on")
    assert (
        await executor.async_execute(
            "00000000-0000-0000-0000-000000000004",
            other.id,
            {"operation": "power_off"},
        )
    ).status == "target_not_exposed"


async def test_missing_disabled_and_unavailable_entities_never_execute(
    hass: HomeAssistant,
) -> None:
    entry, executor = exposed_entity(hass, "switch")
    call = AsyncMock()
    hass.states.async_set(entry.entity_id, "unavailable")
    with patch.object(hass.services, "async_call", call):
        unavailable = await executor.async_execute(
            "unavailable-id", entry.id, {"operation": "power_on"}
        )
        missing = await executor.async_execute(
            "missing-id", "missing-registry-id", {"operation": "power_on"}
        )
    assert unavailable.status == "unavailable"
    assert missing.status == "target_not_found"
    call.assert_not_awaited()

    registry = er.async_get(hass)
    registry.async_update_entity(entry.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    disabled = await executor.async_execute("disabled-id", entry.id, {"operation": "power_on"})
    assert disabled.status == "target_not_found"


async def test_duplicate_id_rename_timeout_and_failure_mapping(hass: HomeAssistant) -> None:
    entry, executor = exposed_entity(hass, "switch")
    registry = er.async_get(hass)
    registry.async_update_entity(entry.entity_id, new_entity_id="switch.renamed")
    hass.states.async_set("switch.renamed", "on")
    call = AsyncMock()
    with patch.object(hass.services, "async_call", call):
        first = await executor.async_execute("same-id", entry.id, {"operation": "power_off"})
        replay = await executor.async_execute("same-id", entry.id, {"operation": "power_off"})
        conflict = await executor.async_execute("same-id", entry.id, {"operation": "power_on"})
    assert first.status == replay.status == "success"
    assert conflict.status == "duplicate"
    call.assert_awaited_once()

    timeout_executor = EkonexVoiceCommandExecutor(
        hass, EntityInventorySynchronizer(hass, set(), {entry.id}, None), timeout=0.001
    )
    with patch.object(hass.services, "async_call", AsyncMock(side_effect=TimeoutError)):
        assert (
            await timeout_executor.async_execute("timeout", entry.id, {"operation": "power_off"})
        ).status == "timeout"
    with patch.object(hass.services, "async_call", AsyncMock(side_effect=RuntimeError("secret"))):
        assert (
            await executor.async_execute("failure", entry.id, {"operation": "power_off"})
        ).status == "execution_failed"


async def test_command_state_change_converges_through_m5_state_sync(
    hass: HomeAssistant,
) -> None:
    entry = er.async_get(hass).async_get_or_create("switch", "test", "state-convergence")
    hass.states.async_set(entry.entity_id, "on")
    inventory = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    websocket = AsyncMock()
    await inventory.async_start(websocket, "75a8dd73-7645-4e13-81c6-d90d75d8c261", cloud_revision=0)
    executor = EkonexVoiceCommandExecutor(hass, inventory)

    async def apply_state(*args: object, **kwargs: object) -> None:
        hass.states.async_set(entry.entity_id, "off")

    with patch.object(hass.services, "async_call", side_effect=apply_state):
        result = await executor.async_execute("state-command", entry.id, {"operation": "power_off"})
    await asyncio.sleep(0.3)
    messages = [call.args[0] for call in websocket.send_json.await_args_list]
    assert result.status == "success"
    assert any(
        message["type"] == "state_update" and message["payload"]["entities"][0]["state"] == "off"
        for message in messages
    )
    await inventory.async_stop()
