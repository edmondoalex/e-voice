import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.auth import AccessDeniedError, AuthenticationService, TenantContext
from apps.cloud_api.app.domain.enums import PairingStatus
from apps.cloud_api.app.domain.models import (
    AuditEvent,
    ConnectorCredential,
    Installation,
    PairingClaimAttempt,
    PairingSession,
)
from apps.cloud_api.app.pairing import (
    PairingAccessDeniedError,
    PairingExpiredError,
    PairingRateLimitedError,
    PairingService,
    PairingUnavailableError,
)
from apps.cloud_api.app.services import ResourceNotFoundError

from .conftest import SeededDomain


@dataclass(slots=True)
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 8, 17, 12, 0, tzinfo=UTC))


@pytest.fixture
def pairing_service(session: AsyncSession, clock: MutableClock) -> PairingService:
    return PairingService(
        session,
        code_pepper=b"test-only-pairing-pepper-32-bytes-minimum",
        delivery_key=Fernet.generate_key(),
        clock=clock,
    )


async def owner_context(
    session: AsyncSession, seeded_domain: SeededDomain, *, tenant: str = "a"
) -> TenantContext:
    user_id = seeded_domain.user_a_id if tenant == "a" else seeded_domain.user_b_id
    tenant_id = seeded_domain.tenant_a_id if tenant == "a" else seeded_domain.tenant_b_id
    return await AuthenticationService(session).tenant_context(user_id=user_id, tenant_id=tenant_id)


async def test_pairing_code_and_polling_secret_are_stored_only_as_hashes(
    session: AsyncSession, pairing_service: PairingService
) -> None:
    started = await pairing_service.create_session(installation_nonce="haos-installation-0001")
    stored = await session.get(PairingSession, started.session_id)

    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", started.code)
    assert started.polling_secret.startswith("evp_")
    assert stored is not None
    assert stored.code_hash != started.code
    assert stored.polling_secret_hash != started.polling_secret
    assert len(stored.code_hash) == 64
    assert len(stored.polling_secret_hash) == 64


async def test_claim_issues_connector_credential_for_one_time_delivery(
    session: AsyncSession,
    seeded_domain: SeededDomain,
    pairing_service: PairingService,
) -> None:
    context = await owner_context(session, seeded_domain)
    started = await pairing_service.create_session(installation_nonce="haos-installation-0002")

    claimed = await pairing_service.claim(
        context, code=started.code, installation_name="Villa Rossi"
    )
    first_poll = await pairing_service.poll(
        session_id=started.session_id, polling_secret=started.polling_secret
    )
    second_poll = await pairing_service.poll(
        session_id=started.session_id, polling_secret=started.polling_secret
    )

    assert claimed.tenant_id == seeded_domain.tenant_a_id
    assert first_poll.status is PairingStatus.CLAIMED
    assert first_poll.installation_id == claimed.installation_id
    assert first_poll.connector_credential is not None
    assert first_poll.connector_credential.startswith("evc_")
    assert first_poll.connector_credential not in {started.code, started.polling_secret}
    assert second_poll.connector_credential is None

    credential = await session.scalar(
        select(ConnectorCredential).where(
            ConnectorCredential.installation_id == claimed.installation_id
        )
    )
    assert credential is not None
    assert (
        credential.secret_hash
        == hashlib.sha256(first_poll.connector_credential.encode()).hexdigest()
    )
    assert first_poll.connector_credential != credential.secret_hash


async def test_expired_code_cannot_be_claimed(
    session: AsyncSession,
    seeded_domain: SeededDomain,
    pairing_service: PairingService,
    clock: MutableClock,
) -> None:
    context = await owner_context(session, seeded_domain)
    started = await pairing_service.create_session(installation_nonce="haos-installation-0003")
    clock.advance(timedelta(minutes=11))

    with pytest.raises(PairingExpiredError):
        await pairing_service.claim(context, code=started.code, installation_name="Expired")

    pairing = await session.get(PairingSession, started.session_id)
    assert pairing is not None
    assert pairing.status is PairingStatus.EXPIRED


async def test_claim_is_one_time_and_replay_cannot_move_installation_between_tenants(
    session: AsyncSession,
    seeded_domain: SeededDomain,
    pairing_service: PairingService,
) -> None:
    context_a = await owner_context(session, seeded_domain, tenant="a")
    context_b = await owner_context(session, seeded_domain, tenant="b")
    started = await pairing_service.create_session(installation_nonce="haos-installation-0004")
    claimed = await pairing_service.claim(
        context_a, code=started.code, installation_name="Tenant A Home"
    )

    with pytest.raises(PairingUnavailableError):
        await pairing_service.claim(
            context_b, code=started.code, installation_name="Tenant B Replay"
        )

    installation = await session.get(Installation, claimed.installation_id)
    assert installation is not None
    assert installation.tenant_id == seeded_domain.tenant_a_id


