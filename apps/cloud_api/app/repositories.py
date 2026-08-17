"""Tenant-scoped persistence interfaces for core and pairing resources."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain.enums import PairingStatus
from .domain.models import (
    AlexaPublication,
    ConnectorCredential,
    Entity,
    Installation,
    PairingClaimAttempt,
    PairingSession,
    Tenant,
)


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


class PairingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def pending_for_nonce(
        self, *, installation_nonce: str, now: datetime
    ) -> PairingSession | None:
        statement = select(PairingSession).where(
            PairingSession.installation_nonce == installation_nonce,
            PairingSession.status == PairingStatus.PENDING,
            PairingSession.expires_at > now,
        )
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def code_hash_exists(self, code_hash: str) -> bool:
        statement = select(PairingSession.id).where(PairingSession.code_hash == code_hash)
        return (await self._session.scalar(statement)) is not None

    async def get_by_code_hash_for_update(self, code_hash: str) -> PairingSession | None:
        statement = (
            select(PairingSession).where(PairingSession.code_hash == code_hash).with_for_update()
        )
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def get_by_id_for_update(self, session_id: UUID) -> PairingSession | None:
        statement = select(PairingSession).where(PairingSession.id == session_id).with_for_update()
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def recent_failed_attempts(self, *, user_id: UUID, since: datetime) -> int:
        statement = select(func.count(PairingClaimAttempt.id)).where(
            PairingClaimAttempt.user_id == user_id,
            PairingClaimAttempt.successful.is_(False),
            PairingClaimAttempt.attempted_at >= since,
        )
        return int((await self._session.scalar(statement)) or 0)


class ConnectorCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_for_installation(
        self, *, tenant_id: UUID, installation_id: UUID
    ) -> list[ConnectorCredential]:
        statement = (
            select(ConnectorCredential)
            .join(Installation)
            .where(
                Installation.tenant_id == tenant_id,
                ConnectorCredential.installation_id == installation_id,
                ConnectorCredential.revoked_at.is_(None),
            )
            .with_for_update()
        )
        return list((await self._session.scalars(statement)).all())

    async def get(self, *, tenant_id: UUID, credential_id: UUID) -> ConnectorCredential | None:
        statement = (
            select(ConnectorCredential)
            .join(Installation)
            .where(
                Installation.tenant_id == tenant_id,
                ConnectorCredential.id == credential_id,
            )
            .with_for_update()
        )
        result = await self._session.scalars(statement)
        return result.one_or_none()
