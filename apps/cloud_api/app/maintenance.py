"""Persistent execution boundary for retention maintenance."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .domain.models import MaintenanceRun
from .history import CleanupResult, cleanup_expired

logger = logging.getLogger(__name__)
CLEANUP_KIND = "retention_cleanup"


def _counts(result: CleanupResult) -> dict[str, int]:
    return {
        "state_history": result.state_history,
        "operational_events": result.operational_events,
        "audit_events": result.audit_events,
        "portal_sessions": result.portal_sessions,
        "login_attempts": result.login_attempts,
    }


async def execute_cleanup(
    session: AsyncSession,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> MaintenanceRun:
    """Run the idempotent cleanup and persist a secret-free execution summary."""
    config = settings or get_settings()
    started_at = now or datetime.now(UTC)
    started_clock = perf_counter()
    run = MaintenanceRun(
        kind=CLEANUP_KIND,
        status="running",
        started_at=started_at,
        deleted_counts_json={},
    )
    session.add(run)
    await session.commit()
    try:
        result = await cleanup_expired(session, config, now=started_at)
        run.status = "ok"
        run.deleted_counts_json = _counts(result)
        run.error_code = None
    except Exception as error:
        await session.rollback()
        run = await session.get(MaintenanceRun, run.id) or run
        run.status = "error"
        run.deleted_counts_json = {}
        run.error_code = type(error).__name__[:100]
    run.completed_at = datetime.now(UTC)
    run.duration_ms = max(0, round((perf_counter() - started_clock) * 1000))
    await session.commit()
    counts = run.deleted_counts_json
    logger.info(
        "database_cleanup started_at=%s result=%s duration_ms=%d "
        "state_history=%d operational_events=%d audit_events=%d "
        "portal_sessions=%d login_attempts=%d error_code=%s",
        run.started_at.isoformat(),
        run.status.upper(),
        run.duration_ms,
        counts.get("state_history", 0),
        counts.get("operational_events", 0),
        counts.get("audit_events", 0),
        counts.get("portal_sessions", 0),
        counts.get("login_attempts", 0),
        run.error_code or "none",
    )
    return run


async def latest_cleanup(session: AsyncSession) -> MaintenanceRun | None:
    result = await session.scalars(
        select(MaintenanceRun)
        .where(MaintenanceRun.kind == CLEANUP_KIND)
        .order_by(MaintenanceRun.started_at.desc())
        .limit(1)
    )
    return result.first()


def next_cleanup_at(
    now: datetime,
    latest_started_at: datetime | None,
    *,
    schedule_hour_utc: int,
) -> datetime:
    """Return today's missed run or the next daily UTC boundary."""
    current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    boundary = current.replace(hour=schedule_hour_utc, minute=0, second=0, microsecond=0)
    latest = latest_started_at
    if latest is not None:
        latest = latest if latest.tzinfo is not None else latest.replace(tzinfo=UTC)
        if latest.astimezone(UTC) >= boundary:
            return boundary + timedelta(days=1)
    return boundary if current >= boundary else boundary
