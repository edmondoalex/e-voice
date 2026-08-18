"""M5 Home Assistant inventory exposure and normalization tests."""

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


async def test_device_selection_exposes_only_supported_entities(hass: HomeAssistant) -> None:
    device, light, sensor = registered_device_entities(hass)
    sync = EntityInventorySynchronizer(hass, {device.id}, set(), None)
    assert sync._serialize(light) is not None
    assert sync._serialize(sensor) is None


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


def test_large_inventory_chunking_is_deterministic_and_bounded() -> None:
    items = [{"registry_id": str(index), "name": "x" * 1000} for index in range(150)]
    first = _chunks(items)
    assert first == _chunks(items)
    assert len(first) > 1
