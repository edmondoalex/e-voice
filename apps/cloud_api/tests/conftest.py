from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.cloud_api.app.database import Base
from apps.cloud_api.app.domain.enums import TenantRole
from apps.cloud_api.app.domain.models import (
    AlexaPublication,
    Dealer,
    Entity,
    Installation,
    Tenant,
    TenantMembership,
    User,
)


@dataclass(frozen=True, slots=True)
class SeededDomain:
    user_a_id: UUID
    user_b_id: UUID
    user_readonly_id: UUID
    tenant_a_id: UUID
    tenant_b_id: UUID
    installation_a_id: UUID
    installation_b_id: UUID
    entity_a_id: UUID
    entity_b_id: UUID
    publication_a_id: UUID
    publication_b_id: UUID


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database_session:
        yield database_session
        await database_session.rollback()

    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_domain(session: AsyncSession) -> SeededDomain:
    dealer = Dealer(name="Ekonex", slug="ekonex")
    user_a = User(email="owner@example.test", password_hash="not-a-real-password-hash")
    user_b = User(email="owner-b@example.test", password_hash="not-a-real-password-hash")
    user_readonly = User(email="readonly@example.test", password_hash="not-a-real-password-hash")
    tenant_a = Tenant(name="Tenant A", slug="tenant-a", dealer=dealer)
    tenant_b = Tenant(name="Tenant B", slug="tenant-b", dealer=dealer)
    session.add_all([dealer, user_a, user_b, user_readonly, tenant_a, tenant_b])
    await session.flush()

    session.add_all(
        [
            TenantMembership(tenant_id=tenant_a.id, user_id=user_a.id, role=TenantRole.OWNER),
            TenantMembership(
                tenant_id=tenant_a.id,
                user_id=user_readonly.id,
                role=TenantRole.SUPPORT_READONLY,
            ),
            TenantMembership(tenant_id=tenant_b.id, user_id=user_b.id, role=TenantRole.OWNER),
        ]
    )
    installation_a = Installation(tenant_id=tenant_a.id, name="Home A", public_id="installation-a")
    installation_b = Installation(tenant_id=tenant_b.id, name="Home B", public_id="installation-b")
    session.add_all([installation_a, installation_b])
    await session.flush()

    entity_a = Entity(
        installation_id=installation_a.id,
        ha_entity_id="light.kitchen",
        ha_domain="light",
        friendly_name="Kitchen",
    )
    entity_b = Entity(
        installation_id=installation_b.id,
        ha_entity_id="light.private",
        ha_domain="light",
        friendly_name="Private",
    )
    session.add_all([entity_a, entity_b])
    await session.flush()

    publication_a = AlexaPublication(
        entity_id=entity_a.id,
        display_name="Kitchen",
        mapper_type="light",
        alexa_endpoint_id="ev1_a",
    )
    publication_b = AlexaPublication(
        entity_id=entity_b.id,
        display_name="Private",
        mapper_type="light",
        alexa_endpoint_id="ev1_b",
    )
    session.add_all([publication_a, publication_b])
    await session.commit()

    return SeededDomain(
        user_a_id=user_a.id,
        user_b_id=user_b.id,
        user_readonly_id=user_readonly.id,
        tenant_a_id=tenant_a.id,
        tenant_b_id=tenant_b.id,
        installation_a_id=installation_a.id,
        installation_b_id=installation_b.id,
        entity_a_id=entity_a.id,
        entity_b_id=entity_b.id,
        publication_a_id=publication_a.id,
        publication_b_id=publication_b.id,
    )
