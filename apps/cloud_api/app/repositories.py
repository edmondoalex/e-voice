"""Tenant-scoped persistence interfaces for M1 resources."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain.models import AlexaPublication, Entity, Installation, Tenant


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, tenant_id: UUID) -> Tenant | None:
        result = await self._session.scalars(select(Tenant).where(Tenant.id == tenant_id))
        return result.one_or_none()


class InstallationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, tenant_id: UUID, installation_id: UUID) -> Installation | None:
        statement = select(Installation).where(
            Installation.tenant_id == tenant_id,
            Installation.id == installation_id,
        )
        result = await self._session.scalars(statement)
        return result.one_or_none()


class EntityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _tenant_statement(tenant_id: UUID) -> Select[tuple[Entity]]:
        return select(Entity).join(Installation).where(Installation.tenant_id == tenant_id)

    async def get(self, *, tenant_id: UUID, entity_id: UUID) -> Entity | None:
        statement = self._tenant_statement(tenant_id).where(Entity.id == entity_id)
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def list_for_installation(
        self, *, tenant_id: UUID, installation_id: UUID
    ) -> list[Entity]:
        statement = self._tenant_statement(tenant_id).where(
            Entity.installation_id == installation_id
        )
        return list((await self._session.scalars(statement)).all())


class AlexaPublicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _tenant_statement(tenant_id: UUID) -> Select[tuple[AlexaPublication]]:
        return (
            select(AlexaPublication)
            .join(Entity)
            .join(Installation)
            .where(Installation.tenant_id == tenant_id)
        )

    async def get(self, *, tenant_id: UUID, publication_id: UUID) -> AlexaPublication | None:
        statement = self._tenant_statement(tenant_id).where(AlexaPublication.id == publication_id)
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def get_for_entity(self, *, tenant_id: UUID, entity_id: UUID) -> AlexaPublication | None:
        statement = self._tenant_statement(tenant_id).where(AlexaPublication.entity_id == entity_id)
        result = await self._session.scalars(statement)
        return result.one_or_none()
