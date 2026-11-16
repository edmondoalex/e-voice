"""CHANGE028 signed launch and installation-bounded authorization core."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .auth import AccessDeniedError, AuthenticationService
from .domain.enums import TenantRole
from .domain.models import (
    AlexaPublication,
    AlexaRoutineExecution,
    AlexaSpeakerGroup,
    AlexaVoiceRoutine,
    AuditEvent,
    Entity,
    EntityStateHistory,
    ExternalInstallationBinding,
    Installation,
    InstallationBoundSession,
    OperationalEvent,
    UserInstallationAssignment,
)

ALLOWED_CAPABILITIES = frozenset({"readonly", "use", "manage", "command", "export", "stream"})
BOUND_SESSION_IDLE_TTL = timedelta(minutes=30)
ACCESS_CAPABILITIES = {
    "readonly": frozenset({"readonly", "stream"}),
    "use": frozenset({"readonly", "use", "command", "stream"}),
    "manage": ALLOWED_CAPABILITIES,
}
ROLE_CAPABILITIES = {
    TenantRole.OWNER: ALLOWED_CAPABILITIES,
    TenantRole.DEALER_ADMIN: ALLOWED_CAPABILITIES,
    TenantRole.INSTALLER: ALLOWED_CAPABILITIES,
    TenantRole.CUSTOMER_ADMIN: ALLOWED_CAPABILITIES,
    TenantRole.CUSTOMER_USER: frozenset({"readonly", "use", "command", "stream"}),
    TenantRole.SUPPORT_READONLY: frozenset({"readonly", "stream"}),
}


class BoundLaunchDenied(Exception):
    """Uniform fail-closed launch denial without cross-installation disclosure."""


@dataclass(frozen=True, slots=True)
class BoundContext:
    session_id: UUID
    user_id: UUID
    tenant_id: UUID
    effective_installation_id: UUID
    capabilities: frozenset[str]
    correlation_id: str

    def require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise BoundLaunchDenied


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode_launch(claims: dict[str, Any], secret: bytes) -> str:
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def decode_launch(token: str, secret: bytes) -> dict[str, Any]:
    try:
        payload, signature = token.split(".", 1)
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise BoundLaunchDenied
        decoded = json.loads(_unb64(payload))
        if not isinstance(decoded, dict):
            raise BoundLaunchDenied
        return decoded
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise BoundLaunchDenied from None


class InstallationBoundService:
    def __init__(self, session: AsyncSession, *, issuer: str, audience: str, secret: bytes) -> None:
        self._session = session
        self._issuer = issuer
        self._audience = audience
        self._secret = secret

    async def exchange(
        self,
        token: str,
        *,
        now: datetime | None = None,
        prepared: bool = False,
    ) -> tuple[str, BoundContext]:
        now = now or datetime.now(UTC)
        claims = decode_launch(token, self._secret)
        required = {"issuer", "audience", "subject", "installation_id", "requested_capabilities", "issued_at", "expires_at", "jti", "correlation_id", "tenant_id"}
        if required - claims.keys() or claims["issuer"] != self._issuer or claims["audience"] != self._audience:
            raise BoundLaunchDenied
        try:
            issued = datetime.fromtimestamp(int(claims["issued_at"]), UTC)
            expires = datetime.fromtimestamp(int(claims["expires_at"]), UTC)
            user_id = UUID(str(claims["subject"]))
            tenant_id = UUID(str(claims["tenant_id"]))
            requested = frozenset(str(item) for item in claims["requested_capabilities"])
        except (ValueError, TypeError, OverflowError):
            raise BoundLaunchDenied from None
        if issued > now + timedelta(seconds=30) or expires <= now or expires - issued > timedelta(minutes=5):
            raise BoundLaunchDenied
        if not requested or not requested <= ALLOWED_CAPABILITIES:
            raise BoundLaunchDenied
        jti_hash = hashlib.sha256(str(claims["jti"]).encode()).hexdigest()
        if await self._session.scalar(select(InstallationBoundSession.id).where(InstallationBoundSession.launch_jti_hash == jti_hash)):
            raise BoundLaunchDenied
        binding = await self._session.scalar(select(ExternalInstallationBinding).where(
            ExternalInstallationBinding.issuer == self._issuer,
            ExternalInstallationBinding.external_installation_id == str(claims["installation_id"]),
            ExternalInstallationBinding.tenant_id == tenant_id,
            ExternalInstallationBinding.revoked_at.is_(None),
        ))
        if binding is None:
            raise BoundLaunchDenied
        auth_context = await AuthenticationService(self._session).tenant_context(
            user_id=user_id, tenant_id=tenant_id
        )
        assignment = await self._session.scalar(select(UserInstallationAssignment).where(
            UserInstallationAssignment.tenant_id == tenant_id,
            UserInstallationAssignment.user_id == user_id,
            UserInstallationAssignment.installation_id == binding.installation_id,
            UserInstallationAssignment.revoked_at.is_(None),
        ))
        if assignment is None or assignment.access_level not in ACCESS_CAPABILITIES:
            raise BoundLaunchDenied
        effective = (
            requested
            & ACCESS_CAPABILITIES[assignment.access_level]
            & ROLE_CAPABILITIES[auth_context.role]
        )
        if not effective:
            raise BoundLaunchDenied
        raw_session = secrets.token_urlsafe(32)
        row = InstallationBoundSession(
            token_hash=hashlib.sha256(raw_session.encode()).hexdigest(), launch_jti_hash=jti_hash,
            correlation_id=str(claims["correlation_id"]), issuer=self._issuer, user_id=user_id,
            tenant_id=tenant_id, effective_installation_id=binding.installation_id,
            capabilities_json=sorted(effective),
            expires_at=(expires if prepared else now + BOUND_SESSION_IDLE_TTL),
        )
        self._session.add(row)
        await self._session.flush()
        self._session.add(AuditEvent(
            tenant_id=tenant_id,
            installation_id=binding.installation_id,
            user_id=user_id,
            source="econtrol_bounded",
            event_type="bounded.launch",
            request_id=str(claims["correlation_id"]),
            payload_redacted_json={
                "issuer": self._issuer,
                "jti_hash": jti_hash,
                "capabilities": sorted(effective),
            },
            result="success",
        ))
        return raw_session, BoundContext(row.id, user_id, tenant_id, binding.installation_id, effective, row.correlation_id)

    async def prepare(self, token: str, *, now: datetime | None = None) -> str:
        """Validate the signed launch and replace it with an opaque URL code."""
        now = now or datetime.now(UTC)
        _raw, context = await self.exchange(token, now=now, prepared=True)
        code = secrets.token_urlsafe(32)
        row = await self._session.get(InstallationBoundSession, context.session_id)
        if row is None:
            raise BoundLaunchDenied
        row.token_hash = hashlib.sha256(code.encode()).hexdigest()
        await self._session.flush()
        return code

    async def open_prepared(self, code: str, *, now: datetime | None = None) -> str:
        """Consume a prepared code once and rotate it to a browser session token."""
        now = now or datetime.now(UTC)
        if not isinstance(code, str) or len(code) < 32 or len(code) > 128:
            raise BoundLaunchDenied
        raw_session = secrets.token_urlsafe(32)
        result = await self._session.execute(
            update(InstallationBoundSession)
            .where(
                InstallationBoundSession.token_hash == hashlib.sha256(code.encode()).hexdigest(),
                InstallationBoundSession.revoked_at.is_(None),
                InstallationBoundSession.expires_at > now,
            )
            .values(
                token_hash=hashlib.sha256(raw_session.encode()).hexdigest(),
                expires_at=now + BOUND_SESSION_IDLE_TTL,
            )
            .returning(InstallationBoundSession.id)
        )
        if result.scalar_one_or_none() is None:
            raise BoundLaunchDenied
        await self._session.flush()
        return raw_session

    async def context(self, raw_session: str, *, now: datetime | None = None) -> BoundContext:
        now = now or datetime.now(UTC)
        row = await self._session.scalar(select(InstallationBoundSession).where(
            InstallationBoundSession.token_hash == hashlib.sha256(raw_session.encode()).hexdigest(),
            InstallationBoundSession.revoked_at.is_(None),
            InstallationBoundSession.expires_at > now,
        ))
        if row is None:
            raise BoundLaunchDenied
        try:
            auth_context = await AuthenticationService(self._session).tenant_context(
                user_id=row.user_id, tenant_id=row.tenant_id
            )
        except AccessDeniedError as error:
            raise BoundLaunchDenied from error
        binding_active = await self._session.scalar(select(ExternalInstallationBinding.id).where(
            ExternalInstallationBinding.issuer == row.issuer,
            ExternalInstallationBinding.tenant_id == row.tenant_id,
            ExternalInstallationBinding.installation_id == row.effective_installation_id,
            ExternalInstallationBinding.revoked_at.is_(None),
        ))
        if binding_active is None:
            raise BoundLaunchDenied
        assignment = await self._session.scalar(select(UserInstallationAssignment).where(
            UserInstallationAssignment.tenant_id == row.tenant_id,
            UserInstallationAssignment.user_id == row.user_id,
            UserInstallationAssignment.installation_id == row.effective_installation_id,
            UserInstallationAssignment.revoked_at.is_(None),
        ))
        if assignment is None:
            raise BoundLaunchDenied
        capabilities = (
            frozenset(row.capabilities_json)
            & ACCESS_CAPABILITIES.get(assignment.access_level, frozenset())
            & ROLE_CAPABILITIES[auth_context.role]
        )
        if not capabilities:
            raise BoundLaunchDenied
        row.expires_at = now + BOUND_SESSION_IDLE_TTL
        return BoundContext(row.id, row.user_id, row.tenant_id, row.effective_installation_id, capabilities, row.correlation_id)

    async def revoke(self, raw_session: str, *, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        row = await self._session.scalar(select(InstallationBoundSession).where(
            InstallationBoundSession.token_hash == hashlib.sha256(raw_session.encode()).hexdigest(),
            InstallationBoundSession.revoked_at.is_(None),
        ))
        if row is not None:
            row.revoked_at = now
            await self._session.flush()


class BoundedResourceService:
    """Central query gate for every object reachable from a bounded session."""

    def __init__(self, session: AsyncSession, context: BoundContext) -> None:
        self._session = session
        self.context = context

    async def installation(self, installation_id: UUID) -> Installation:
        if installation_id != self.context.effective_installation_id:
            raise BoundLaunchDenied
        item = await self._session.scalar(select(Installation).where(
            Installation.id == installation_id,
            Installation.tenant_id == self.context.tenant_id,
            Installation.revoked_at.is_(None),
        ))
        if item is None:
            raise BoundLaunchDenied
        return item

    async def entities(self) -> list[Entity]:
        return list((await self._session.scalars(
            select(Entity)
            .options(selectinload(Entity.voice_category))
            .join(Installation, Installation.id == Entity.installation_id)
            .where(
                Installation.tenant_id == self.context.tenant_id,
                Entity.installation_id == self.context.effective_installation_id,
            )
        )).all())

    async def entity(self, entity_id: UUID) -> Entity:
        item = await self._session.scalar(
            select(Entity)
            .join(Installation, Installation.id == Entity.installation_id)
            .where(
                Entity.id == entity_id,
                Installation.tenant_id == self.context.tenant_id,
                Entity.installation_id == self.context.effective_installation_id,
            )
        )
        if item is None:
            raise BoundLaunchDenied
        return item

    async def publication(self, publication_id: UUID) -> AlexaPublication:
        item = await self._session.scalar(
            select(AlexaPublication)
            .join(Entity, Entity.id == AlexaPublication.entity_id)
            .join(Installation, Installation.id == Entity.installation_id)
            .where(
                AlexaPublication.id == publication_id,
                Installation.tenant_id == self.context.tenant_id,
                Entity.installation_id == self.context.effective_installation_id,
            )
        )
        if item is None:
            raise BoundLaunchDenied
        return item

    async def routines(self) -> list[AlexaVoiceRoutine]:
        return list((await self._session.scalars(select(AlexaVoiceRoutine).where(
            AlexaVoiceRoutine.tenant_id == self.context.tenant_id,
            AlexaVoiceRoutine.installation_id == self.context.effective_installation_id,
        ))).all())

    async def speaker_groups(self) -> list[AlexaSpeakerGroup]:
        return list((await self._session.scalars(select(AlexaSpeakerGroup).where(
            AlexaSpeakerGroup.tenant_id == self.context.tenant_id,
            AlexaSpeakerGroup.installation_id == self.context.effective_installation_id,
        ))).all())

    async def routine_executions(self) -> list[AlexaRoutineExecution]:
        return list((await self._session.scalars(select(AlexaRoutineExecution).where(
            AlexaRoutineExecution.tenant_id == self.context.tenant_id,
            AlexaRoutineExecution.installation_id == self.context.effective_installation_id,
        ))).all())

    async def state_history(self) -> list[EntityStateHistory]:
        return list((await self._session.scalars(select(EntityStateHistory).where(
            EntityStateHistory.tenant_id == self.context.tenant_id,
            EntityStateHistory.installation_id == self.context.effective_installation_id,
        ))).all())

    async def operational_events(self) -> list[OperationalEvent]:
        return list((await self._session.scalars(select(OperationalEvent).where(
            OperationalEvent.tenant_id == self.context.tenant_id,
            OperationalEvent.installation_id == self.context.effective_installation_id,
        ))).all())

    async def audit_events(self) -> list[AuditEvent]:
        return list((await self._session.scalars(select(AuditEvent).where(
            AuditEvent.tenant_id == self.context.tenant_id,
            AuditEvent.installation_id == self.context.effective_installation_id,
        ))).all())

    def authorize_installation(self, installation_id: UUID, capability: str) -> None:
        self.context.require(capability)
        if installation_id != self.context.effective_installation_id:
            raise BoundLaunchDenied

    async def mutate(self, installation_id: UUID, callback: Any) -> Any:
        """Run a command only after command capability and exact A/B binding checks."""
        self.authorize_installation(installation_id, "command")
        return await callback()
