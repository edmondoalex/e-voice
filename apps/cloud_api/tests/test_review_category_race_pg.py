import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.cloud_api.app import voice_scope
from apps.cloud_api.app.admin_console import _assign_visible_categories
from apps.cloud_api.app.domain.models import ConnectorCredential, Entity, VoiceCategory
from apps.cloud_api.app.voice_selection_store import persist_selection, register_selection_session


async def prepare(session, seeded_domain, monkeypatch):
    a = seeded_domain.installation_a_id
    tenant = seeded_domain.tenant_a_id
    sid = uuid4()
    entity = Entity(
        installation_id=a,
        ha_registry_id="category-registry",
        ha_entity_id="sensor.category_guard",
        ha_domain="sensor",
        control_exposed=True,
    )
    cred = ConnectorCredential(installation_id=a, secret_hash="c" * 64)
    category = VoiceCategory(tenant_id=tenant, slug="categoryguard", name="Guard", builtin=False)
    session.add_all([entity, cred, category])
    await session.flush()
    eid, cid, catid = entity.id, cred.id, category.id
    await register_selection_session(session, a, tenant, sid, cid)
    await persist_selection(session, a, tenant, sid, 1, ["category-registry"])
    await session.commit()
    monkeypatch.setattr(
        voice_scope,
        "get_settings",
        lambda: SimpleNamespace(
            voice_selection_v1_enabled=True, voice_selection_projection_enabled=True
        ),
    )
    return (
        eid,
        cid,
        catid,
        SimpleNamespace(tenant_id=tenant),
        async_sessionmaker(session.bind, expire_on_commit=False),
    )


async def test_revocation_committed_before_update_blocks_assignment(
    session, seeded_domain, monkeypatch
):
    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    async with factory() as revoker, factory() as writer:
        credential = await revoker.get(ConnectorCredential, cid)
        credential.revoked_at = datetime.now(UTC)
        await revoker.commit()
        assert await _assign_visible_categories(writer, ctx, [eid], cat) == 0
        await writer.commit()


async def test_revocation_while_update_waits_blocks_assignment(session, seeded_domain, monkeypatch):
    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    async with factory() as blocker, factory() as revoker, factory() as writer:
        await blocker.scalar(select(Entity).where(Entity.id == eid).with_for_update())
        pending = asyncio.create_task(_assign_visible_categories(writer, ctx, [eid], cat))
        await asyncio.sleep(0.3)
        assert not pending.done()
        credential = await revoker.get(ConnectorCredential, cid)
        credential.revoked_at = datetime.now(UTC)
        revoke_task = asyncio.create_task(revoker.commit())
        await asyncio.sleep(0.3)
        assert not revoke_task.done()
        await blocker.commit()
        count = await asyncio.wait_for(pending, 5)
        await writer.commit()
        await asyncio.wait_for(revoke_task, 5)
        assert count == 1


async def test_expiry_while_waiting_installation_lock_blocks_category(
    session, seeded_domain, monkeypatch
):
    from datetime import timedelta

    from apps.cloud_api.app.domain.models import Installation
    from apps.cloud_api.app.voice_selection_store import VoiceSelectionSnapshot

    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    async with factory() as setup:
        row = await setup.get(VoiceSelectionSnapshot, seeded_domain.installation_a_id)
        row.expires_at = datetime.now(UTC) + timedelta(seconds=0.4)
        await setup.commit()
    async with factory() as blocker, factory() as writer:
        await blocker.scalar(
            select(Installation)
            .where(Installation.id == seeded_domain.installation_a_id)
            .with_for_update()
        )
        pending = asyncio.create_task(_assign_visible_categories(writer, ctx, [eid], cat))
        await asyncio.sleep(0.8)
        assert not pending.done()
        await blocker.commit()
        assert await asyncio.wait_for(pending, 5) == 0
        await writer.commit()


async def test_lock_inversion_with_revocation_audit_can_deadlock(
    session, seeded_domain, monkeypatch
):
    from apps.cloud_api.app.domain.models import AuditEvent

    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    async with factory() as revoker, factory() as writer:
        credential = await revoker.get(ConnectorCredential, cid)
        credential.revoked_at = datetime.now(UTC)
        await revoker.flush()
        pending = asyncio.create_task(_assign_visible_categories(writer, ctx, [eid], cat))
        await asyncio.sleep(0.3)
        assert not pending.done()
        revoker.add(
            AuditEvent(
                tenant_id=ctx.tenant_id,
                installation_id=seeded_domain.installation_a_id,
                user_id=seeded_domain.user_a_id,
                source="synthetic",
                event_type="revoked",
                result="success",
            )
        )
        revoke_task = asyncio.create_task(revoker.commit())
        outcomes = await asyncio.wait_for(
            asyncio.gather(pending, revoke_task, return_exceptions=True), 5
        )
        assert outcomes == [0, None]
        await writer.commit()
        await revoker.rollback()


