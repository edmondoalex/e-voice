"""Official Wear OS enrollment and per-device authorization (CHANGE081)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain.enums import RecordStatus
from .domain.models import (
    Installation,
    Tenant,
    TenantMembership,
    User,
    UserInstallationAssignment,
    WearClaimAttempt,
    WearDevice,
    WearEnrollment,
)

CAPABILITIES = frozenset(
    {
        "state.read",
        "command.execute",
        "routine.execute",
        "voice.ask",
        "notification.receive",
        "notification.ack",
    }
)


class WearDenied(Exception):
    """Uniform denial that does not disclose which authorization check failed."""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class WearContext:
    device_id: UUID
    user_id: UUID
    tenant_id: UUID
    installation_id: UUID
    capabilities: frozenset[str]

    def require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise WearDenied


class WearDeviceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_enrollment(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        installation_id: UUID,
        created_by_user_id: UUID,
        capabilities: set[str],
        ttl_seconds: int = 600,
    ) -> tuple[WearEnrollment, str]:
        if not capabilities or not capabilities <= CAPABILITIES or not 60 <= ttl_seconds <= 600:
            raise WearDenied
        from .wear_bridge import lock_wear_writer

        await lock_wear_writer(self.session, installation_id)
        grant = await self.session.scalar(
            select(UserInstallationAssignment).where(
                UserInstallationAssignment.tenant_id == tenant_id,
                UserInstallationAssignment.user_id == user_id,
                UserInstallationAssignment.installation_id == installation_id,
                UserInstallationAssignment.revoked_at.is_(None),
            )
        )
        membership = await self.session.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user_id,
            )
        )
        installation = await self.session.scalar(
            select(Installation).where(
                Installation.id == installation_id,
                Installation.tenant_id == tenant_id,
                Installation.revoked_at.is_(None),
            )
        )
        if grant is None or membership is None or installation is None:
            raise WearDenied
        for _attempt in range(20):
            code = f"{secrets.randbelow(1_000_000):06d}"
            exists = await self.session.scalar(
                select(WearEnrollment.id).where(WearEnrollment.code_hash == _digest(code))
            )
            if exists is None:
                break
        else:
            raise WearDenied
        enrollment = WearEnrollment(
            code_hash=_digest(code),
            tenant_id=tenant_id,
            user_id=user_id,
            installation_id=installation_id,
            capabilities_json=sorted(capabilities),
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            created_by_user_id=created_by_user_id,
        )
        self.session.add(enrollment)
        await self.session.flush()
        return enrollment, code

    async def claim(self, code: str, display_name: str) -> tuple[WearDevice, str]:
        now = datetime.now(UTC)
        enrollment = await self.session.scalar(
            select(WearEnrollment).where(
                WearEnrollment.code_hash == _digest(code),
            )
        )
        if (
            enrollment is None
            or enrollment.revoked_at is not None
            or enrollment.claimed_at is not None
            or _aware(enrollment.expires_at) <= now
        ):
            raise WearDenied
        from .wear_bridge import lock_wear_writer

        await lock_wear_writer(self.session, enrollment.installation_id)
        credential = secrets.token_urlsafe(32)
        device = WearDevice(
            credential_hash=_digest(credential),
            tenant_id=enrollment.tenant_id,
            user_id=enrollment.user_id,
            installation_id=enrollment.installation_id,
            display_name=display_name.strip(),
            capabilities_json=list(enrollment.capabilities_json),
        )
        if not device.display_name or len(device.display_name) > 120:
            raise WearDenied
        enrollment.claimed_at = now
        self.session.add(device)
        await self.session.flush()
        return device, credential

    async def context(self, credential: str) -> WearContext:
        now = datetime.now(UTC)
        row = (
            await self.session.execute(
                select(WearDevice, User, Tenant, Installation, TenantMembership)
                .join(User, User.id == WearDevice.user_id)
                .join(Tenant, Tenant.id == WearDevice.tenant_id)
                .join(Installation, Installation.id == WearDevice.installation_id)
                .join(
                    TenantMembership,
                    (
                        (TenantMembership.tenant_id == WearDevice.tenant_id)
                        & (TenantMembership.user_id == WearDevice.user_id)
                    ),
                )
                .where(WearDevice.credential_hash == _digest(credential))
            )
        ).one_or_none()
        if row is None:
            raise WearDenied
        device, user, tenant, installation, _membership = row
        grant = await self.session.scalar(
            select(UserInstallationAssignment).where(
                UserInstallationAssignment.tenant_id == device.tenant_id,
                UserInstallationAssignment.user_id == device.user_id,
                UserInstallationAssignment.installation_id == device.installation_id,
                UserInstallationAssignment.revoked_at.is_(None),
            )
        )
        if (
            device.revoked_at is not None
            or (device.expires_at is not None and _aware(device.expires_at) <= now)
            or user.status != RecordStatus.ACTIVE
            or tenant.status != RecordStatus.ACTIVE
            or installation.revoked_at is not None
            or grant is None
        ):
            raise WearDenied
        device.last_seen_at = now
        return WearContext(
            device_id=device.id,
            user_id=device.user_id,
            tenant_id=device.tenant_id,
            installation_id=device.installation_id,
            capabilities=frozenset(device.capabilities_json) & CAPABILITIES,
        )

    async def revoke(self, *, device_id: UUID, tenant_id: UUID) -> None:
        device = await self.session.scalar(
            select(WearDevice).where(
                WearDevice.id == device_id,
                WearDevice.tenant_id == tenant_id,
                WearDevice.revoked_at.is_(None),
            )
        )
        if device is None:
            raise WearDenied
        from .wear_bridge import lock_wear_writer

        await lock_wear_writer(self.session, device.installation_id, allow_econtrol=True)
        device.revoked_at = datetime.now(UTC)
        device.fcm_token_encrypted = None
        device.fcm_token_updated_at = None


class WearClaimLimiter:
    """Persisted five-attempt/15-minute limit for an anonymized source."""

    def __init__(self, session: AsyncSession, source_hash: str) -> None:
        self.session = session
        self.source_hash = source_hash

    async def failed_count(self) -> int:
        since = datetime.now(UTC) - timedelta(minutes=15)
        value = await self.session.scalar(
            select(func.count())
            .select_from(WearClaimAttempt)
            .where(
                WearClaimAttempt.source_hash == self.source_hash,
                WearClaimAttempt.successful.is_(False),
                WearClaimAttempt.attempted_at >= since,
            )
        )
        return int(value or 0)

    def record(self, *, successful: bool) -> None:
        self.session.add(WearClaimAttempt(source_hash=self.source_hash, successful=successful))
