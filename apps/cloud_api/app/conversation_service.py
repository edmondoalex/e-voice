"""Accesso tenant-scoped alle entità usate dal motore conversazionale."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .auth import TenantContext
from .conversation import ConversationEngine, ConversationReply, EntitySnapshot
from .domain.models import Entity
from .repositories import EntityRepository, InstallationRepository
from .services import ResourceNotFoundError


class ConversationEntityService:
    """Risponde usando soltanto entità dell'installazione appartenente al tenant."""

    def __init__(self, session: AsyncSession, engine: ConversationEngine | None = None) -> None:
        self._installations = InstallationRepository(session)
        self._entities = EntityRepository(session)
        self._engine = engine or ConversationEngine()

    async def ask(
        self,
        context: TenantContext,
        installation_id: UUID,
        utterance: str,
        *,
        now: datetime | None = None,
    ) -> ConversationReply:
        installation = await self._installations.get(
            tenant_id=context.tenant_id, installation_id=installation_id
        )
        if installation is None:
            raise ResourceNotFoundError

        return await self.ask_for_scope(context.tenant_id, installation_id, utterance, now=now)

    async def ask_for_scope(
        self,
        tenant_id: UUID,
        installation_id: UUID,
        utterance: str,
        *,
        now: datetime | None = None,
        named_only: bool = False,
    ) -> ConversationReply:
        entities = await self._entities.list_for_installation(
            tenant_id=tenant_id, installation_id=installation_id
        )
        snapshots = tuple(
            self._snapshot(entity)
            for entity in entities
            if entity.deleted_at is None and (not named_only or bool(entity.voice_name))
        )
        return self._engine.ask(utterance, snapshots, now=now)

    @staticmethod
    def _snapshot(entity: Entity) -> EntitySnapshot:
        attributes = entity.attributes_json if isinstance(entity.attributes_json, dict) else {}
        raw_unit = attributes.get("unit_of_measurement")
        unit = str(raw_unit)[:32] if isinstance(raw_unit, str) else None
        aliases = tuple(alias for alias in entity.voice_aliases if isinstance(alias, str))
        observed_at = entity.last_seen_at or entity.last_changed_at
        if observed_at is not None and observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        return EntitySnapshot(
            entity_id=entity.ha_entity_id,
            name=(
                entity.voice_name
                or entity.display_name
                or entity.friendly_name
                or entity.ha_entity_id
            ),
            domain=entity.ha_domain,
            state=entity.state,
            unit=unit,
            device_class=entity.device_class,
            area=entity.area_name,
            aliases=aliases,
            available=entity.available,
            observed_at=observed_at,
        )
