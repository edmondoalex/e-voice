"""Tests for the authenticated local e-Face Media API."""

from types import SimpleNamespace

from homeassistant.components.media_player import MediaPlayerEntityFeature

from custom_components.ekonex_voice.local_media_api import _player_payload, _siblings


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
