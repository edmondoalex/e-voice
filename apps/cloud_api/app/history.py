"""Storage-efficient state history and retention maintenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .config import Settings, get_settings
from .domain.models import (
    AuditEvent,
    Entity,
    EntityStateHistory,
    OperationalEvent,
    PortalLoginAttempt,
    PortalSession,
)


class StateHistoryService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    async def record_change(
        self,
        entity: Entity,
        *,
        tenant_id: UUID,
        previous_state: str | None,
        previous_available: bool,
    ) -> bool:
        """Append only when the significant state tuple actually changed."""
        if entity.ha_domain in self._settings.excluded_history_domains:
            return False
        if previous_state == entity.state and previous_available == entity.available:
            return False
        latest = await self._session.scalar(
            select(EntityStateHistory)
            .where(EntityStateHistory.entity_id == entity.id)
            .order_by(EntityStateHistory.recorded_at.desc())
            .limit(1)
        )
        if (
            latest is not None
            and latest.state == entity.state
            and latest.available == entity.available
        ):
            return False
        self._session.add(
            EntityStateHistory(
                tenant_id=tenant_id,
                installation_id=entity.installation_id,
                entity_id=entity.id,
                state=entity.state,
                available=entity.available,
            )
        )
        return True


@dataclass(frozen=True, slots=True)
class CleanupResult:
    state_history: int
    operational_events: int
    audit_events: int
    portal_sessions: int
    login_attempts: int


async def cleanup_expired(
    session: AsyncSession,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> CleanupResult:
    """Apply configured retention safely and idempotently."""
    config = settings or get_settings()
    current = now or datetime.now(UTC)

    async def remove(model: Any, condition: ColumnElement[bool]) -> int:
        result = await session.execute(delete(model).where(condition))
        return int(result.rowcount or 0)

    history = await remove(
        EntityStateHistory,
        EntityStateHistory.recorded_at
        < current - timedelta(days=config.state_history_retention_days),
    )
    operations = await remove(
        OperationalEvent,
        OperationalEvent.created_at
        < current - timedelta(days=config.operational_event_retention_days),
    )
    audits = await remove(
        AuditEvent,
        AuditEvent.created_at < current - timedelta(days=config.admin_audit_retention_days),
    )
    portal_sessions = await remove(PortalSession, PortalSession.expires_at < current)
    attempts = await remove(
        PortalLoginAttempt,
        PortalLoginAttempt.attempted_at
        < current - timedelta(days=config.portal_login_attempt_retention_days),
    )
    await session.commit()
    return CleanupResult(history, operations, audits, portal_sessions, attempts)


async def history_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count(EntityStateHistory.id))) or 0)
