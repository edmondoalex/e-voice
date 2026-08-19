"""Entity display and voice naming policy tests."""

from uuid import uuid4

import pytest

from apps.cloud_api.app.domain.models import Entity
from apps.cloud_api.app.entity_names import (
    all_voice_names,
    clean_voice_aliases,
    effective_display_name,
    effective_voice_name,
    unambiguous_voice_entities,
    voice_collisions,
)


def entity(name: str, **overrides: object) -> Entity:
    values = {
        "id": uuid4(),
        "installation_id": uuid4(),
        "ha_entity_id": f"light.{name.casefold().replace(' ', '_')}",
        "ha_registry_id": str(uuid4()),
        "ha_domain": "light",
        "friendly_name": name,
        "voice_aliases": [],
    }
    values.update(overrides)
    return Entity(**values)


def test_display_and_voice_fallbacks_and_alias_priority() -> None:
    item = entity("Luce Ufficio Alex")
    assert effective_display_name(item) == "Luce Ufficio Alex"
    assert effective_voice_name(item) == "Luce Ufficio Alex"
    item.display_name = "Ufficio Alex"
    assert effective_display_name(item) == "Ufficio Alex"
    assert effective_voice_name(item) == "Ufficio Alex"
    item.voice_name = "luce ufficio"
    item.voice_aliases = ["ufficio", "luce alex"]
    assert effective_voice_name(item) == "luce ufficio"
    assert all_voice_names(item) == [
        "luce ufficio",
        "ufficio",
        "luce alex",
        "Ufficio Alex",
        "Luce Ufficio Alex",
    ]


def test_aliases_are_trimmed_and_deduplicated_case_insensitively() -> None:
    assert clean_voice_aliases([" ufficio ", "UFFICIO", "", "luce   alex"]) == [
        "ufficio",
        "luce alex",
    ]
    with pytest.raises(ValueError):
        clean_voice_aliases(["x" * 121])


def test_voice_collisions_fail_closed_for_every_ambiguous_entity() -> None:
    installation_id = uuid4()
    first = entity("Kitchen", installation_id=installation_id, voice_name="ufficio")
    second = entity("Desk", installation_id=installation_id, voice_aliases=["UFFICIO"])
    third = entity("Hall", installation_id=installation_id)
    assert voice_collisions([first, second, third]) == {"ufficio": {first.id, second.id}}
    assert unambiguous_voice_entities([first, second, third]) == [third]
