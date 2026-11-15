from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.auth import AuthenticationService
from apps.cloud_api.app.domain.enums import TenantRole
from apps.cloud_api.app.domain.models import (
    Installation,
    TenantMembership,
    User,
    UserInstallationAssignment,
)
from apps.cloud_api.app.installation_access import (
    AssignmentNotAllowedError,
    InstallationAccessService,
    InstallationAssignmentService,
)
from apps.cloud_api.app.portal_auth import hash_password
from apps.cloud_api.app.services import ResourceNotFoundError, TenantDomainService

from .conftest import SeededDomain


async def _customer_context(session: AsyncSession, seeded: SeededDomain):
    user = User(
        email="customer@example.test",
        password_hash=hash_password("customer-password-123"),
    )
    session.add(user)
    await session.flush()
    session.add(
        TenantMembership(
            tenant_id=seeded.tenant_a_id,
            user_id=user.id,
            role=TenantRole.CUSTOMER_USER,
        )
    )
    await session.commit()
    context = await AuthenticationService(session).tenant_context(
        user_id=user.id, tenant_id=seeded.tenant_a_id
    )
    return user, context


async def test_customer_sees_only_explicitly_assigned_installations(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    user, context = await _customer_context(session, seeded_domain)
    second = Installation(
        tenant_id=seeded_domain.tenant_a_id,
        name="Home A2",
        public_id="installation-a2",
    )
    session.add(second)
    await session.flush()
    session.add(
        UserInstallationAssignment(
            tenant_id=seeded_domain.tenant_a_id,
            user_id=user.id,
            installation_id=seeded_domain.installation_a_id,
            granted_by_user_id=seeded_domain.user_a_id,
        )
    )
    await session.commit()

    installations = await InstallationAccessService(session).list(context)

    assert [item.id for item in installations] == [seeded_domain.installation_a_id]
    with pytest.raises(ResourceNotFoundError):
        await TenantDomainService(session).get_installation(context, second.id)


async def test_revocation_is_effective_during_the_next_access_check(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    user, context = await _customer_context(session, seeded_domain)
    assignment = UserInstallationAssignment(
        tenant_id=seeded_domain.tenant_a_id,
        user_id=user.id,
        installation_id=seeded_domain.installation_a_id,
        granted_by_user_id=seeded_domain.user_a_id,
    )
    session.add(assignment)
    await session.commit()
    assert await InstallationAccessService(session).allows(
        context, seeded_domain.installation_a_id
    )

    assignment.revoked_at = datetime.now(UTC)
    await session.commit()

    assert not await InstallationAccessService(session).allows(
        context, seeded_domain.installation_a_id
    )


async def test_assignment_cannot_cross_tenant_boundary(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    user, context = await _customer_context(session, seeded_domain)
    session.add(
        UserInstallationAssignment(
            tenant_id=seeded_domain.tenant_a_id,
            user_id=user.id,
            installation_id=seeded_domain.installation_b_id,
            granted_by_user_id=seeded_domain.user_a_id,
        )
    )
    await session.commit()

    assert not await InstallationAccessService(session).allows(
        context, seeded_domain.installation_b_id
    )


async def test_tenant_administrators_keep_management_access(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    context = await AuthenticationService(session).tenant_context(
        user_id=seeded_domain.user_a_id,
        tenant_id=seeded_domain.tenant_a_id,
    )

    assert await InstallationAccessService(session).allows(
        context, seeded_domain.installation_a_id
    )


async def test_admin_can_grant_revoke_and_regrant_access(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    user, customer = await _customer_context(session, seeded_domain)
    owner = await AuthenticationService(session).tenant_context(
        user_id=seeded_domain.user_a_id,
        tenant_id=seeded_domain.tenant_a_id,
    )
    assignments = InstallationAssignmentService(session)

    await assignments.grant(
        owner,
        user_id=user.id,
        installation_id=seeded_domain.installation_a_id,
        access_level="readonly",
    )
    await session.commit()
    assert await InstallationAccessService(session).allows(
        customer, seeded_domain.installation_a_id
    )

    await assignments.revoke(
        owner,
        user_id=user.id,
        installation_id=seeded_domain.installation_a_id,
    )
    await session.commit()
    assert not await InstallationAccessService(session).allows(
        customer, seeded_domain.installation_a_id
    )

    assignment = await assignments.grant(
        owner,
        user_id=user.id,
        installation_id=seeded_domain.installation_a_id,
    )
    await session.commit()
    assert assignment.revoked_at is None


async def test_customer_cannot_manage_assignments(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    user, customer = await _customer_context(session, seeded_domain)

    with pytest.raises(AssignmentNotAllowedError):
        await InstallationAssignmentService(session).grant(
            customer,
            user_id=user.id,
            installation_id=seeded_domain.installation_a_id,
        )


async def test_admin_cannot_assign_a_foreign_installation(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    user, _ = await _customer_context(session, seeded_domain)
    owner = await AuthenticationService(session).tenant_context(
        user_id=seeded_domain.user_a_id,
        tenant_id=seeded_domain.tenant_a_id,
    )

    with pytest.raises(AssignmentNotAllowedError):
        await InstallationAssignmentService(session).grant(
            owner,
            user_id=user.id,
            installation_id=seeded_domain.installation_b_id,
        )
