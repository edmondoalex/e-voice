"""Central user-to-installation authorization policy."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import TenantContext
from .domain.enums import TenantRole
from .domain.models import Installation, TenantMembership, UserInstallationAssignment

INSTALLATION_ADMIN_ROLES = frozenset(
    {
        TenantRole.OWNER,
        TenantRole.DEALER_ADMIN,
        TenantRole.INSTALLER,
        TenantRole.CUSTOMER_ADMIN,
    }
)
INSTALLATION_ACCESS_LEVELS = frozenset({"use", "manage", "readonly"})


class AssignmentNotAllowedError(Exception):
    """Assignment request is unauthorized or crosses a tenant boundary."""


class InstallationAccessService:
    """Resolve installation access without disclosing unauthorized resources."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def statement(context: TenantContext) -> Select[tuple[Installation]]:
        statement = select(Installation).where(Installation.tenant_id == context.tenant_id)
        if context.role in INSTALLATION_ADMIN_ROLES:
            return statement
        assignment_exists = exists().where(
            UserInstallationAssignment.tenant_id == context.tenant_id,
            UserInstallationAssignment.user_id == context.user_id,
            UserInstallationAssignment.installation_id == Installation.id,
            UserInstallationAssignment.revoked_at.is_(None),
        )
        return statement.where(assignment_exists)

    async def list(self, context: TenantContext) -> list[Installation]:
        return list((await self._session.scalars(self.statement(context))).all())

    async def get(self, context: TenantContext, installation_id: UUID) -> Installation | None:
        result = await self._session.scalars(
            self.statement(context).where(Installation.id == installation_id)
        )
        return result.one_or_none()

    async def allows(self, context: TenantContext, installation_id: UUID) -> bool:
        return (await self.get(context, installation_id)) is not None


class InstallationAssignmentService:
    """Grant and revoke installation access through the central tenant policy."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _require_admin(context: TenantContext) -> None:
        if context.role not in INSTALLATION_ADMIN_ROLES:
            raise AssignmentNotAllowedError

    async def grant(
        self,
        context: TenantContext,
        *,
        user_id: UUID,
        installation_id: UUID,
        access_level: str = "use",
    ) -> UserInstallationAssignment:
        self._require_admin(context)
        if access_level not in INSTALLATION_ACCESS_LEVELS:
            raise ValueError("Unsupported installation access level")
        membership = await self._session.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == context.tenant_id,
                TenantMembership.user_id == user_id,
            )
        )
        installation = await self._session.scalar(
            select(Installation).where(
                Installation.tenant_id == context.tenant_id,
                Installation.id == installation_id,
            )
        )
        if membership is None or installation is None:
            raise AssignmentNotAllowedError
        assignment = await self._session.scalar(
            select(UserInstallationAssignment).where(
                UserInstallationAssignment.tenant_id == context.tenant_id,
                UserInstallationAssignment.user_id == user_id,
                UserInstallationAssignment.installation_id == installation_id,
            )
        )
        now = datetime.now(UTC)
        if assignment is None:
            assignment = UserInstallationAssignment(
                tenant_id=context.tenant_id,
                user_id=user_id,
                installation_id=installation_id,
                access_level=access_level,
                granted_by_user_id=context.user_id,
                granted_at=now,
            )
            self._session.add(assignment)
        else:
            assignment.access_level = access_level
            assignment.granted_by_user_id = context.user_id
            assignment.granted_at = now
            assignment.revoked_at = None
        await self._session.flush()
        return assignment

    async def revoke(
        self,
        context: TenantContext,
        *,
        user_id: UUID,
        installation_id: UUID,
    ) -> None:
        self._require_admin(context)
        assignment = await self._session.scalar(
            select(UserInstallationAssignment).where(
                UserInstallationAssignment.tenant_id == context.tenant_id,
                UserInstallationAssignment.user_id == user_id,
                UserInstallationAssignment.installation_id == installation_id,
                UserInstallationAssignment.revoked_at.is_(None),
            )
        )
        if assignment is None:
            raise AssignmentNotAllowedError
        assignment.revoked_at = datetime.now(UTC)
        await self._session.flush()
