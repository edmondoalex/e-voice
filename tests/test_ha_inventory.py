"""M5 Home Assistant inventory exposure and normalization tests."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ekonex_voice.entity_inventory import EntityInventorySynchronizer, _chunks


def registered_light(hass: HomeAssistant) -> er.RegistryEntry:
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "light", "test", "stable-light-1", suggested_object_id="kitchen"
    )
    hass.states.async_set(
        entry.entity_id,
        "on",
        {"brightness": 120, "access_token": "never-share", "friendly_name": "Kitchen"},
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


async def test_ui_entity_selection_uses_stable_registry_id_and_allowlist(
    hass: HomeAssistant,
) -> None:
    entry = registered_light(hass)
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    item = sync._serialize(entry)
    assert item is not None
    assert item["registry_id"] == entry.id
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
    renamed = registry.async_get(entry.entity_id)
    item = EntityInventorySynchronizer(hass, set(), {entry.id}, None)._serialize(renamed)
    assert item is not None
    assert item["registry_id"] == entry.id
    assert item["friendly_name"] == "User configured name"


async def test_user_rename_replaces_original_name_in_next_inventory_full(
    hass: HomeAssistant,
) -> None:
    original_name = "BusPro Luci Luce Ufficio Alex"
    user_name = "Luce Ufficio Alex"
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "light",
        "test",
        "buspro-office-light",
        suggested_object_id="buspro_gateway_192_168_3_27_6000_luce_ufficio_alex",
        original_name=original_name,
    )
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": original_name})
    websocket = AsyncMock()
    sync = EntityInventorySynchronizer(hass, set(), {entry.id}, None)
    await sync.async_start(websocket, str(uuid4()), cloud_revision=0)
    websocket.send_json.reset_mock()

    registry.async_update_entity(entry.entity_id, name=user_name)
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": user_name})
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()

    full_messages = [
        call.args[0]
        for call in websocket.send_json.await_args_list
        if call.args[0]["type"] == "inventory_full"
    ]
    assert full_messages
    renamed = full_messages[-1]["payload"]["entities"][0]
    assert renamed["friendly_name"] == user_name
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
