"""Lightweight daily retention scheduler for the dedicated Docker service."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from .config import get_settings
from .database import async_session_factory
from .maintenance import execute_cleanup, latest_cleanup, next_cleanup_at

logger = logging.getLogger(__name__)


async def run_forever() -> None:
    settings = get_settings()
    while True:
        try:
            async with async_session_factory() as session:
                latest = await latest_cleanup(session)
                now = datetime.now(UTC)
                scheduled = next_cleanup_at(
                    now,
                    latest.started_at if latest else None,
                    schedule_hour_utc=settings.cleanup_schedule_hour_utc,
                )
                delay = (scheduled - now).total_seconds()
                if delay <= 0:
                    await execute_cleanup(session, settings, now=now)
                    continue
            logger.info("database_cleanup next_run_at=%s", scheduled.isoformat())
            await asyncio.sleep(delay)
        except Exception as error:
            logger.error(
                "database_cleanup scheduler_result=ERROR error_code=%s retry_seconds=%d",
                type(error).__name__,
                settings.cleanup_error_retry_seconds,
            )
            await asyncio.sleep(settings.cleanup_error_retry_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_forever())
