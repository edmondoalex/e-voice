"""Manual retention command intended for cron or systemd timers."""

from __future__ import annotations

import asyncio

from .database import async_session_factory
from .history import cleanup_expired


async def _run() -> None:
    async with async_session_factory() as session:
        result = await cleanup_expired(session)
    print(
        "Cleanup complete: "
        f"history={result.state_history} operations={result.operational_events} "
        f"audit={result.audit_events} sessions={result.portal_sessions} "
        f"login_attempts={result.login_attempts}"
    )


if __name__ == "__main__":
    asyncio.run(_run())
