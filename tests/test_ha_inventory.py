"""M5 Home Assistant inventory exposure and normalization tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from aiohttp import ClientConnectionResetError
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ekonex_voice.entity_inventory import (
    _LOGGER,
    EntityInventorySynchronizer,
    _chunks,
)


def registered_light(hass: HomeAssistant) -> er.RegistryEntry:
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "light", "test", "stable-light-1", suggested_object_id="kitchen"
    )
    hass.states.async_set(
        entry.entity_id,
        "on",
        {
            "brightness": 120,
            "access_token": "never-share",
            "friendly_name": "Kitchen",
            "icon": "mdi:ceiling-light",
        },
    )
    return entry


def registered_device_entities(
    hass: HomeAssistant,
) -> tuple[dr.DeviceEntry, er.RegistryEntry, er.RegistryEntry]:
    config_entry = MockConfigEntry(domain="test")
    config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("test", "device-1")},
        name="Kitchen device",
    )
    registry = er.async_get(hass)
    light = registry.async_get_or_create("light", "test", "device-light", device_id=device.id)
    sensor = registry.async_get_or_create("sensor", "test", "device-sensor", device_id=device.id)
    hass.states.async_set(light.entity_id, "on")
    hass.states.async_set(sensor.entity_id, "42")
    return device, light, sensor


async def test_fresh_integration_exposes_nothing(hass: HomeAssistant) -> None:
    entry = registered_light(hass)
    sync = EntityInventorySynchronizer(hass, set(), set(), None)
    assert sync._serialize(entry) is None


async def test_initial_inventory_sync_sends_selected_entities(hass: HomeAssistant) -> None:
    entry = registered_light(hass)
    websocket = AsyncMock()
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)

    await sync.async_start(websocket, str(uuid4()), cloud_revision=0)

    message = websocket.send_json.await_args_list[0].args[0]
    assert message["type"] == "inventory_full"
    assert message["payload"]["revision"] == 1
    assert [item["registry_id"] for item in message["payload"]["entities"]] == [entry.id]
    assert sync.last_full_entity_count == 1
    await sync.async_stop()


async def test_climate_inventory_includes_current_temperature(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "climate", "test", "stable-climate-1", suggested_object_id="office"
    )
    hass.states.async_set(
        entry.entity_id,
        "heat",
        {
            "temperature": 21.5,
            "current_temperature": 20.25,
            "hvac_modes": ["off", "heat"],
            "access_token": "never-share",
        },
    )
    websocket = AsyncMock()
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)

    await sync.async_start(websocket, str(uuid4()), cloud_revision=0)

    message = websocket.send_json.await_args_list[0].args[0]
    [item] = message["payload"]["entities"]
    assert item["attributes"] == {
        "current_temperature": 20.25,
        "hvac_modes": ["off", "heat"],
        "temperature": 21.5,
    }
    assert "access_token" not in item["attributes"]
    await sync.async_stop()


async def test_cover_inventory_preserves_current_position(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "cover", "test", "stable-cover-position", suggested_object_id="office"
    )
    hass.states.async_set(
        entry.entity_id,
        "open",
        {"current_position": 37, "supported_features": 15},
    )
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)

    item = sync._serialize(entry)

    assert item is not None
    assert item["attributes"] == {"current_position": 37}


async def test_zero_selected_entities_sends_explicit_empty_snapshot(
    hass: HomeAssistant,
) -> None:
    registered_light(hass)
    websocket = AsyncMock()
    sync = EntityInventorySynchronizer(hass, set(), set(), None)

    await sync.async_start(websocket, str(uuid4()), cloud_revision=4)

    message = websocket.send_json.await_args_list[0].args[0]
    assert message["type"] == "inventory_full"
    assert message["payload"]["revision"] == 5
    assert message["payload"]["entities"] == []
    assert sync.last_full_entity_count == 0
    await sync.async_stop()


async def test_reconnect_sends_full_resync_from_cloud_revision(hass: HomeAssistant) -> None:
    entry = registered_light(hass)
    first, second = AsyncMock(), AsyncMock()
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)

    await sync.async_start(first, str(uuid4()), cloud_revision=0)
    await sync.async_start(second, str(uuid4()), cloud_revision=1)

    assert first.send_json.await_args.args[0]["payload"]["revision"] == 1
    assert second.send_json.await_args.args[0]["type"] == "inventory_full"
    assert second.send_json.await_args.args[0]["payload"]["revision"] == 2
    await sync.async_stop()


async def test_concurrent_full_and_state_update_send_monotonic_revisions(
    hass: HomeAssistant,
) -> None:
    entry = registered_light(hass)
    websocket = AsyncMock()
    first_send_entered, release_first_send = asyncio.Event(), asyncio.Event()

    async def ordered_send(message: dict[str, object]) -> None:
        if not first_send_entered.is_set():
            first_send_entered.set()
            await release_first_send.wait()

    websocket.send_json.side_effect = ordered_send
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    sync._websocket, sync._session_id = websocket, str(uuid4())
    item = sync._serialize(entry)
    assert item is not None
    full = asyncio.create_task(sync._send_full())
    await first_send_entered.wait()
    state = asyncio.create_task(sync._send("state_update", [item]))
    await asyncio.sleep(0)

    assert websocket.send_json.await_count == 1
    release_first_send.set()
    await full
    await state
    revisions = [
        call.args[0]["payload"]["revision"] for call in websocket.send_json.await_args_list
    ]
    assert revisions == [1, 2]


async def test_closed_socket_is_never_used_for_inventory_send(hass: HomeAssistant) -> None:
    entry = registered_light(hass)
    websocket = AsyncMock()
    websocket.closed = True
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    sync._websocket, sync._session_id = websocket, str(uuid4())

    await sync._send_full()

    websocket.send_json.assert_not_awaited()


async def test_socket_closing_during_inventory_and_state_sends_is_contained(
    hass: HomeAssistant,
) -> None:
    entry = registered_light(hass)
    websocket = AsyncMock()
    websocket.closed = False

    async def close_during_send(message: dict[str, object]) -> None:
        websocket.closed = True
        raise ClientConnectionResetError("Cannot write to closing transport")

    websocket.send_json.side_effect = close_during_send
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    sync._websocket, sync._session_id = websocket, str(uuid4())

    await sync._send_full()
    websocket.closed = False
    item = sync._serialize(entry)
    assert item is not None
    await sync._send("state_update", [item])

    assert sync.send_failure_count == 2
    assert sync.last_error_code == "transport_closing"


async def test_reconnect_cancels_delayed_full_before_using_new_socket(
    hass: HomeAssistant,
) -> None:
    entry = registered_light(hass)
    first, second = AsyncMock(), AsyncMock()
    first.closed = second.closed = False
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    await sync.async_start(first, str(uuid4()), cloud_revision=0)
    first.send_json.reset_mock()
    sync._registry_changed(MagicMock())

    await sync.async_start(second, str(uuid4()), cloud_revision=1)
    await asyncio.sleep(0.3)

    first.send_json.assert_not_awaited()
    assert second.send_json.await_count == 1
    assert second.send_json.await_args.args[0]["type"] == "inventory_full"
    await sync.async_stop()


async def test_stop_cancels_pending_state_flush_without_task_exception(
    hass: HomeAssistant,
) -> None:
    entry = registered_light(hass)
    websocket = AsyncMock()
    websocket.closed = False
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    await sync.async_start(websocket, str(uuid4()), cloud_revision=0)
    websocket.send_json.reset_mock()
    sync._state_changed(MagicMock(data={"entity_id": entry.entity_id}))

    await sync.async_stop()
    await asyncio.sleep(0.3)

    websocket.send_json.assert_not_awaited()
    assert sync._flush_task is None
    assert sync._resync_task is None


async def test_state_update_preserves_unavailable_semantics(hass: HomeAssistant) -> None:
    entry = registered_light(hass)
    websocket = AsyncMock()
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    sync._websocket, sync._session_id = websocket, str(uuid4())
    hass.states.async_set(entry.entity_id, "unavailable")
    sync._pending.add(entry.entity_id)

    await sync._flush_states()

    message = websocket.send_json.await_args.args[0]
    assert message["type"] == "state_update"
    assert message["payload"]["entities"][0]["available"] is False
    assert message["payload"]["entities"][0]["state"] is None


async def test_unknown_assumed_state_cover_remains_available(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("cover", "test", "stateless-cover")
    hass.states.async_set(
        entry.entity_id,
        "unknown",
        {"assumed_state": True, "is_closed": None, "supported_features": 11},
    )
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)

    item = sync._serialize(entry)

    assert item is not None
    assert item["state"] is None
    assert item["available"] is True
    assert item["supported_features"] == 11


def _registered_cover_with_device_classes(
    hass: HomeAssistant,
    *,
    original: str | None = None,
    override: str | None = None,
    state_value: str | None = None,
) -> er.RegistryEntry:
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "cover",
        "test",
        f"device-class-{uuid4()}",
        original_device_class=original,
    )
    if override is not None:
        updated = registry.async_update_entity(entry.entity_id, device_class=override)
        assert updated is not None
        entry = updated
    attributes = {"device_class": state_value} if state_value is not None else {}
    hass.states.async_set(entry.entity_id, "closed", attributes)
    return entry


async def test_inventory_device_class_prefers_user_registry_override(
    hass: HomeAssistant,
) -> None:
    entry = _registered_cover_with_device_classes(
        hass, original="shutter", override="blind", state_value="curtain"
    )

    item = EntityInventorySynchronizer(hass, set(), {entry.id}, None)._serialize(entry)

    assert item is not None
    assert item["device_class"] == "blind"


async def test_inventory_device_class_falls_back_to_registry_original(
    hass: HomeAssistant,
) -> None:
    entry = _registered_cover_with_device_classes(hass, original="shutter", state_value="curtain")

    item = EntityInventorySynchronizer(hass, set(), {entry.id}, None)._serialize(entry)

    assert item is not None
    assert item["device_class"] == "shutter"


async def test_inventory_device_class_falls_back_to_state_attribute(
    hass: HomeAssistant,
) -> None:
    entry = _registered_cover_with_device_classes(hass, state_value="curtain")

    item = EntityInventorySynchronizer(hass, set(), {entry.id}, None)._serialize(entry)

    assert item is not None
    assert item["device_class"] == "curtain"


async def test_inventory_device_class_remains_null_when_home_assistant_has_none(
    hass: HomeAssistant,
) -> None:
    entry = _registered_cover_with_device_classes(hass)

    item = EntityInventorySynchronizer(hass, set(), {entry.id}, None)._serialize(entry)

    assert item is not None
    assert item["device_class"] is None


async def test_inventory_device_class_diagnostic_is_allowlisted(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _registered_cover_with_device_classes(
        hass, original="shutter", override="blind", state_value="curtain"
    )
    diagnostic = MagicMock()
    monkeypatch.setattr(_LOGGER, "debug", diagnostic)

    EntityInventorySynchronizer(hass, set(), {entry.id}, None)._serialize(entry)

    diagnostic.assert_called_once_with(
        "entity_device_class_resolved %s",
        {
            "registry_device_class": "blind",
            "registry_original_device_class": "shutter",
            "state_device_class": "curtain",
            "resolved_device_class": "blind",
            "device_class_source": "registry_override",
        },
    )


async def test_ui_entity_selection_uses_stable_registry_id_and_allowlist(
    hass: HomeAssistant,
) -> None:
    entry = registered_light(hass)
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    item = sync._serialize(entry)
    assert item is not None
    assert item["registry_id"] == entry.id
    assert item["icon"] == "mdi:ceiling-light"
    assert item["attributes"] == {"brightness": 120}


async def test_label_id_authorizes_entity_independent_of_label_name(hass: HomeAssistant) -> None:
    entry = registered_light(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(entry.entity_id, labels={"stable-ekonex-label-id"})
    updated = registry.async_get(entry.entity_id)
    sync = EntityInventorySynchronizer(hass, set(), set(), "stable-ekonex-label-id")
    assert sync._serialize(updated) is not None


async def test_device_selection_does_not_reject_an_installer_selected_domain(
    hass: HomeAssistant,
) -> None:
    device, light, sensor = registered_device_entities(hass)
    sync = EntityInventorySynchronizer(hass, {device.id}, set(), None)
    assert sync._serialize(light) is not None
    sensor_item = sync._serialize(sensor)
    assert sensor_item is not None
    assert sensor_item["domain"] == "sensor"
    assert sensor_item["attributes"] == {}


async def test_ui_and_label_sources_have_union_semantics(hass: HomeAssistant) -> None:
    entry = registered_light(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(entry.entity_id, labels={"stable-label-id"})
    updated = registry.async_get(entry.entity_id)
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, "stable-label-id")
    sync._registry_ids.clear()
    assert sync._serialize(updated) is not None
    registry.async_update_entity(entry.entity_id, labels=set())
    assert sync._serialize(registry.async_get(entry.entity_id)) is None


async def test_registry_rename_preserves_stable_ui_selection(hass: HomeAssistant) -> None:
    entry = registered_light(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(entry.entity_id, new_entity_id="light.renamed_kitchen")
    hass.states.async_set("light.renamed_kitchen", "on")
    renamed = registry.async_get("light.renamed_kitchen")
    assert renamed is not None and renamed.id == entry.id
    assert EntityInventorySynchronizer(hass, set(), {entry.id}, None)._serialize(renamed)


async def test_current_user_configured_name_is_mutable_metadata(hass: HomeAssistant) -> None:
    entry = registered_light(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(entry.entity_id, name="User configured name")
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": "User configured name"})
    renamed = registry.async_get(entry.entity_id)
    item = EntityInventorySynchronizer(hass, set(), {entry.id}, None)._serialize(renamed)
    assert item is not None
    assert item["registry_id"] == entry.id
    assert item["friendly_name"] == "User configured name"


async def test_user_rename_replaces_original_name_in_next_inventory_full(
    hass: HomeAssistant,
) -> None:
    original_name = "BusPro Luci Luce Ufficio Alex"
    current_name = "Luce Ufficio Alex"
    renamed_name = "Luce Ufficio Alex evoice"
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "light",
        "test",
        "buspro-office-light",
        suggested_object_id="buspro_gateway_192_168_3_27_6000_luce_ufficio_alex",
        original_name=original_name,
    )
    registry.async_update_entity(entry.entity_id, name=current_name)
    entry = registry.async_get(entry.entity_id)
    assert entry is not None
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": current_name})
    websocket = AsyncMock()
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    await sync.async_start(websocket, str(uuid4()), cloud_revision=0)
    websocket.send_json.reset_mock()

    hass.states.async_set(entry.entity_id, "on", {"friendly_name": renamed_name})
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert getattr(entry, "name_by_user", None) is None
    assert entry.name == current_name
    assert entry.original_name == original_name
    assert state.attributes["friendly_name"] == renamed_name
    assert state.name == renamed_name
    await sync._send_full()

    full_messages = [
        call.args[0]
        for call in websocket.send_json.await_args_list
        if call.args[0]["type"] == "inventory_full"
    ]
    assert full_messages
    renamed = full_messages[-1]["payload"]["entities"][0]
    assert renamed["friendly_name"] == renamed_name
    assert renamed["entity_id"] == entry.entity_id
    assert renamed["registry_id"] == entry.id
    await sync.async_stop()


async def test_removing_final_ui_and_label_authorization_emits_empty_reconciliation(
    hass: HomeAssistant,
) -> None:
    entry = registered_light(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(entry.entity_id, labels={"stable-label-id"})
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, "stable-label-id")
    websocket = AsyncMock()
    sync._websocket = websocket
    sync._session_id = str(uuid4())

    await sync._send_full()
    authorized = websocket.send_json.await_args_list[-1].args[0]["payload"]["entities"]
    assert len(authorized) == 1
    assert authorized[0]["registry_id"] == entry.id

    sync._registry_ids.clear()
    registry.async_update_entity(entry.entity_id, labels=set())
    await sync._send_full()
    reconciled = websocket.send_json.await_args_list[-1].args[0]["payload"]["entities"]
    assert reconciled == []


def test_large_inventory_chunking_is_deterministic_and_bounded() -> None:
    items = [{"registry_id": str(index), "name": "x" * 1000} for index in range(150)]
    first = _chunks(items)
    assert first == _chunks(items)
    assert len(first) > 1
