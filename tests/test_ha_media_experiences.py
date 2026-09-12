"""Media experience inventory tests."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ekonex_voice.entity_inventory import EntityInventorySynchronizer


async def test_manual_media_experiences_are_serialized_in_state_updates(
    hass: HomeAssistant,
) -> None:
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("media_player", "test", "speaker")
    hass.states.async_set(entry.entity_id, "playing", {"supported_features": 1})
    inventory = EntityInventorySynchronizer(
        hass,
        set(),
        {entry.id},
        None,
        {entry.id: ["watch", "listen"]},
    )

    item = inventory._serialize(entry)

    assert item is not None
    assert item["experiences"] == ["watch", "listen"]


async def test_echo_device_metadata_is_serialized_without_renaming(
    hass: HomeAssistant,
) -> None:
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="test-entry",
        identifiers={("alexa_media", "echo-sala")},
        manufacturer="Amazon",
        model="Echo Dot",
    )
    entry = er.async_get(hass).async_get_or_create(
        "media_player", "alexa_media", "echo-sala", device_id=device.id
    )
    hass.states.async_set(entry.entity_id, "idle", {"friendly_name": "Echo Sala"})

    item = EntityInventorySynchronizer(
        hass, set(), {entry.id}, None, {entry.id: ["listen"]}
    )._serialize(entry)

    assert item is not None
    assert item["friendly_name"] == "Echo Sala"
    assert item["manufacturer"] == "Amazon"
    assert item["model"] == "Echo Dot"
    assert item["platform"] == "alexa_media"
