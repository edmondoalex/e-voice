"""Authorized use cases for the M1 tenant-owned domain."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .auth import TenantContext
from .domain.enums import TenantRole
from .domain.models import AlexaPublication, Entity, Installation
from .installation_access import InstallationAccessService
from .repositories import AlexaPublicationRepository, EntityRepository, InstallationRepository


class ResourceNotFoundError(Exception):
    """A tenant-safe not-found response that does not disclose foreign records."""


class OperationNotAllowedError(Exception):
    """The authenticated tenant role cannot perform the requested operation."""


PUBLICATION_WRITE_ROLES = {
    TenantRole.OWNER,
    TenantRole.DEALER_ADMIN,
    TenantRole.INSTALLER,
    TenantRole.CUSTOMER_ADMIN,
}


class TenantDomainService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._installations = InstallationRepository(session)
        self._entities = EntityRepository(session)
        self._publications = AlexaPublicationRepository(session)
        self._installation_access = InstallationAccessService(session)

    async def get_installation(self, context: TenantContext, installation_id: UUID) -> Installation:
        installation = await self._installation_access.get(context, installation_id)
        if installation is None:
            raise ResourceNotFoundError
        return installation

    async def get_entity(self, context: TenantContext, entity_id: UUID) -> Entity:
        entity = await self._entities.get(tenant_id=context.tenant_id, entity_id=entity_id)
        if entity is None or not await self._installation_access.allows(
            context, entity.installation_id
        ):
            raise ResourceNotFoundError
        return entity

    async def set_publication_enabled(
        self, context: TenantContext, publication_id: UUID, *, enabled: bool
    ) -> AlexaPublication:
        if context.role not in PUBLICATION_WRITE_ROLES:
            raise OperationNotAllowedError
        publication = await self._publications.get(
            tenant_id=context.tenant_id, publication_id=publication_id
        )
        if publication is None or not await self._installation_access.allows(
            context, publication.entity.installation_id
        ):
            raise ResourceNotFoundError
        publication.enabled = enabled
        await self._session.flush()
        return publication
