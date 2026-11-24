"""Wear and notification selectors must consume the single voice exposure scope."""

from pathlib import Path


def test_wear_uses_shared_voice_scope_for_commands_and_notifications() -> None:
    source = (Path(__file__).parents[1] / "app" / "wear_api.py").read_text(encoding="utf-8")

    assert source.count("voice_visible_clause(Entity)") >= 3
    assert "Entity.control_exposed.is_(True)" not in source
    notification = source[source.index("notification_entities = list(") :]
    assert "Entity.ha_registry_id.is_not(None)" in notification
    assert "voice_visible_clause(Entity)" in notification
