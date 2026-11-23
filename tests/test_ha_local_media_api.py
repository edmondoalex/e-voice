"""Tests for the authenticated local e-Face Media API."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.ekonex_voice.local_media_api import (
    _exposed_items,
    _media_event_entity_ids,
    _player_payload,
    _siblings,
)


def echo_items() -> tuple[dict[str, object], list[dict[str, object]]]:
    player: dict[str, object] = {
        "registry_id": "echo-registry",
        "entity_id": "media_player.echo_sala",
        "domain": "media_player",
        "friendly_name": "Echo Sala",
        "device_id": "echo-device",
        "manufacturer": "Amazon",
        "model": "Echo Dot",
        "platform": "alexa_devices",
        "available": True,
        "state": "playing",
        "experiences": ["listen"],
        "supported_features": int(
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
        ),
        "attributes": {"volume_level": 0.42, "is_volume_muted": False},
    }
    related = [
        player,
        {
            "registry_id": "speak-registry",
            "entity_id": "notify.echo_sala_speak",
            "domain": "notify",
            "device_id": "echo-device",
            "available": True,
        },
        {
            "registry_id": "dnd-registry",
            "entity_id": "switch.echo_sala_do_not_disturb",
            "domain": "switch",
            "device_id": "echo-device",
            "available": True,
            "state": "off",
        },
    ]
    return player, related


def test_local_echo_payload_has_real_capabilities_and_stable_room() -> None:
    player, related = echo_items()
    entry = SimpleNamespace(data={"installation_id": "installation-one"})

    payload = _player_payload(entry, player, related)

    assert payload["registry_id"] == "echo-registry"
    assert payload["room_id"] == "echo-registry"
    assert payload["room_name"] == "Echo Sala"
    assert payload["device_class"] == "echo"
    assert payload["manufacturer"] == "Amazon"
    assert payload["model"] == "Echo Dot"
    assert payload["capabilities"]["tts"] is True  # type: ignore[index]
    assert payload["capabilities"]["do_not_disturb"] is True  # type: ignore[index]
    assert payload["dnd"] is False


def test_echo_capabilities_are_false_without_exposed_siblings() -> None:
    player, _ = echo_items()
    speech, dnd, is_echo = _siblings(player, [player])
    payload = _player_payload(
        SimpleNamespace(data={"installation_id": "installation-one"}), player, [player]
    )

    assert is_echo is True
    assert speech is None and dnd is None
    assert payload["capabilities"]["tts"] is False  # type: ignore[index]
    assert payload["capabilities"]["do_not_disturb"] is False  # type: ignore[index]


def test_media_events_ignore_unrelated_exposed_entities() -> None:
    player, related = echo_items()
    related.append(
        {
            "registry_id": "temperature-registry",
            "entity_id": "sensor.temperature_sala",
            "domain": "sensor",
            "device_id": "another-device",
            "available": True,
        }
    )
    players = {
        "echo-registry": (
            SimpleNamespace(data={"installation_id": "installation-one"}),
            player,
        )
    }

    assert _media_event_entity_ids(players, related) == {
        "media_player.echo_sala",
        "notify.echo_sala_speak",
        "switch.echo_sala_do_not_disturb",
    }


def test_local_player_uses_only_config_entry_that_exposes_it(hass: HomeAssistant) -> None:
    registry_entry = er.async_get(hass).async_get_or_create(
        "media_player", "alexa_devices", "echo-office", suggested_object_id="echo_office"
    )
    hass.states.async_set(registry_entry.entity_id, "idle")
    item = {
        "registry_id": registry_entry.id,
        "entity_id": registry_entry.entity_id,
        "domain": "media_player",
        "available": True,
    }
    production_inventory = MagicMock()
    production_inventory._serialize.return_value = item
    production_inventory.is_exposed.return_value = False
    laboratory_inventory = MagicMock()
    laboratory_inventory._serialize.return_value = item
    laboratory_inventory.is_exposed.return_value = True
    production = SimpleNamespace(
        runtime_data=SimpleNamespace(inventory=production_inventory), data={}
    )
    laboratory = SimpleNamespace(
        runtime_data=SimpleNamespace(inventory=laboratory_inventory), data={}
    )

    with patch(
        "custom_components.ekonex_voice.local_media_api._loaded_entries",
        return_value=[production, laboratory],
    ):
        players, _ = _exposed_items(hass)

    assert players[registry_entry.id][0] is laboratory
    production_inventory.is_exposed.assert_called_with(registry_entry)
    laboratory_inventory.is_exposed.assert_called_with(registry_entry)
