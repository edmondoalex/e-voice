"""Media experience inventory tests."""

from homeassistant.core import HomeAssistant
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