async def test_real_revoke_waits_for_category_then_revokes_without_deadlock(
    session, seeded_domain, monkeypatch
):
    from cryptography.fernet import Fernet
    from sqlalchemy import event

    from apps.cloud_api.app.domain.enums import TenantRole
    from apps.cloud_api.app.pairing import PairingService

    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    context = SimpleNamespace(
        tenant_id=ctx.tenant_id, user_id=seeded_domain.user_a_id, role=TenantRole.OWNER
    )
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "connector_credentials" in statement or "audit_events" in statement:
            statements.append(statement.split()[0:3])

    event.listen(session.bind.sync_engine, "before_cursor_execute", record)
    try:
        async with factory() as blocker, factory() as writer, factory() as revoker:
            await blocker.scalar(select(Entity).where(Entity.id == eid).with_for_update())
            pending = asyncio.create_task(_assign_visible_categories(writer, ctx, [eid], cat))
            await asyncio.sleep(0.3)
            assert not pending.done()
            svc = PairingService(revoker, code_pepper=b"p" * 32, delivery_key=Fernet.generate_key())
            revoke_task = asyncio.create_task(svc.revoke_credential(context, credential_id=cid))
            await asyncio.sleep(0.3)
            assert not revoke_task.done()
            await blocker.commit()
            assert await asyncio.wait_for(pending, 5) == 1
            await writer.commit()
            await asyncio.wait_for(revoke_task, 5)
            async with factory() as check:
                cred = await check.get(ConnectorCredential, cid)
                assert cred.revoked_at is not None
        assert any(parts[0].upper() == "UPDATE" for parts in statements) and any(
            parts[0].upper() == "INSERT" for parts in statements
        )
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", record)


async def test_real_revoke_commits_first_category_is_skipped(session, seeded_domain, monkeypatch):
    from cryptography.fernet import Fernet

    from apps.cloud_api.app.domain.enums import TenantRole
    from apps.cloud_api.app.pairing import PairingService

    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    context = SimpleNamespace(
        tenant_id=ctx.tenant_id, user_id=seeded_domain.user_a_id, role=TenantRole.OWNER
    )
    async with factory() as revoker, factory() as writer:
        svc = PairingService(revoker, code_pepper=b"p" * 32, delivery_key=Fernet.generate_key())
        await svc.revoke_credential(context, credential_id=cid)
        assert await _assign_visible_categories(writer, ctx, [eid], cat) == 0
        await writer.commit()


async def test_selection_disconnect_serializes_after_inflight_category(
    session, seeded_domain, monkeypatch
):
    from apps.cloud_api.app.voice_selection_store import VoiceSelectionFence, end_selection_session

    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    async with factory() as blocker, factory() as writer, factory() as closer:
        fence = await closer.get(VoiceSelectionFence, seeded_domain.installation_a_id)
        sid = fence.session_id
        await closer.rollback()
        await blocker.scalar(select(Entity).where(Entity.id == eid).with_for_update())
        pending = asyncio.create_task(_assign_visible_categories(writer, ctx, [eid], cat))
        await asyncio.sleep(0.3)
        assert not pending.done()
        closing = asyncio.create_task(
            end_selection_session(closer, seeded_domain.installation_a_id, sid)
        )
        await asyncio.sleep(0.3)
        assert not closing.done()
        await blocker.commit()
        assert await asyncio.wait_for(pending, 5) == 1
        await writer.commit()
        await asyncio.wait_for(closing, 5)
        await closer.commit()
        async with factory() as check:
            assert not await check.scalar(
                select(voice_scope.selection_current_clause(seeded_domain.installation_a_id))
            )


