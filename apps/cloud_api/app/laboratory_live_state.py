"""Read-only live-state overlay for the isolated laboratory database."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm.attributes import set_committed_value

from .config import get_settings
from .domain.models import Entity, Installation

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveEntityState:
    ha_entity_id: str
    ha_registry_id: str | None
    ha_domain: str
    icon: str | None
    friendly_name: str | None
    area_id: str | None
    area_name: str | None
    device_id: str | None
    device_name: str | None
    state: str | None
    available: bool
    attributes: dict[str, Any]
    device_class: str | None
    supported_features: int
    last_changed_at: datetime | None
    last_seen_at: datetime | None


@dataclass(frozen=True)
class LiveInstallationState:
    status: str
    connector_version: str | None
    ha_version: str | None
    connector_protocol_version: int | None
    connector_capabilities: dict[str, bool] | None
    compatibility_status: str | None
    compatibility_reason: str | None
    ha_installation_type: str | None
    last_seen_at: datetime | None
    sync_revision: int
    inventory_synced_at: datetime | None


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
                    SELECT e.ha_entity_id, e.ha_registry_id, e.ha_domain, e.icon,
                           e.friendly_name, e.area_id, e.area_name, e.device_id,
                           e.device_name, e.state, e.available, e.attributes_json,
                           e.device_class, e.supported_features, e.last_changed_at,
                           e.last_seen_at
                    FROM entities AS e
                    JOIN installations AS i ON i.id = e.installation_id
                    WHERE i.public_id = :public_id AND e.deleted_at IS NULL
                    """
                ),
                {"public_id": installation_public_id},
            )
            return {
                row.ha_entity_id: LiveEntityState(
                    ha_entity_id=row.ha_entity_id,
                    ha_registry_id=row.ha_registry_id,
                    ha_domain=row.ha_domain,
                    icon=row.icon,
                    friendly_name=row.friendly_name,
                    area_id=row.area_id,
                    area_name=row.area_name,
                    device_id=row.device_id,
                    device_name=row.device_name,
                    state=row.state,
                    available=bool(row.available),
                    attributes=row.attributes_json
                    if isinstance(row.attributes_json, dict)
                    else {},
                    device_class=row.device_class,
                    supported_features=int(row.supported_features or 0),
                    last_changed_at=row.last_changed_at,
                    last_seen_at=row.last_seen_at,
                )
                for row in result
            }
    except Exception:
        LOGGER.warning("Laboratory live-state overlay unavailable", exc_info=True)
        return {}


async def sync_live_entities(session: AsyncSession, installation: Installation) -> int:
    """Import production inventory into the isolated laboratory database."""
    states = await load_live_states(installation.public_id)
    if not states:
        return 0
    local_entities = list(
        (
            await session.scalars(
                select(Entity).where(Entity.installation_id == installation.id)
            )
        ).all()
    )
    by_entity_id = {entity.ha_entity_id: entity for entity in local_entities}
    created = 0
    for live in states.values():
        entity = by_entity_id.get(live.ha_entity_id)
        if entity is None:
            entity = Entity(
                installation_id=installation.id,
                ha_entity_id=live.ha_entity_id,
                ha_domain=live.ha_domain,
                voice_aliases=[],
            )
            session.add(entity)
            created += 1
        for field, value in (
            ("ha_registry_id", live.ha_registry_id),
            ("ha_domain", live.ha_domain),
            ("icon", live.icon),
            ("friendly_name", live.friendly_name),
            ("area_id", live.area_id),
            ("area_name", live.area_name),
            ("device_id", live.device_id),
            ("device_name", live.device_name),
            ("device_class", live.device_class),
            ("supported_features", live.supported_features),
            ("state", live.state),
            ("available", live.available),
            ("attributes_json", live.attributes),
            ("last_changed_at", live.last_changed_at),
            ("last_seen_at", live.last_seen_at),
            ("deleted_at", None),
        ):
            setattr(entity, field, value)
    await session.commit()
    return created


async def load_live_installation(
    installation_public_id: str,
) -> LiveInstallationState | None:
    """Return current production connection metadata for a laboratory installation."""
    settings = get_settings()
    database_url = settings.laboratory_live_database_url.strip()
    if settings.environment != "laboratory" or not database_url or not installation_public_id:
        return None
    try:
        async with _engine(database_url).connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT status, connector_version, ha_version,
                               connector_protocol_version, connector_capabilities_json,
                               connector_compatibility_status,
                               connector_compatibility_reason, ha_installation_type,
                               last_seen_at, sync_revision, inventory_synced_at
                        FROM installations
                        WHERE public_id = :public_id AND revoked_at IS NULL
                        """
                    ),
                    {"public_id": installation_public_id},
                )
            ).first()
            if row is None:
                return None
            return LiveInstallationState(
                status=str(row.status),
                connector_version=row.connector_version,
                ha_version=row.ha_version,
                connector_protocol_version=row.connector_protocol_version,
                connector_capabilities=row.connector_capabilities_json,
                compatibility_status=row.connector_compatibility_status,
                compatibility_reason=row.connector_compatibility_reason,
                ha_installation_type=row.ha_installation_type,
                last_seen_at=row.last_seen_at,
                sync_revision=int(row.sync_revision or 0),
                inventory_synced_at=row.inventory_synced_at,
            )
    except Exception:
        LOGGER.warning("Laboratory live installation overlay unavailable", exc_info=True)
        return None


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


def overlay_installation(
    installation: Installation, live: LiveInstallationState | None
) -> None:
    """Overlay connection metadata without modifying the laboratory database."""
    if live is None:
        return
    values = {
        "status": live.status,
        "connector_version": live.connector_version,
        "ha_version": live.ha_version,
        "connector_protocol_version": live.connector_protocol_version,
        "connector_capabilities_json": live.connector_capabilities,
        "connector_compatibility_status": live.compatibility_status,
        "connector_compatibility_reason": live.compatibility_reason,
        "ha_installation_type": live.ha_installation_type,
        "last_seen_at": live.last_seen_at,
        "sync_revision": live.sync_revision,
        "inventory_synced_at": live.inventory_synced_at,
    }
    for field, value in values.items():
        set_committed_value(installation, field, value)
