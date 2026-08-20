"""Installation-scoped, idempotent M5 entity synchronization."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain.models import Entity, Installation
from .history import StateHistoryService

logger = logging.getLogger(__name__)


class StaleSyncError(Exception):
    """A duplicate or out-of-order synchronization message."""


class EntitySyncService:
    def __init__(self, session: AsyncSession, installation: Installation) -> None:
        self._session, self._installation = session, installation

    async def apply_full(self, revision: int, items: list[dict[str, object]]) -> None:
        if revision <= self._installation.sync_revision:
            raise StaleSyncError
        seen: set[str] = set()
        for item in items:
            seen.add(str(item["registry_id"]))
            await self._upsert(item)
        existing = await self._session.scalars(
            select(Entity).where(Entity.installation_id == self._installation.id)
        )
        now = datetime.now(UTC)
        tombstoned = 0
        for entity in existing:
            if entity.ha_registry_id not in seen and entity.deleted_at is None:
                previous_state, previous_available = entity.state, entity.available
                entity.deleted_at = now
                entity.available = False
                await StateHistoryService(self._session).record_change(
                    entity,
                    tenant_id=self._installation.tenant_id,
                    previous_state=previous_state,
                    previous_available=previous_available,
                )
                tombstoned += 1
        self._installation.sync_revision = revision
        self._installation.inventory_synced_at = now
        await self._session.commit()
        logger.debug(
            "Full entity inventory committed: revision=%d active=%d tombstoned=%d",
            revision,
            len(seen),
            tombstoned,
        )
        from .alexa_events import reconcile_discovery_safely

        await reconcile_discovery_safely(self._session, self._installation)

    async def apply_delta(self, revision: int, items: list[dict[str, object]]) -> None:
        if revision != self._installation.sync_revision + 1:
            raise StaleSyncError
        for item in items:
            if bool(item.get("removed")):
                entity = await self._by_registry(str(item["registry_id"]))
                if entity is not None:
                    previous_state, previous_available = entity.state, entity.available
                    entity.deleted_at, entity.available = datetime.now(UTC), False
                    await StateHistoryService(self._session).record_change(
                        entity,
                        tenant_id=self._installation.tenant_id,
                        previous_state=previous_state,
                        previous_available=previous_available,
                    )
            else:
                await self._upsert(item)
        self._installation.sync_revision = revision
        await self._session.commit()
        from .alexa_events import reconcile_discovery_safely

        await reconcile_discovery_safely(self._session, self._installation)

    async def apply_state(self, revision: int, items: list[dict[str, object]]) -> None:
        if revision != self._installation.sync_revision + 1:
            raise StaleSyncError
        changed_entities: list[Entity] = []
        for item in items:
            entity = await self._by_registry(str(item["registry_id"]))
            if entity is None or entity.deleted_at is not None:
                raise StaleSyncError
            previous_state, previous_available = entity.state, entity.available
            entity.state = _optional(item, "state")
            entity.available = bool(item.get("available", True))
            attributes = item.get("attributes", {})
            entity.attributes_json = attributes if isinstance(attributes, dict) else {}
            entity.last_seen_at = datetime.now(UTC)
            await StateHistoryService(self._session).record_change(
                entity,
                tenant_id=self._installation.tenant_id,
                previous_state=previous_state,
                previous_available=previous_available,
            )
            changed_entities.append(entity)
        self._installation.sync_revision = revision
        await self._session.commit()
        from .alexa import SUPPORTED_DOMAINS
        from .alexa_events import AlexaEventGateway

        gateway = AlexaEventGateway(self._session)
        try:
            for entity in changed_entities:
                if entity.ha_domain in SUPPORTED_DOMAINS:
                    await gateway.report_entity(entity)
            await gateway.reconcile_discovery(self._installation)
        except Exception:  # External Alexa delivery must not fail committed entity state.
            await self._session.rollback()
            logger.exception(
                "Alexa proactive reporting failed installation_id=%s", self._installation.id
            )
        finally:
            await gateway.close()

    async def _by_registry(self, registry_id: str) -> Entity | None:
        result = await self._session.scalars(
            select(Entity).where(
                Entity.installation_id == self._installation.id,
                Entity.ha_registry_id == registry_id,
            )
        )
        return result.one_or_none()

    async def _upsert(self, item: dict[str, object]) -> Entity:
        entity = await self._by_registry(str(item["registry_id"]))
        if entity is None:
            result = await self._session.scalars(
                select(Entity).where(
                    Entity.installation_id == self._installation.id,
                    Entity.ha_entity_id == str(item["entity_id"]),
                )
            )
            entity = result.one_or_none()
        is_new = entity is None
        if entity is None:
            entity = Entity(
                installation_id=self._installation.id,
                ha_registry_id=str(item["registry_id"]),
                ha_entity_id=str(item["entity_id"]),
                ha_domain=str(item["domain"]),
            )
            self._session.add(entity)
            await self._session.flush()
        previous_state, previous_available = entity.state, entity.available
        entity.ha_registry_id = str(item["registry_id"])
        entity.ha_entity_id = str(item["entity_id"])
        entity.ha_domain = str(item["domain"])
        entity.friendly_name = _optional(item, "friendly_name")
        entity.area_id, entity.area_name = _optional(item, "area_id"), _optional(item, "area_name")
        entity.device_id, entity.device_name = (
            _optional(item, "device_id"),
            _optional(item, "device_name"),
        )
        entity.device_class = _optional(item, "device_class")
        supported_features = item.get("supported_features", 0)
        entity.supported_features = supported_features if isinstance(supported_features, int) else 0
        entity.state = _optional(item, "state")
        entity.available = bool(item.get("available", True))
        attributes = item.get("attributes", {})
        entity.attributes_json = attributes if isinstance(attributes, dict) else {}
        changed = item.get("last_changed_at")
        entity.last_changed_at = (
            datetime.fromisoformat(str(changed).replace("Z", "+00:00")) if changed else None
        )
        entity.last_seen_at, entity.deleted_at = datetime.now(UTC), None
        if is_new:
            previous_state, previous_available = None, not entity.available
        await StateHistoryService(self._session).record_change(
            entity,
            tenant_id=self._installation.tenant_id,
            previous_state=previous_state,
            previous_available=previous_available,
        )
        return entity


def _optional(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    return str(value)[:255] if value is not None else None
