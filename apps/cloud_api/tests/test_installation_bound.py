from datetime import UTC, datetime, timedelta

import pytest

from apps.cloud_api.app.domain.models import (
    AlexaRoutineExecution,
    AlexaSpeakerGroup,
    AlexaVoiceRoutine,
    AuditEvent,
    EntityStateHistory,
    ExternalInstallationBinding,
    InstallationBoundSession,
    OperationalEvent,
    UserInstallationAssignment,
)
from apps.cloud_api.app.domain.enums import RecordStatus
from apps.cloud_api.app.domain.models import User
from apps.cloud_api.app.installation_bound import (
    BoundedResourceService,
    BoundLaunchDenied,
    InstallationBoundService,
    encode_launch,
)

SECRET = b"synthetic-change028-secret"
ISSUER = "econtrol-test"
AUDIENCE = "evoice-test"


def _claims(seeded, now, **updates):
    claims = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "subject": str(seeded.user_a_id),
        "tenant_id": str(seeded.tenant_a_id),
        "installation_id": "econtrol-a",
        "requested_capabilities": ["readonly", "use", "command", "stream"],
        "issued_at": int(now.timestamp()),
        "expires_at": int((now + timedelta(minutes=2)).timestamp()),
        "jti": "jti-a",
        "correlation_id": "corr-a",
    }
    claims.update(updates)
    return claims


async def _grant(session, seeded, level="use"):
    session.add_all([
        ExternalInstallationBinding(
            issuer=ISSUER,
            external_installation_id="econtrol-a",
            tenant_id=seeded.tenant_a_id,
            installation_id=seeded.installation_a_id,
        ),
        UserInstallationAssignment(
            tenant_id=seeded.tenant_a_id,
            user_id=seeded.user_a_id,
            installation_id=seeded.installation_a_id,
            access_level=level,
            granted_by_user_id=seeded.user_a_id,
        ),
    ])
    await session.commit()


