"""Persisted, installation-bound Wear notification delivery."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain.models import WearDevice, WearNotification
from .wear_devices import WearContext, WearDenied

SEVERITIES = frozenset({"info", "warning", "critical"})


class WearNotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue_for_installation(
        self,
        *,
        tenant_id: UUID,
        installation_id: UUID,
        event_key: str,
        category: str,
        severity: str,
        title: str,
        body: str,
    ) -> int:
        if (
            not event_key.strip()
            or len(event_key) > 120
            or not category.strip()
            or len(category) > 64
            or severity not in SEVERITIES
            or not title.strip()
            or len(title) > 120
            or not body.strip()
            or len(body) > 500
        ):
            raise WearDenied
        devices = (await self.session.scalars(select(WearDevice).where(
            WearDevice.tenant_id == tenant_id,
            WearDevice.installation_id == installation_id,
            WearDevice.revoked_at.is_(None),
        ))).all()
        created = 0
        for device in devices:
            if "notification.receive" not in device.capabilities_json:
                continue
            existing = await self.session.scalar(select(WearNotification.id).where(
                WearNotification.device_id == device.id,
                WearNotification.event_key == event_key,
            ))
            if existing is not None:
                continue
            self.session.add(WearNotification(
                device_id=device.id,
                tenant_id=tenant_id,
                installation_id=installation_id,
                event_key=event_key,
                category=category,
                severity=severity,
                title=title.strip(),
                body=body.strip(),
            ))
            created += 1
        await self.session.flush()
        return created

    async def pending(self, context: WearContext, limit: int = 20) -> list[WearNotification]:
        context.require("notification.receive")
        now = datetime.now(UTC)
        items = list((await self.session.scalars(select(WearNotification).where(
            WearNotification.device_id == context.device_id,
            WearNotification.tenant_id == context.tenant_id,
            WearNotification.installation_id == context.installation_id,
            WearNotification.acknowledged_at.is_(None),
            (WearNotification.expires_at.is_(None) | (WearNotification.expires_at > now)),
        ).order_by(WearNotification.created_at).limit(max(1, min(limit, 50))))).all())
        for item in items:
            if item.delivered_at is None:
                item.delivered_at = now
        return items

    async def acknowledge(self, context: WearContext, notification_id: UUID) -> None:
        context.require("notification.ack")
        item = await self.session.scalar(select(WearNotification).where(
            WearNotification.id == notification_id,
            WearNotification.device_id == context.device_id,
            WearNotification.tenant_id == context.tenant_id,
            WearNotification.installation_id == context.installation_id,
            WearNotification.acknowledged_at.is_(None),
        ))
        if item is None:
            raise WearDenied
        item.acknowledged_at = datetime.now(UTC)
