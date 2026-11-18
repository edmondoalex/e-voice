"""Automatic Wear notifications driven by committed entity state changes."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import async_session_factory
from .domain.models import Entity, WearDevice
from .wear_notifications import WearNotificationService

logger = logging.getLogger(__name__)


def render_wear_rule_message(
    rule: dict[str, object], entity: Entity, previous_state: str | None = None
) -> tuple[str, str]:
    name = entity.voice_name or entity.display_name or entity.friendly_name or entity.ha_entity_id
    value = entity.state or ("disponibile" if entity.available else "non disponibile")
    replacements = {
        "{nome}": name,
        "{valore}": value,
        "{valore_precedente}": previous_state or "—",
        "{id}": entity.ha_entity_id,
    }

    def render(template: object, fallback: str, limit: int) -> str:
        result = str(template or fallback)
        for marker, replacement in replacements.items():
            result = result.replace(marker, replacement)
        return result[:limit]

    return render(rule.get("title"), name, 120), render(rule.get("body"), f"{name}: {value}", 500)


def schedule_wear_rule_evaluation(
    installation_id: UUID, changes: list[tuple[UUID, str | None, bool]]
) -> None:
    task = asyncio.create_task(_evaluate_after_sync(installation_id, changes))
    task.add_done_callback(_log_task_error)


def _log_task_error(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("wear_rule_evaluation_failed", exc_info=task.exception())


async def _evaluate_after_sync(
    installation_id: UUID, changes: list[tuple[UUID, str | None, bool]]
) -> None:
    async with async_session_factory() as session:
        await evaluate_wear_notification_rules(session, installation_id, changes)


async def evaluate_wear_notification_rules(
    session: AsyncSession,
    installation_id: UUID,
    changes: list[tuple[UUID, str | None, bool]],
) -> int:
    previous = {entity_id: (state, available) for entity_id, state, available in changes}
    if not previous:
        return 0
    entities = {
        item.id: item
        for item in (
            await session.scalars(
                select(Entity).where(
                    Entity.installation_id == installation_id,
                    Entity.id.in_(previous),
                    Entity.deleted_at.is_(None),
                )
            )
        ).all()
    }
    devices = (
        await session.scalars(
            select(WearDevice).where(
                WearDevice.installation_id == installation_id,
                WearDevice.revoked_at.is_(None),
            )
        )
    ).all()
    created = 0
    service = WearNotificationService(session)
    for device in devices:
        rules = (device.notification_preferences_json or {}).get("notification_rules", [])
        for rule in rules if isinstance(rules, list) else []:
            if not isinstance(rule, dict) or rule.get("enabled", True) is not True:
                continue
            try:
                entity_id = UUID(str(rule.get("entity_id")))
            except (TypeError, ValueError):
                continue
            entity = entities.get(entity_id)
            if entity is None:
                continue
            old_state, old_available = previous[entity_id]
            new_state = entity.state
            trigger = str(rule.get("trigger") or "any_change")
            target = str(rule.get("target_state") or "").casefold()
            matched = (
                trigger == "any_change"
                and (old_state != new_state or old_available != entity.available)
            ) or (
                trigger == "becomes"
                and old_state != new_state
                and (new_state or "").casefold() == target
            ) or (
                trigger == "unavailable" and old_available and not entity.available
            ) or (
                trigger == "available" and not old_available and entity.available
            )
            if not matched:
                continue
            state_text = new_state or ("disponibile" if entity.available else "non disponibile")
            observed_at = entity.updated_at or entity.last_changed_at
            event_stamp = observed_at.isoformat() if observed_at else state_text
            title, body = render_wear_rule_message(rule, entity, old_state)
            created += await service.enqueue_for_device(
                device=device,
                event_key=f"wear-rule:{str(rule.get('id'))[:36]}:{event_stamp}"[:120],
                category=str(rule.get("category") or "system"),
                severity=str(rule.get("severity") or "info"),
                title=title,
                body=body,
            )
    await session.commit()
    return created
