"""Installation-scoped, idempotent M5 entity synchronization."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
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
        previous_media_ids = set(
            (
                await self._session.scalars(
                    select(Entity.ha_registry_id).where(
                        Entity.installation_id == self._installation.id,
                        Entity.ha_domain == "media_player",
                        Entity.deleted_at.is_(None),
                    )
                )
            ).all()
        )
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
        from .media_realtime import media_events

        media_events.publish(
            self._installation.id,
            revision,
            "snapshot.required",
            {"reason": "inventory_replaced", "current_installation_revision": revision},
        )
        await self._publish_inventory_players(revision, previous_media_ids)

    async def apply_delta(self, revision: int, items: list[dict[str, object]]) -> None:
        if revision != self._installation.sync_revision + 1:
            raise StaleSyncError
        previous_media_ids = set(
            (
                await self._session.scalars(
                    select(Entity.ha_registry_id).where(
                        Entity.installation_id == self._installation.id,
                        Entity.ha_domain == "media_player",
                        Entity.deleted_at.is_(None),
                    )
                )
            ).all()
        )
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
        from .media_realtime import media_events

        media_events.publish(
            self._installation.id,
            revision,
            "snapshot.required",
            {"reason": "inventory_changed", "current_installation_revision": revision},
        )
        await self._publish_inventory_players(revision, previous_media_ids)

    async def _publish_inventory_players(
        self, revision: int, previous_media_ids: set[str | None]
    ) -> None:
        """Publish complete area-bound players after an inventory mutation."""
        from .media_api import _player
        from .media_realtime import media_events

        players = list(
            (
                await self._session.scalars(
                    select(Entity).where(
                        Entity.installation_id == self._installation.id,
                        Entity.ha_domain == "media_player",
                        Entity.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        for entity in players:
            if not (entity.attributes_json or {}).get("_experiences"):
                continue
            event_type = (
                "player.updated"
                if entity.ha_registry_id in previous_media_ids
                else "player.created"
            )
            payload: dict[str, object] = {"player": _player(entity, self._installation)}
            if event_type == "player.updated":
                payload.update(
                    {
                        "registry_id": entity.ha_registry_id,
                        "resource_revision": _resource_revision(entity),
                        "changed_fields": ["inventory", "room", "experiences"],
                    }
                )
            media_events.publish(self._installation.id, revision, event_type, payload)

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
            _store_device_metadata(entity, item)
            if entity.ha_domain == "media_player":
                entity.attributes_json["_experiences"] = _experiences(item)
            changed = item.get("last_changed_at")
            entity.last_changed_at = (
                datetime.fromisoformat(str(changed).replace("Z", "+00:00"))
                if changed
                else entity.last_changed_at
            )
            updated = item.get("last_updated_at")
            if updated:
                entity.attributes_json["_last_updated_at"] = str(updated)[:64]
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
        from .media_api import _enrich_echo_players, _groups, _player
        from .media_realtime import media_events

        media_players = list(
            (
                await self._session.scalars(
                    select(Entity).where(
                        Entity.installation_id == self._installation.id,
                        Entity.ha_domain == "media_player",
                        Entity.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        current_groups = _groups(media_players, self._installation.id)
        all_entities = list(
            (
                await self._session.scalars(
                    select(Entity).where(
                        Entity.installation_id == self._installation.id,
                        Entity.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        group_by_member = {
            member: group for group in current_groups for member in group["member_registry_ids"]
        }
        group_changed = False
        for entity in changed_entities:
            if entity.ha_domain == "media_player":
                serialized = _player(entity, self._installation)
                if not (entity.attributes_json or {}).get("_experiences"):
                    continue
                _enrich_echo_players([serialized], all_entities)
                serialized["group"] = group_by_member.get(entity.ha_registry_id)
                media_events.publish(
                    self._installation.id,
                    revision,
                    "player.updated",
                    {
                        "registry_id": entity.ha_registry_id,
                        "resource_revision": _resource_revision(entity),
                        "changed_fields": [
                            "state",
                            "attributes",
                            "availability",
                            "experiences",
                        ],
                        "player": serialized,
                    },
                )
                group_changed = group_changed or "group_members" in (entity.attributes_json or {})
            elif entity.ha_domain == "switch" and entity.ha_entity_id.endswith(
                ("_do_not_disturb", "_non_disturbare")
            ):
                for player in media_players:
                    if player.device_id != entity.device_id or not (
                        player.attributes_json or {}
                    ).get("_experiences"):
                        continue
                    serialized = _player(player, self._installation)
                    _enrich_echo_players([serialized], all_entities)
                    media_events.publish(
                        self._installation.id,
                        revision,
                        "player.updated",
                        {
                            "registry_id": player.ha_registry_id,
                            "resource_revision": _resource_revision(player),
                            "changed_fields": ["dnd"],
                            "player": serialized,
                        },
                    )
        if group_changed:
            for group in current_groups:
                media_events.publish(
                    self._installation.id,
                    revision,
                    "group.updated",
                    {
                        "group_id": group["group_id"],
                        "resource_revision": group["resource_revision"],
                        "changed_fields": ["member_registry_ids", "completeness"],
                        "group": group,
                    },
                )
        if get_settings().environment == "laboratory":
            from .voice_alerts import schedule_alert_evaluation

            schedule_alert_evaluation(
                self._installation.id, [entity.id for entity in changed_entities]
            )
            return
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
        entity.icon = _optional(item, "icon")
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
        _store_device_metadata(entity, item)
        if entity.ha_domain == "media_player":
            entity.attributes_json["_experiences"] = _experiences(item)
        changed = item.get("last_changed_at")
        entity.last_changed_at = (
            datetime.fromisoformat(str(changed).replace("Z", "+00:00")) if changed else None
        )
        updated = item.get("last_updated_at")
        if updated:
            entity.attributes_json["_last_updated_at"] = str(updated)[:64]
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


def _experiences(item: dict[str, object]) -> list[str]:
    values = item.get("experiences", [])
    if not isinstance(values, list):
        return []
    return [value for value in values if value in {"watch", "listen"}]


def _store_device_metadata(entity: Entity, item: dict[str, object]) -> None:
    for key in ("manufacturer", "model", "platform"):
        value = item.get(key)
        if value is not None:
            entity.attributes_json[f"_{key}"] = str(value)[:255]


def _resource_revision(entity: Entity) -> int:
    """Return a stable monotonic-enough resource version without exposing DB identifiers."""
    value = entity.updated_at or entity.last_seen_at or datetime.now(UTC)
    return int(value.timestamp() * 1_000_000)
