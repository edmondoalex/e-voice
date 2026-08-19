"""Centralized entity display and voice naming policy."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from uuid import UUID

from .domain.models import Entity

MAX_NAME_LENGTH = 120
MAX_VOICE_ALIASES = 20


def clean_optional_name(value: str) -> str | None:
    """Trim an optional custom name and enforce its storage boundary."""
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ValueError(f"Name exceeds {MAX_NAME_LENGTH} characters")
    return cleaned


def normalize_voice_name(value: str) -> str:
    """Normalize voice names for case-insensitive comparison and deduplication."""
    return " ".join(value.split()).casefold()


def clean_voice_aliases(values: Iterable[str]) -> list[str]:
    """Clean, bound and case-insensitively deduplicate voice aliases."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        alias = clean_optional_name(value)
        if alias is None:
            continue
        key = normalize_voice_name(alias)
        if key in seen:
            continue
        seen.add(key)
        result.append(alias)
        if len(result) > MAX_VOICE_ALIASES:
            raise ValueError(f"More than {MAX_VOICE_ALIASES} aliases")
    return result


def effective_display_name(entity: Entity) -> str:
    """Return the dashboard name, falling back to the synchronized e-Control name."""
    return entity.display_name or entity.friendly_name or entity.ha_entity_id


def effective_voice_name(entity: Entity) -> str:
    """Return the primary voice name using the documented fallback order."""
    return entity.voice_name or effective_display_name(entity)


def all_voice_names(entity: Entity) -> list[str]:
    """Return primary, aliases and fallbacks once, in voice-resolution priority order."""
    candidates = [
        entity.voice_name,
        *(entity.voice_aliases or []),
        entity.display_name,
        entity.friendly_name,
    ]
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        key = normalize_voice_name(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result or [entity.ha_entity_id]


def voice_collisions(entities: Sequence[Entity]) -> dict[str, set[UUID]]:
    """Return only ambiguous normalized names mapped to every matching entity."""
    owners: dict[str, set[UUID]] = defaultdict(set)
    for entity in entities:
        for name in all_voice_names(entity):
            owners[normalize_voice_name(name)].add(entity.id)
    return {name: ids for name, ids in owners.items() if len(ids) > 1}


def unambiguous_voice_entities(entities: Sequence[Entity]) -> list[Entity]:
    """Fail closed on collisions within an installation, never across installations."""
    by_installation: dict[UUID, list[Entity]] = defaultdict(list)
    for entity in entities:
        by_installation[entity.installation_id].append(entity)
    ambiguous_ids: set[UUID] = set()
    for installation_entities in by_installation.values():
        ambiguous_ids.update(
            entity_id
            for ids in voice_collisions(installation_entities).values()
            for entity_id in ids
        )
    return [entity for entity in entities if entity.id not in ambiguous_ids]
