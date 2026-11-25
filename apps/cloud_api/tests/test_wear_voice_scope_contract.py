"""Wear and notification selectors must consume the single voice exposure scope."""

from pathlib import Path


def test_wear_uses_shared_voice_scope_for_commands_and_notifications() -> None:
    source = (Path(__file__).parents[1] / "app" / "wear_api.py").read_text(encoding="utf-8")

    assert source.count("voice_visible_clause(Entity)") >= 3
    assert "Entity.control_exposed.is_(True)" not in source
    notification = source[source.index("notification_entities = list(") :]
    assert "Entity.ha_registry_id.is_not(None)" in notification
    assert "voice_visible_clause(Entity)" in notification


def test_wear_authorized_buttons_use_the_existing_press_command() -> None:
    source = (Path(__file__).parents[1] / "app" / "wear_api.py").read_text(encoding="utf-8")

    assert 'entity.ha_domain == "button" and requested == "power_on"' in source
    assert 'PressCommand(operation="press"), "press"' in source
    assert '("light", "switch", "button")' in source
    assert '"press": "attivato"' in source