async def test_brute_force_limit_blocks_even_a_later_correct_code(
    session: AsyncSession,
    seeded_domain: SeededDomain,
    clock: MutableClock,
) -> None:
    service = PairingService(
        session,
        code_pepper=b"test-only-pairing-pepper-32-bytes-minimum",
        delivery_key=Fernet.generate_key(),
        max_failed_attempts=3,
        clock=clock,
    )
    context = await owner_context(session, seeded_domain)
    started = await service.create_session(installation_nonce="haos-installation-0005")

    for code in ("AAAA-AAAA", "BBBB-BBBB", "CCCC-CCCC"):
        with pytest.raises(PairingUnavailableError):
            await service.claim(context, code=code, installation_name="Guess")

    with pytest.raises(PairingRateLimitedError):
        await service.claim(context, code=started.code, installation_name="Correct but blocked")

    await session.rollback()
    failed_count = await session.scalar(
        select(func.count(PairingClaimAttempt.id)).where(
            PairingClaimAttempt.user_id == context.user_id,
            PairingClaimAttempt.successful.is_(False),
        )
    )
    assert failed_count == 3


async def test_user_cannot_claim_for_tenant_without_membership(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    with pytest.raises(AccessDeniedError):
        await AuthenticationService(session).tenant_context(
            user_id=seeded_domain.user_a_id, tenant_id=seeded_domain.tenant_b_id
        )


async def test_polling_requires_the_independent_secret(
    pairing_service: PairingService,
) -> None:
    started = await pairing_service.create_session(installation_nonce="haos-installation-0006")

    with pytest.raises(PairingAccessDeniedError):
        await pairing_service.poll(session_id=started.session_id, polling_secret="evp_wrong-secret")


async def test_new_session_for_same_installation_nonce_locks_previous_session(
    pairing_service: PairingService,
) -> None:
    first = await pairing_service.create_session(installation_nonce="haos-installation-0009")
    second = await pairing_service.create_session(installation_nonce="haos-installation-0009")

    first_status = await pairing_service.poll(
        session_id=first.session_id, polling_secret=first.polling_secret
    )
    second_status = await pairing_service.poll(
        session_id=second.session_id, polling_secret=second.polling_secret
    )

    assert first_status.status is PairingStatus.LOCKED
    assert second_status.status is PairingStatus.PENDING


async def test_connector_credential_can_be_rotated_and_revoked_tenant_safely(
    session: AsyncSession,
    seeded_domain: SeededDomain,
    pairing_service: PairingService,
) -> None:
    context_a = await owner_context(session, seeded_domain, tenant="a")
    context_b = await owner_context(session, seeded_domain, tenant="b")
    started = await pairing_service.create_session(installation_nonce="haos-installation-0007")
    claimed = await pairing_service.claim(
        context_a, code=started.code, installation_name="Rotating Home"
    )
    original = await session.scalar(
        select(ConnectorCredential).where(
            ConnectorCredential.installation_id == claimed.installation_id
        )
    )
    assert original is not None

    rotated = await pairing_service.rotate_credential(
        context_a, installation_id=claimed.installation_id
    )
    assert rotated.secret.startswith("evc_")
    assert rotated.credential_id != original.id
    assert original.revoked_at is not None

    with pytest.raises(ResourceNotFoundError):
        await pairing_service.revoke_credential(context_b, credential_id=rotated.credential_id)

    await pairing_service.revoke_credential(context_a, credential_id=rotated.credential_id)
    current = await session.get(ConnectorCredential, rotated.credential_id)
    assert current is not None
    assert current.revoked_at is not None


async def test_pairing_audit_contains_no_codes_or_credentials(
    session: AsyncSession,
    seeded_domain: SeededDomain,
    pairing_service: PairingService,
) -> None:
    context = await owner_context(session, seeded_domain)
    started = await pairing_service.create_session(installation_nonce="haos-installation-0008")
    await pairing_service.claim(context, code=started.code, installation_name="Audited Home")
    polled = await pairing_service.poll(
        session_id=started.session_id, polling_secret=started.polling_secret
    )
    audits = list((await session.scalars(select(AuditEvent))).all())
    rendered = repr(
        [(audit.event_type, audit.result, audit.payload_redacted_json) for audit in audits]
    )

    assert audits
    assert started.code not in rendered
    assert started.polling_secret not in rendered
    assert polled.connector_credential is not None
    assert polled.connector_credential not in rendered
    assert all(audit.payload_redacted_json == {} for audit in audits)
