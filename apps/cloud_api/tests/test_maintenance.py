"""Automatic retention execution and scheduling tests."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app import maintenance
from apps.cloud_api.app.config import Settings
from apps.cloud_api.app.domain.models import MaintenanceRun, PortalLoginAttempt
from apps.cloud_api.app.maintenance import execute_cleanup, next_cleanup_at


def test_next_cleanup_handles_future_missed_and_completed_boundaries() -> None:
    before = datetime(2026, 8, 19, 2, 0, tzinfo=UTC)
    boundary = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)
    assert next_cleanup_at(before, None, schedule_hour_utc=3) == boundary
    after = datetime(2026, 8, 19, 5, 0, tzinfo=UTC)
    assert next_cleanup_at(after, None, schedule_hour_utc=3) == boundary
    assert next_cleanup_at(after, boundary, schedule_hour_utc=3) == boundary + timedelta(days=1)


async def test_cleanup_run_is_persisted_logged_and_idempotent(
    session: AsyncSession, seeded_domain: object, caplog: pytest.LogCaptureFixture
) -> None:
    now = datetime.now(UTC)
    session.add(
        PortalLoginAttempt(
            email_hash="safe-hash",
            successful=False,
            attempted_at=now - timedelta(days=31),
        )
    )
    await session.commit()
    caplog.set_level(logging.INFO, logger="apps.cloud_api.app.maintenance")
    first = await execute_cleanup(session, Settings(), now=now)
    second = await execute_cleanup(session, Settings(), now=now + timedelta(seconds=1))
    assert first.status == second.status == "ok"
    assert first.deleted_counts_json["login_attempts"] == 1
    assert second.deleted_counts_json["login_attempts"] == 0
    assert await session.scalar(select(func.count(MaintenanceRun.id))) == 2
    assert "result=OK" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "login_attempts=1" in caplog.text


async def test_cleanup_failure_is_recorded_without_raising_or_sensitive_details(
    session: AsyncSession,
    seeded_domain: object,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("credential=must-not-be-logged")

    monkeypatch.setattr(maintenance, "cleanup_expired", fail)
    caplog.set_level(logging.INFO, logger="apps.cloud_api.app.maintenance")
    run = await execute_cleanup(session, Settings())
    assert run.status == "error"
    assert run.error_code == "RuntimeError"
    assert "result=ERROR" in caplog.text
    assert "must-not-be-logged" not in caplog.text
