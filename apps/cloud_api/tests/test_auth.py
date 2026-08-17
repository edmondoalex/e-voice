import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.auth import AccessDeniedError, AuthenticationService
from apps.cloud_api.app.domain.enums import TenantRole

from .conftest import SeededDomain


async def test_active_membership_resolves_tenant_context(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    context = await AuthenticationService(session).tenant_context(
        user_id=seeded_domain.user_a_id, tenant_id=seeded_domain.tenant_a_id
    )

    assert context.tenant_id == seeded_domain.tenant_a_id
    assert context.role is TenantRole.OWNER


async def test_user_cannot_claim_context_for_another_tenant(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    with pytest.raises(AccessDeniedError):
        await AuthenticationService(session).tenant_context(
            user_id=seeded_domain.user_a_id, tenant_id=seeded_domain.tenant_b_id
        )
