"""Minimal authorization context for tenant-scoped M1 services."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain.enums import RecordStatus, TenantRole
from .domain.models import Tenant, TenantMembership, User


class AccessDeniedError(Exception):
    """Raised without disclosing whether another tenant's resource exists."""


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Authenticated user authorization within one tenant boundary."""

    user_id: UUID
    tenant_id: UUID
    role: TenantRole


class AuthenticationService:
    """Resolve an active user's active tenant membership."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def tenant_context(self, *, user_id: UUID, tenant_id: UUID) -> TenantContext:
        statement = (
            select(TenantMembership)
            .join(User, User.id == TenantMembership.user_id)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .where(
                TenantMembership.user_id == user_id,
                TenantMembership.tenant_id == tenant_id,
                User.status == RecordStatus.ACTIVE,
                Tenant.status == RecordStatus.ACTIVE,
            )
        )
        membership = await self._session.scalar(statement)
        if membership is None:
            raise AccessDeniedError
        return TenantContext(user_id=user_id, tenant_id=tenant_id, role=membership.role)
