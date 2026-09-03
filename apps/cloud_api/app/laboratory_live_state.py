"""Read-only live-state overlay for the isolated laboratory database."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm.attributes import set_committed_value

from .config import get_settings
from .domain.models import Entity

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveEntityState:
    state: str | None
    available: bool
    attributes: dict[str, Any]
    device_class: str | None
    last_changed_at: datetime | None
    last_seen_at: datetime | None


@lru_cache(maxsize=2)
def _engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


async def load_live_states(installation_public_id: str) -> dict[str, LiveEntityState]:
    """Return production states when the laboratory overlay is configured."""
    settings = get_settings()
    database_url = settings.laboratory_live_database_url.strip()
    if settings.environment != "laboratory" or not database_url or not installation_public_id:
        return {}
    try:
        async with _engine(database_url).connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT e.ha_entity_id, e.state, e.available, e.attributes_json,
                           e.device_class, e.last_changed_at, e.last_seen_at
                    FROM entities AS e
                    JOIN installations AS i ON i.id = e.installation_id
                    WHERE i.public_id = :public_id AND e.deleted_at IS NULL
                    """
                ),
                {"public_id": installation_public_id},
            )
            return {
                row.ha_entity_id: LiveEntityState(
                    state=row.state,
                    available=bool(row.available),
                    attributes=row.attributes_json
                    if isinstance(row.attributes_json, dict)
                    else {},
                    device_class=row.device_class,
                    last_changed_at=row.last_changed_at,
                    last_seen_at=row.last_seen_at,
                )
                for row in result
            }
    except Exception:
        LOGGER.warning("Laboratory live-state overlay unavailable", exc_info=True)
        return {}


def overlay_entities(entities: Iterable[Entity], states: dict[str, LiveEntityState]) -> None:
    """Overlay state fields without marking ORM rows as modified."""
    for entity in entities:
        live = states.get(entity.ha_entity_id)
        if live is None:
            continue
        for field, value in (
            ("state", live.state),
            ("available", live.available),
            ("attributes_json", live.attributes),
            ("device_class", live.device_class),
            ("last_changed_at", live.last_changed_at),
            ("last_seen_at", live.last_seen_at),
        ):
            set_committed_value(entity, field, value)
