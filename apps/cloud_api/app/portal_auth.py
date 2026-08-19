"""Minimal local authentication and opaque server-side portal sessions."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AccessDeniedError, AuthenticationService, TenantContext
from .config import get_settings
from .domain.enums import RecordStatus
from .domain.models import (
    PortalLoginAttempt,
    PortalSession,
    Tenant,
    TenantMembership,
    User,
)

password_hash = PasswordHash.recommended()
SESSION_PREFIX = "evs_"
LOGIN_WINDOW = timedelta(minutes=15)
MAX_LOGIN_FAILURES = 5


class LoginRateLimitedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PortalIdentity:
    user: User
    session: PortalSession
    memberships: tuple[TenantMembership, ...]
    context: TenantContext | None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 1024:
        raise ValueError("password must contain 12 to 1024 characters")
    return password_hash.hash(password)


class PortalAuthenticationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def login(self, *, email: str, password: str) -> tuple[str, PortalIdentity] | None:
        normalized = email.strip().lower()
        email_hash = _digest(normalized)
        since = datetime.now(UTC) - LOGIN_WINDOW
        failures = await self._session.scalar(
            select(func.count(PortalLoginAttempt.id)).where(
                PortalLoginAttempt.email_hash == email_hash,
                PortalLoginAttempt.successful.is_(False),
                PortalLoginAttempt.attempted_at >= since,
            )
        )
        if int(failures or 0) >= MAX_LOGIN_FAILURES:
            raise LoginRateLimitedError
        user = await self._session.scalar(
            select(User).where(User.email == normalized, User.status == RecordStatus.ACTIVE)
        )
        valid = False
        updated_hash: str | None = None
        if user is not None:
            try:
                valid, updated_hash = password_hash.verify_and_update(password, user.password_hash)
            except Exception:  # malformed legacy hashes fail closed
                valid = False
        else:
            password_hash.verify(password, password_hash.hash("dummy-password-not-a-credential"))
        self._session.add(
            PortalLoginAttempt(
                email_hash=email_hash, successful=valid, attempted_at=datetime.now(UTC)
            )
        )
        if not valid or user is None:
            await self._session.commit()
            return None
        if updated_hash is not None:
            user.password_hash = updated_hash
        memberships = await self._memberships(user.id)
        if not memberships:
            await self._session.commit()
            return None
        now = datetime.now(UTC)
        token = SESSION_PREFIX + secrets.token_urlsafe(48)
        portal_session = PortalSession(
            user_id=user.id,
            token_hash=_digest(token),
            selected_tenant_id=memberships[0].tenant_id if len(memberships) == 1 else None,
            expires_at=now + timedelta(hours=get_settings().pairing_portal_session_hours),
            last_seen_at=now,
        )
        user.last_login_at = now
        self._session.add(portal_session)
        await self._session.commit()
        identity = await self.resolve(token)
        if identity is None:
            raise RuntimeError("new portal session could not be resolved")
        return token, identity

    async def resolve(self, token: str | None) -> PortalIdentity | None:
        if token is None or not token.startswith(SESSION_PREFIX):
            return None
        now = datetime.now(UTC)
        portal_session = await self._session.scalar(
            select(PortalSession).where(
                PortalSession.token_hash == _digest(token),
                PortalSession.revoked_at.is_(None),
                PortalSession.expires_at > now,
            )
        )
        if portal_session is None:
            return None
        user = await self._session.get(User, portal_session.user_id)
        if user is None or user.status is not RecordStatus.ACTIVE:
            return None
        memberships = await self._memberships(user.id)
        context = None
        if portal_session.selected_tenant_id is not None:
            try:
                context = await AuthenticationService(self._session).tenant_context(
                    user_id=user.id, tenant_id=portal_session.selected_tenant_id
                )
            except AccessDeniedError:
                portal_session.selected_tenant_id = None
        portal_session.last_seen_at = now
        await self._session.commit()
        return PortalIdentity(user, portal_session, tuple(memberships), context)

    async def select_tenant(self, identity: PortalIdentity, tenant_id: UUID) -> PortalIdentity:
        await AuthenticationService(self._session).tenant_context(
            user_id=identity.user.id, tenant_id=tenant_id
        )
        identity.session.selected_tenant_id = tenant_id
        await self._session.commit()
        refreshed = await self.resolve_by_session(identity.session)
        if refreshed is None:
            raise AccessDeniedError
        return refreshed

    async def resolve_by_session(self, portal_session: PortalSession) -> PortalIdentity | None:
        user = await self._session.get(User, portal_session.user_id)
        if user is None or user.status is not RecordStatus.ACTIVE:
            return None
        memberships = await self._memberships(user.id)
        context = None
        if portal_session.selected_tenant_id is not None:
            context = await AuthenticationService(self._session).tenant_context(
                user_id=user.id, tenant_id=portal_session.selected_tenant_id
            )
        return PortalIdentity(user, portal_session, tuple(memberships), context)

    async def logout(self, identity: PortalIdentity) -> None:
        identity.session.revoked_at = datetime.now(UTC)
        await self._session.commit()

    async def _memberships(self, user_id: UUID) -> list[TenantMembership]:
        return list(
            (
                await self._session.scalars(
                    select(TenantMembership)
                    .join(Tenant)
                    .where(
                        TenantMembership.user_id == user_id,
                        Tenant.status == RecordStatus.ACTIVE,
                    )
                    .order_by(Tenant.name)
                )
            ).all()
        )
