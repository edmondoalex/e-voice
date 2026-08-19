"""Retention command shared by manual operations and the automatic scheduler."""

from __future__ import annotations

import asyncio

from .database import async_session_factory
from .maintenance import execute_cleanup


async def _run() -> None:
    async with async_session_factory() as session:
        await execute_cleanup(session)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_run())