async def test_admin_global_role_is_still_bounded_to_a(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    raw, context = await service.exchange(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    assert context.effective_installation_id == seeded_domain.installation_a_id
    assert seeded_domain.installation_b_id != context.effective_installation_id
    assert (await service.context(raw, now=now)).effective_installation_id == seeded_domain.installation_a_id


async def test_browser_session_uses_sliding_thirty_minute_idle_timeout(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    raw, _context = await service.exchange(
        encode_launch(_claims(seeded_domain, now), SECRET), now=now
    )
    await service.context(raw, now=now + timedelta(minutes=29))
    await service.context(raw, now=now + timedelta(minutes=58))
    with pytest.raises(BoundLaunchDenied):
        await service.context(raw, now=now + timedelta(minutes=89))


async def test_prepared_code_keeps_launch_expiry_then_opens_browser_session(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    code = await service.prepare(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    raw = await service.open_prepared(code, now=now + timedelta(minutes=1))
    assert (await service.context(raw, now=now + timedelta(minutes=25))).effective_installation_id == seeded_domain.installation_a_id


async def test_unused_prepared_code_expires_with_launch(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    code = await service.prepare(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    with pytest.raises(BoundLaunchDenied):
        await service.open_prepared(code, now=now + timedelta(minutes=3))


async def test_launch_replay_is_denied(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    token = encode_launch(_claims(seeded_domain, now), SECRET)
    await service.exchange(token, now=now)
    with pytest.raises(BoundLaunchDenied):
        await service.exchange(token, now=now)


@pytest.mark.parametrize("mutation", [
    {"audience": "wrong"},
    {"installation_id": "econtrol-b"},
    {"tenant_id": "00000000-0000-0000-0000-000000000001"},
    {"expires_at": 1},
    {"requested_capabilities": ["superuser"]},
])
async def test_tampered_or_invalid_launch_fails_closed(session, seeded_domain, mutation):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    with pytest.raises(BoundLaunchDenied):
        await service.exchange(encode_launch(_claims(seeded_domain, now, **mutation), SECRET), now=now)


async def test_readonly_assignment_intersects_requested_capabilities(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain, "readonly")
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    _, context = await service.exchange(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    assert context.capabilities == frozenset({"readonly", "stream"})
    with pytest.raises(BoundLaunchDenied):
        context.require("command")


async def test_readonly_role_cannot_be_amplified_by_manage_assignment(session, seeded_domain):
    now = datetime.now(UTC)
    session.add_all([
        ExternalInstallationBinding(
            issuer=ISSUER, external_installation_id="econtrol-a",
            tenant_id=seeded_domain.tenant_a_id,
            installation_id=seeded_domain.installation_a_id,
        ),
        UserInstallationAssignment(
            tenant_id=seeded_domain.tenant_a_id,
            user_id=seeded_domain.user_readonly_id,
            installation_id=seeded_domain.installation_a_id,
            access_level="manage",
            granted_by_user_id=seeded_domain.user_a_id,
        ),
    ])
    await session.commit()
    claims = _claims(seeded_domain, now, subject=str(seeded_domain.user_readonly_id))
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    _, context = await service.exchange(encode_launch(claims, SECRET), now=now)
    assert context.capabilities == frozenset({"readonly", "stream"})


async def test_assignment_revocation_invalidates_existing_session(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    raw, _ = await service.exchange(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    assignment = await session.scalar(__import__("sqlalchemy").select(UserInstallationAssignment))
    assignment.revoked_at = now
    await session.commit()
    with pytest.raises(BoundLaunchDenied):
        await service.context(raw, now=now)


async def test_token_is_stored_only_as_hash(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    raw, _ = await service.exchange(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    stored = await session.scalar(__import__("sqlalchemy").select(InstallationBoundSession))
    assert raw not in stored.token_hash
    assert len(stored.token_hash) == 64


async def test_indirect_entity_and_publication_ids_cannot_cross_to_b(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    _, context = await service.exchange(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    resources = BoundedResourceService(session, context)
    assert (await resources.entity(seeded_domain.entity_a_id)).id == seeded_domain.entity_a_id
    assert (await resources.publication(seeded_domain.publication_a_id)).id == seeded_domain.publication_a_id
    with pytest.raises(BoundLaunchDenied):
        await resources.entity(seeded_domain.entity_b_id)
    with pytest.raises(BoundLaunchDenied):
        await resources.publication(seeded_domain.publication_b_id)


async def test_bounded_lists_contain_zero_records_from_b(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    _, context = await service.exchange(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    entities = await BoundedResourceService(session, context).entities()
    assert [item.id for item in entities] == [seeded_domain.entity_a_id]


async def test_account_disable_invalidates_existing_session(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    raw, _ = await service.exchange(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    user = await session.get(User, seeded_domain.user_a_id)
    user.status = RecordStatus.DISABLED
    await session.commit()
    with pytest.raises(BoundLaunchDenied):
        await service.context(raw, now=now)


async def test_binding_revocation_invalidates_existing_session(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    raw, _ = await service.exchange(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    binding = await session.scalar(__import__("sqlalchemy").select(ExternalInstallationBinding))
    binding.revoked_at = now
    await session.commit()
    with pytest.raises(BoundLaunchDenied):
        await service.context(raw, now=now)


async def test_logout_revokes_existing_session(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    raw, _ = await service.exchange(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    await service.revoke(raw, now=now)
    await session.commit()
    with pytest.raises(BoundLaunchDenied):
        await service.context(raw, now=now)


async def test_all_read_export_and_stream_surfaces_exclude_b(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    for installation_id, suffix in (
        (seeded_domain.installation_a_id, "a"),
        (seeded_domain.installation_b_id, "b"),
    ):
        tenant_id = (
            seeded_domain.tenant_a_id if suffix == "a" else seeded_domain.tenant_b_id
        )
        routine = AlexaVoiceRoutine(
            tenant_id=tenant_id, installation_id=installation_id, name=suffix,
            slug=f"routine-{suffix}", destination_type="entity", enabled=True,
        )
        session.add(routine)
        await session.flush()
        session.add_all([
            AlexaSpeakerGroup(
                tenant_id=tenant_id, installation_id=installation_id,
                name=suffix, slug=f"group-{suffix}", enabled=True,
            ),
            AlexaRoutineExecution(
                tenant_id=tenant_id, installation_id=installation_id,
                routine_id=routine.id, destination=suffix, mode="announce",
                message_preview=suffix, status="success",
            ),
            EntityStateHistory(
                tenant_id=tenant_id, installation_id=installation_id,
                entity_id=(seeded_domain.entity_a_id if suffix == "a" else seeded_domain.entity_b_id),
                state="on", available=True,
            ),
            OperationalEvent(
                tenant_id=tenant_id, installation_id=installation_id,
                event_type="test", source="test", outcome="success", metadata_json={},
            ),
            AuditEvent(
                tenant_id=tenant_id, installation_id=installation_id,
                source="test", event_type="test", result="success",
                payload_redacted_json={},
            ),
        ])
    await session.commit()
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    _, context = await service.exchange(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    resources = BoundedResourceService(session, context)
    collections = (
        await resources.routines(), await resources.speaker_groups(),
        await resources.routine_executions(), await resources.state_history(),
        await resources.operational_events(),
    )
    assert all(len(items) == 1 for items in collections)
    assert all(items[0].installation_id == seeded_domain.installation_a_id for items in collections)
    surface_audits = [item for item in await resources.audit_events() if item.source == "test"]
    assert len(surface_audits) == 1
    assert surface_audits[0].installation_id == seeded_domain.installation_a_id


async def test_command_to_b_is_denied_before_any_side_effect(session, seeded_domain):
    now = datetime.now(UTC)
    await _grant(session, seeded_domain)
    service = InstallationBoundService(session, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    _, context = await service.exchange(encode_launch(_claims(seeded_domain, now), SECRET), now=now)
    resources = BoundedResourceService(session, context)
    side_effects = {"a": 0, "b": 0}

    async def mutate_b():
        side_effects["b"] += 1

    with pytest.raises(BoundLaunchDenied):
        await resources.mutate(seeded_domain.installation_b_id, mutate_b)
    assert side_effects == {"a": 0, "b": 0}

    async def mutate_a():
        side_effects["a"] += 1

    await resources.mutate(seeded_domain.installation_a_id, mutate_a)
    assert side_effects == {"a": 1, "b": 0}