async def test_real_rotate_waits_for_inflight_strict_assignment(
    session, seeded_domain, monkeypatch
):
    from cryptography.fernet import Fernet

    from apps.cloud_api.app.domain.enums import TenantRole
    from apps.cloud_api.app.pairing import PairingService

    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    context = SimpleNamespace(
        tenant_id=ctx.tenant_id, user_id=seeded_domain.user_a_id, role=TenantRole.OWNER
    )
    async with factory() as blocker, factory() as writer, factory() as rotator:
        await blocker.scalar(select(Entity).where(Entity.id == eid).with_for_update())
        assignment = asyncio.create_task(_assign_visible_categories(writer, ctx, [eid], cat))
        await asyncio.sleep(0.3)
        assert not assignment.done()
        service = PairingService(rotator, code_pepper=b"p" * 32, delivery_key=Fernet.generate_key())
        rotation = asyncio.create_task(
            service.rotate_credential(context, installation_id=seeded_domain.installation_a_id)
        )
        await asyncio.sleep(0.3)
        assert not rotation.done()
        await blocker.commit()
        assert await asyncio.wait_for(assignment, 5) == 1
        await writer.commit()
        issued = await asyncio.wait_for(rotation, 5)
        async with factory() as check:
            old = await check.get(ConnectorCredential, cid)
            assert old.revoked_at is not None and issued.credential_id != cid
            assert not await check.scalar(
                select(voice_scope.selection_current_clause(seeded_domain.installation_a_id))
            )


async def test_real_rotate_first_causes_strict_assignment_skip(session, seeded_domain, monkeypatch):
    from cryptography.fernet import Fernet

    from apps.cloud_api.app.domain.enums import TenantRole
    from apps.cloud_api.app.pairing import PairingService

    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    context = SimpleNamespace(
        tenant_id=ctx.tenant_id, user_id=seeded_domain.user_a_id, role=TenantRole.OWNER
    )
    async with factory() as writer, factory() as rotator:
        service = PairingService(rotator, code_pepper=b"p" * 32, delivery_key=Fernet.generate_key())
        await service.rotate_credential(context, installation_id=seeded_domain.installation_a_id)
        assert await _assign_visible_categories(writer, ctx, [eid], cat) == 0
        await writer.commit()


async def test_strict_lock_timeout_fails_without_category_write(
    session, seeded_domain, monkeypatch
):
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from apps.cloud_api.app.domain.models import Installation

    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    async with factory() as blocker, factory() as writer:
        await blocker.scalar(
            select(Installation)
            .where(Installation.id == seeded_domain.installation_a_id)
            .with_for_update()
        )
        await writer.execute(text("SET LOCAL lock_timeout = '150ms'"))
        with pytest.raises(DBAPIError):
            await _assign_visible_categories(writer, ctx, [eid], cat)
        await writer.rollback()
        await blocker.rollback()
        row = await writer.get(Entity, eid)
        assert row.voice_category_id is None


async def test_legacy_assignment_remains_available_after_rotation(
    session, seeded_domain, monkeypatch
):
    from cryptography.fernet import Fernet

    from apps.cloud_api.app.domain.enums import TenantRole
    from apps.cloud_api.app.pairing import PairingService

    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    monkeypatch.setattr(
        voice_scope,
        "get_settings",
        lambda: SimpleNamespace(
            voice_selection_v1_enabled=False, voice_selection_projection_enabled=False
        ),
    )
    context = SimpleNamespace(
        tenant_id=ctx.tenant_id, user_id=seeded_domain.user_a_id, role=TenantRole.OWNER
    )
    async with factory() as writer, factory() as rotator:
        await PairingService(
            rotator, code_pepper=b"p" * 32, delivery_key=Fernet.generate_key()
        ).rotate_credential(context, installation_id=seeded_domain.installation_a_id)
        assert await _assign_visible_categories(writer, ctx, [eid], cat) == 1
        await writer.commit()


async def test_old_rotator_concurrent_category_is_serialized_in_this_schedule(
    session, seeded_domain, monkeypatch
):
    from cryptography.fernet import Fernet

    from apps.cloud_api.app.domain.enums import TenantRole
    from apps.cloud_api.app.pairing_legacy_fixture import PairingService as OldPairingService

    eid, cid, cat, ctx, factory = await prepare(session, seeded_domain, monkeypatch)
    context = SimpleNamespace(
        tenant_id=ctx.tenant_id, user_id=seeded_domain.user_a_id, role=TenantRole.OWNER
    )
    async with factory() as blocker, factory() as writer, factory() as rotator:
        await blocker.scalar(select(Entity).where(Entity.id == eid).with_for_update())
        assignment = asyncio.create_task(_assign_visible_categories(writer, ctx, [eid], cat))
        await asyncio.sleep(0.3)
        assert not assignment.done()
        old = OldPairingService(rotator, code_pepper=b"p" * 32, delivery_key=Fernet.generate_key())
        rotation = asyncio.create_task(
            old.rotate_credential(context, installation_id=seeded_domain.installation_a_id)
        )
        await asyncio.sleep(0.3)
        assert not rotation.done()
        await blocker.commit()
        assert await asyncio.wait_for(assignment, 5) == 1
        await writer.commit()
        issued = await asyncio.wait_for(rotation, 5)
        assert issued.credential_id != cid
