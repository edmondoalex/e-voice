"""Post-commit durable FCM wake processing; at-least-once wake semantics.

Worker startup remains disabled by default pending schema and concurrency checks.
"""

import asyncio
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .domain.models import WearDevice
from .wear_bridge import WearBridgeWake
from .wear_fcm import WearFcmSender

logger = logging.getLogger(__name__)


async def dispatch_one(session: AsyncSession, sender=None) -> bool:
    wake = await session.scalar(
        select(WearBridgeWake)
        .order_by(WearBridgeWake.event_key)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if wake is None:
        await session.rollback()
        return False
    device = await session.get(WearDevice, wake.device_id)
    if device is None or device.revoked_at is not None or not device.fcm_token_encrypted:
        await session.delete(wake)
        await session.commit()
        return True
    observed_token = device.fcm_token_encrypted
    try:
        delivered = await (sender or WearFcmSender()).send_wake(observed_token, wake.event_key)
    except Exception:
        wake.attempts += 1
        await session.commit()
        return False
    if delivered is False:
        result = await session.execute(
            update(WearDevice)
            .where(
                WearDevice.id == device.id,
                WearDevice.revoked_at.is_(None),
                WearDevice.fcm_token_encrypted == observed_token,
            )
            .values(fcm_token_encrypted=None, fcm_token_updated_at=None)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            # A token was changed/revoked while FCM was in flight; never erase
            # the new credential and leave the wake for a fresh retry.
            wake.attempts += 1
            await session.commit()
            return False
    else:
        current = (
            await session.execute(
                select(WearDevice.fcm_token_encrypted, WearDevice.revoked_at)
                .where(WearDevice.id == device.id)
                .with_for_update()
            )
        ).one_or_none()
        if (
            current is not None
            and current.revoked_at is None
            and current.fcm_token_encrypted != observed_token
        ):
            wake.attempts += 1
            await session.commit()
            return False
    await session.delete(wake)
    await session.commit()
    return True


async def run_worker(session_factory, *, idle_seconds: float = 5.0) -> None:
    while True:
        try:
            async with session_factory() as session:
                progressed = await dispatch_one(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("wear_wake_dispatch_failed")
            progressed = False
        await asyncio.sleep(0 if progressed else idle_seconds)
