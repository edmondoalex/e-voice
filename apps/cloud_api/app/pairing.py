"""Provider-neutral secure installation pairing for M2."""

import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import TenantContext
from .domain.enums import InstallationStatus, PairingStatus
from .domain.models import (
    AuditEvent,
    ConnectorCredential,
    Installation,
    PairingClaimAttempt,
    PairingSession,
)
from .repositories import ConnectorCredentialRepository, InstallationRepository, PairingRepository
from .services import PUBLICATION_WRITE_ROLES, OperationNotAllowedError, ResourceNotFoundError

PAIRING_CODE_PATTERN = re.compile(r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class PairingError(Exception):
    """Base class for pairing failures safe to map to generic API errors."""


class PairingAccessDeniedError(PairingError):
    pass


class PairingExpiredError(PairingError):
    pass


class PairingRateLimitedError(PairingError):
    pass


class PairingUnavailableError(PairingError):
    """Invalid, already claimed, locked, or otherwise unavailable code."""


@dataclass(frozen=True, slots=True)
class PairingStart:
    session_id: UUID
    code: str
    polling_secret: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PairingClaim:
    session_id: UUID
    installation_id: UUID
    tenant_id: UUID


@dataclass(frozen=True, slots=True)
class PairingPoll:
    status: PairingStatus
    installation_id: UUID | None = None
    connector_credential: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedCredential:
    credential_id: UUID
    installation_id: UUID
    secret: str


class PairingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        code_pepper: bytes,
        delivery_key: bytes,
        ttl: timedelta = timedelta(minutes=10),
        attempt_window: timedelta = timedelta(minutes=15),
        max_failed_attempts: int = 5,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(code_pepper) < 32:
            raise ValueError("code_pepper must contain at least 32 bytes")
        if ttl <= timedelta(0) or attempt_window <= timedelta(0):
            raise ValueError("pairing time windows must be positive")
        if max_failed_attempts < 1:
            raise ValueError("max_failed_attempts must be positive")
        self._session = session
        self._pairings = PairingRepository(session)
        self._installations = InstallationRepository(session)
        self._credentials = ConnectorCredentialRepository(session)
        self._code_pepper = code_pepper
        self._delivery_cipher = Fernet(delivery_key)
        self._ttl = ttl
        self._attempt_window = attempt_window
        self._max_failed_attempts = max_failed_attempts
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return self._clock()

    @staticmethod
    def _is_expired(expires_at: datetime, now: datetime) -> bool:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= now

    def _code_hash(self, code: str) -> str:
        normalized = code.strip().upper().encode("ascii", errors="ignore")
        return hmac.new(self._code_pepper, normalized, hashlib.sha256).hexdigest()

    @staticmethod
    def _secret_hash(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    @staticmethod
    def _new_pairing_code() -> str:
        value = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        return f"{value[:4]}-{value[4:]}"

    @staticmethod
    def _new_polling_secret() -> str:
        return f"evp_{secrets.token_urlsafe(32)}"

    @staticmethod
    def _new_connector_secret() -> str:
        return f"evc_{secrets.token_urlsafe(32)}"

    async def create_session(self, *, installation_nonce: str) -> PairingStart:
        nonce = installation_nonce.strip()
        if not 16 <= len(nonce) <= 255:
            raise ValueError("installation_nonce must contain 16 to 255 characters")
        now = self._now()
        existing = await self._pairings.pending_for_nonce(installation_nonce=nonce, now=now)
        if existing is not None:
            existing.status = PairingStatus.LOCKED

        for _ in range(5):
            code = self._new_pairing_code()
            code_hash = self._code_hash(code)
            if not await self._pairings.code_hash_exists(code_hash):
                break
        else:
            raise RuntimeError("unable to allocate a unique pairing code")

        polling_secret = self._new_polling_secret()
        pairing = PairingSession(
            code_hash=code_hash,
            polling_secret_hash=self._secret_hash(polling_secret),
            installation_nonce=nonce,
            expires_at=now + self._ttl,
            status=PairingStatus.PENDING,
        )
        self._session.add(pairing)
        await self._session.commit()
        return PairingStart(
            session_id=pairing.id,
            code=code,
            polling_secret=polling_secret,
            expires_at=pairing.expires_at,
        )

    async def claim(
        self, context: TenantContext, *, code: str, installation_name: str
    ) -> PairingClaim:
        self._ensure_credential_write_allowed(context)
        now = self._now()
        await self._enforce_rate_limit(context, now)
        normalized_code = code.strip().upper()
        pairing = None
        if PAIRING_CODE_PATTERN.fullmatch(normalized_code):
            pairing = await self._pairings.get_by_code_hash_for_update(
                self._code_hash(normalized_code)
            )

        if pairing is None:
            await self._record_attempt(context, now=now, result="invalid_code")
            await self._session.commit()
            raise PairingUnavailableError
        if pairing.status is not PairingStatus.PENDING:
            await self._record_attempt(
                context, now=now, result="replay", pairing_session_id=pairing.id
            )
            await self._session.commit()
            raise PairingUnavailableError
        if self._is_expired(pairing.expires_at, now):
            pairing.status = PairingStatus.EXPIRED
            await self._record_attempt(
                context, now=now, result="expired", pairing_session_id=pairing.id
            )
            await self._session.commit()
            raise PairingExpiredError

        name = installation_name.strip()
        if not 1 <= len(name) <= 200:
            raise ValueError("installation_name must contain 1 to 200 characters")

        installation = Installation(
            tenant_id=context.tenant_id,
            name=name,
            public_id=f"evi_{secrets.token_urlsafe(18)}",
            status=InstallationStatus.ACTIVE,
            ha_installation_type="haos",
        )
        self._session.add(installation)
        await self._session.flush()

        connector_secret = self._new_connector_secret()
        credential = ConnectorCredential(
            installation_id=installation.id,
            secret_hash=self._secret_hash(connector_secret),
        )
        self._session.add(credential)
        await self._session.flush()

        pairing.status = PairingStatus.CLAIMED
        pairing.claimed_by_user_id = context.user_id
        pairing.claimed_tenant_id = context.tenant_id
        pairing.claimed_installation_id = installation.id
        pairing.connector_credential_id = credential.id
        pairing.credential_envelope = self._delivery_cipher.encrypt(connector_secret.encode())
        pairing.claimed_at = now
        await self._record_attempt(
            context,
            now=now,
            result="success",
            pairing_session_id=pairing.id,
            successful=True,
            installation_id=installation.id,
        )
        await self._session.commit()
        return PairingClaim(
            session_id=pairing.id,
            installation_id=installation.id,
            tenant_id=context.tenant_id,
        )

    async def poll(self, *, session_id: UUID, polling_secret: str) -> PairingPoll:
        pairing = await self._pairings.get_by_id_for_update(session_id)
        supplied_hash = self._secret_hash(polling_secret)
        if pairing is None or not hmac.compare_digest(pairing.polling_secret_hash, supplied_hash):
            raise PairingAccessDeniedError

        now = self._now()
        if pairing.status is PairingStatus.PENDING and self._is_expired(pairing.expires_at, now):
            pairing.status = PairingStatus.EXPIRED
            await self._session.commit()
        if pairing.status is not PairingStatus.CLAIMED:
            return PairingPoll(status=pairing.status)
        if pairing.credential_envelope is None:
            return PairingPoll(
                status=pairing.status, installation_id=pairing.claimed_installation_id
            )

        try:
            connector_secret = self._delivery_cipher.decrypt(pairing.credential_envelope).decode()
        except (InvalidToken, UnicodeDecodeError) as error:
            raise PairingAccessDeniedError from error
        pairing.credential_envelope = None
        pairing.credential_delivered_at = now
        await self._session.commit()
        return PairingPoll(
            status=pairing.status,
            installation_id=pairing.claimed_installation_id,
            connector_credential=connector_secret,
        )

    async def revoke_credential(self, context: TenantContext, *, credential_id: UUID) -> None:
        self._ensure_credential_write_allowed(context)
        credential = await self._credentials.get(
            tenant_id=context.tenant_id, credential_id=credential_id
        )
        if credential is None:
            raise ResourceNotFoundError
        if credential.revoked_at is None:
            credential.revoked_at = self._now()
            self._audit(
                context,
                event_type="connector_credential.revoked",
                result="success",
                installation_id=credential.installation_id,
            )
            await self._session.commit()

    async def rotate_credential(
        self, context: TenantContext, *, installation_id: UUID
    ) -> IssuedCredential:
        self._ensure_credential_write_allowed(context)
        installation = await self._installations.get(
            tenant_id=context.tenant_id, installation_id=installation_id
        )
        if installation is None:
            raise ResourceNotFoundError
        now = self._now()
        active_credentials = await self._credentials.active_for_installation(
            tenant_id=context.tenant_id, installation_id=installation_id
        )
        rotated_from_id = None
        for credential in active_credentials:
            credential.revoked_at = now
            rotated_from_id = credential.id

        secret = self._new_connector_secret()
        credential = ConnectorCredential(
            installation_id=installation_id,
            secret_hash=self._secret_hash(secret),
            rotated_from_id=rotated_from_id,
        )
        self._session.add(credential)
        self._audit(
            context,
            event_type="connector_credential.rotated",
            result="success",
            installation_id=installation_id,
        )
        await self._session.commit()
        return IssuedCredential(
            credential_id=credential.id, installation_id=installation_id, secret=secret
        )

    async def _enforce_rate_limit(self, context: TenantContext, now: datetime) -> None:
        count = await self._pairings.recent_failed_attempts(
            user_id=context.user_id, since=now - self._attempt_window
        )
        if count >= self._max_failed_attempts:
            self._audit(context, event_type="pairing.claim", result="rate_limited")
            await self._session.commit()
            raise PairingRateLimitedError

    async def _record_attempt(
        self,
        context: TenantContext,
        *,
        now: datetime,
        result: str,
        pairing_session_id: UUID | None = None,
        successful: bool = False,
        installation_id: UUID | None = None,
    ) -> None:
        self._session.add(
            PairingClaimAttempt(
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                pairing_session_id=pairing_session_id,
                successful=successful,
                result=result,
                attempted_at=now,
            )
        )
        self._audit(
            context,
            event_type="pairing.claim",
            result=result,
            installation_id=installation_id,
        )
        await self._session.flush()

    def _audit(
        self,
        context: TenantContext,
        *,
        event_type: str,
        result: str,
        installation_id: UUID | None = None,
    ) -> None:
        self._session.add(
            AuditEvent(
                tenant_id=context.tenant_id,
                installation_id=installation_id,
                user_id=context.user_id,
                source="cloud_api",
                event_type=event_type,
                payload_redacted_json={},
                result=result,
            )
        )

    @staticmethod
    def _ensure_credential_write_allowed(context: TenantContext) -> None:
        if context.role not in PUBLICATION_WRITE_ROLES:
            raise OperationNotAllowedError
