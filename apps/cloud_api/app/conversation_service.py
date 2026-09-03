"""Accesso tenant-scoped alle entità usate dal motore conversazionale."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .ai_interpreter import OpenAIQuestionInterpreter
from .auth import TenantContext
from .config import get_settings
from .conversation import ConversationEngine, ConversationReply, EntitySnapshot, ReplyStatus
from .conversation_learning import ConversationLearningStore
from .domain.models import Entity
from .laboratory_live_state import load_live_states, overlay_entities
from .repositories import EntityRepository, InstallationRepository
from .services import ResourceNotFoundError


class ConversationEntityService:
    """Risponde usando soltanto entità dell'installazione appartenente al tenant."""

    def __init__(
        self,
        session: AsyncSession,
        engine: ConversationEngine | None = None,
        learning_store: ConversationLearningStore | None = None,
    ) -> None:
        self._installations = InstallationRepository(session)
        self._entities = EntityRepository(session)
        self._engine = engine or ConversationEngine()
        settings = get_settings()
        self._learning_store = learning_store or ConversationLearningStore(
            settings.redis_url,
            settings.conversation_learning_ttl_days * 24 * 60 * 60,
        )

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
        category_slug: str | None = None,
    ) -> ConversationReply:
        entities = await self._entities.list_for_installation(
            tenant_id=tenant_id, installation_id=installation_id
        )
        settings = get_settings()
        live_states = await load_live_states(
            settings.alexa_laboratory_installation_public_id
        )
        overlay_entities(entities, live_states)
        snapshots = tuple(
            self._snapshot(entity)
            for entity in entities
            if entity.deleted_at is None
            and (not named_only or bool(entity.voice_name))
            and (
                category_slug is None
                or (
                    entity.voice_category is not None
                    and entity.voice_category.slug == category_slug
                )
            )
        )
        reply = self._engine.ask(utterance, snapshots, now=now)
        if (
            settings.conversation_learning_enabled
            and reply.status is ReplyStatus.ANSWERED
            and reply.diagnostics.get("scope") in {"multiple_sites", "multiple_entities"}
        ):
            await self._learning_store.remember(
                tenant_id,
                installation_id,
                utterance,
                utterance,
                reply.intent or "",
            )
        fallback_statuses = {
            ReplyStatus.UNSUPPORTED,
            ReplyStatus.NOT_FOUND,
            ReplyStatus.AMBIGUOUS,
        }
        if reply.status not in fallback_statuses:
            return reply
        if not settings.conversation_learning_enabled:
            return await self._ask_with_ai(utterance, snapshots, reply, now=now)

        learned = await self._learning_store.get(tenant_id, installation_id, utterance)
        if learned:
            interpreted = self._engine.ask(learned, snapshots, now=now)
            if interpreted.status is ReplyStatus.ANSWERED:
                return replace(
                    interpreted,
                    diagnostics={**interpreted.diagnostics, "interpretation": "learned"},
                )

        interpreted = await self._ask_with_ai(utterance, snapshots, reply, now=now)
        canonical = interpreted.diagnostics.get("canonical_query")
        if interpreted.status is ReplyStatus.ANSWERED and canonical:
            await self._learning_store.remember(
                tenant_id,
                installation_id,
                utterance,
                canonical,
                interpreted.intent or "",
            )
            return replace(
                interpreted,
                diagnostics={
                    key: value
                    for key, value in interpreted.diagnostics.items()
                    if key != "canonical_query"
                }
                | {"interpretation": "ai"},
            )
        return interpreted

    async def _ask_with_ai(
        self,
        utterance: str,
        snapshots: tuple[EntitySnapshot, ...],
        original: ConversationReply,
        *,
        now: datetime | None,
    ) -> ConversationReply:
        settings = get_settings()
        canonical = await OpenAIQuestionInterpreter(
            settings.openai_api_key,
            settings.openai_model,
            settings.openai_timeout_seconds,
        ).interpret(utterance, snapshots)
        if canonical is None:
            return original
        interpreted = self._engine.ask(canonical, snapshots, now=now)
        if interpreted.status is not ReplyStatus.ANSWERED:
            return original
        return replace(
            interpreted,
            diagnostics={**interpreted.diagnostics, "canonical_query": canonical},
        )

    @staticmethod
    def _snapshot(entity: Entity) -> EntitySnapshot:
        attributes = entity.attributes_json if isinstance(entity.attributes_json, dict) else {}
        raw_unit = attributes.get("unit_of_measurement")
        unit = str(raw_unit)[:32] if isinstance(raw_unit, str) else None
        state = entity.state
        if entity.ha_domain == "climate":
            current_temperature = attributes.get("current_temperature")
            if (
                isinstance(current_temperature, (int, float))
                and not isinstance(current_temperature, bool)
            ):
                state = str(current_temperature)
                temperature_unit = attributes.get("temperature_unit")
                unit = (
                    str(temperature_unit)[:32]
                    if isinstance(temperature_unit, str)
                    else "°C"
                )
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
            state=state,
            unit=unit,
            device_class=entity.device_class,
            area=entity.area_name,
            aliases=aliases,
            available=entity.available,
            observed_at=observed_at,
            category=entity.voice_category.slug if entity.voice_category is not None else None,
        )
