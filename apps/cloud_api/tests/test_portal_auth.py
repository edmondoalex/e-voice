"""Portal password and first-user bootstrap tests."""

import pytest
from pwdlib import PasswordHash
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.bootstrap_portal_user import bootstrap_first_user
from apps.cloud_api.app.domain.enums import TenantRole


async def test_bootstrap_creates_argon2_user_and_refuses_second_run(
    session: AsyncSession,
) -> None:
    password = "bootstrap-password-123"
    user = await bootstrap_first_user(
        session,
        email="Admin@Example.Test",
        password=password,
        dealer_name="Ekonex",
        dealer_slug="ekonex",
        tenant_name="First Tenant",
        tenant_slug="first-tenant",
        role=TenantRole.OWNER,
    )
    assert user.email == "admin@example.test"
    assert password not in user.password_hash
    assert PasswordHash.recommended().verify(password, user.password_hash)
    with pytest.raises(RuntimeError, match="already exists"):
        await bootstrap_first_user(
            session,
            email="second@example.test",
            password="second-password-123",
            dealer_name="Other",
            dealer_slug="other",
            tenant_name="Other",
            tenant_slug="other",
            role=TenantRole.OWNER,
        )
