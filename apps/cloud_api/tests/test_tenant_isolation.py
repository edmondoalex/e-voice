import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.auth import AuthenticationService
from apps.cloud_api.app.services import (
    OperationNotAllowedError,
    ResourceNotFoundError,
    TenantDomainService,
)

from .conftest import SeededDomain


async def test_tenant_cannot_read_foreign_installation(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    context = await AuthenticationService(session).tenant_context(
        user_id=seeded_domain.user_a_id, tenant_id=seeded_domain.tenant_a_id
    )

    with pytest.raises(ResourceNotFoundError):
        await TenantDomainService(session).get_installation(
            context, seeded_domain.installation_b_id
        )


async def test_tenant_cannot_read_foreign_entity(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    context = await AuthenticationService(session).tenant_context(
        user_id=seeded_domain.user_a_id, tenant_id=seeded_domain.tenant_a_id
    )

    with pytest.raises(ResourceNotFoundError):
        await TenantDomainService(session).get_entity(context, seeded_domain.entity_b_id)


async def test_tenant_cannot_modify_foreign_publication(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    context = await AuthenticationService(session).tenant_context(
        user_id=seeded_domain.user_a_id, tenant_id=seeded_domain.tenant_a_id
    )

    with pytest.raises(ResourceNotFoundError):
        await TenantDomainService(session).set_publication_enabled(
            context, seeded_domain.publication_b_id, enabled=True
        )


async def test_readonly_role_cannot_modify_own_tenant_publication(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    context = await AuthenticationService(session).tenant_context(
        user_id=seeded_domain.user_readonly_id, tenant_id=seeded_domain.tenant_a_id
    )

    with pytest.raises(OperationNotAllowedError):
        await TenantDomainService(session).set_publication_enabled(
            context, seeded_domain.publication_a_id, enabled=True
        )


async def test_owner_can_modify_own_tenant_publication(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    context = await AuthenticationService(session).tenant_context(
        user_id=seeded_domain.user_a_id, tenant_id=seeded_domain.tenant_a_id
    )

    publication = await TenantDomainService(session).set_publication_enabled(
        context, seeded_domain.publication_a_id, enabled=True
    )

    assert publication.enabled is True
