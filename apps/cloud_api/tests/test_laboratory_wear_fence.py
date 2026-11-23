from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.cloud_api.app.domain.models import Entity, Installation
from apps.cloud_api.app.laboratory_live_state import sync_live_entities
from apps.cloud_api.app.wear_bridge import WearBridgeAuthority, lock_wear_writer
from apps.cloud_api.app.wear_cutover import CutoverBlocked, committed_cutover_receipt


@pytest.mark.parametrize("owner", ["legacy", "econtrol"])
async def test_laboratory_overlay_respects_wear_owner_and_rollback(
    session, seeded_domain, monkeypatch, owner
):
    installation = await session.get(Installation, seeded_domain.installation_a_id)
    entity = await session.get(Entity, seeded_domain.entity_a_id)
    old_registry = entity.ha_registry_id
    entity_id = entity.ha_entity_id
    state = SimpleNamespace(
        ha_entity_id=entity_id,
        ha_registry_id="lab-new-registry",
        ha_domain="sensor",
        icon=None,
        friendly_name="Lab",
        area_id=None,
        area_name=None,
        device_id=None,
        device_name=None,
        device_class=None,
        supported_features=0,
        state="on",
        available=True,
        attributes={},
        last_changed_at=None,
        last_seen_at=None,
    )

    async def fake_states(_):
        return {entity_id: state}

    monkeypatch.setattr("apps.cloud_api.app.laboratory_live_state.load_live_states", fake_states)
    monkeypatch.setattr(
        "apps.cloud_api.app.wear_bridge.get_settings",
        lambda: SimpleNamespace(wear_bridge_fence_enabled=True, wear_bridge_scope={}),
    )
    session.add(
        WearBridgeAuthority(
            installation_id=installation.id, owner=owner, generation=1 if owner == "econtrol" else 0
        )
    )
    await session.commit()
    if owner == "econtrol":
        # Model an overlay attempt between two mapping page reads.
        first_page = (entity.ha_registry_id, installation.sync_revision)
        with pytest.raises(HTTPException) as exc:
            await sync_live_entities(session, installation)
        assert exc.value.status_code == 409
        await session.rollback()
        reloaded = await session.scalar(
            select(Entity)
            .where(Entity.id == seeded_domain.entity_a_id)
            .execution_options(populate_existing=True)
        )
        assert reloaded.ha_registry_id == old_registry
        current_installation = await session.get(Installation, seeded_domain.installation_a_id)
        assert (reloaded.ha_registry_id, current_installation.sync_revision) == first_page
    else:
        await sync_live_entities(session, installation)
        assert entity.ha_registry_id == "lab-new-registry"


async def test_long_session_reloads_authority_before_allowing_overlay(
    session, seeded_domain, monkeypatch
):
    installation_id = seeded_domain.installation_a_id
    session.add(WearBridgeAuthority(installation_id=installation_id, owner="legacy", generation=0))
    await session.commit()
    monkeypatch.setattr(
        "apps.cloud_api.app.wear_bridge.get_settings",
        lambda: SimpleNamespace(wear_bridge_fence_enabled=True, wear_bridge_scope={}),
    )
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    async with factory() as long_session, factory() as writer_session:
        cached = await long_session.get(WearBridgeAuthority, installation_id)
        assert cached.owner == "legacy"
        writer = await writer_session.get(WearBridgeAuthority, installation_id)
        writer.owner = "econtrol"
        writer.generation = 1
        await writer_session.commit()
        assert cached.owner == "legacy"
        with pytest.raises(HTTPException) as exc:
            await lock_wear_writer(long_session, installation_id)
        assert exc.value.status_code == 409
        assert cached.owner == "econtrol" and cached.generation == 1


async def test_receipt_reloads_authority_after_remote_rollback(session, seeded_domain):
    installation_id = seeded_domain.installation_a_id
    revision = "a" * 64
    session.add(
        WearBridgeAuthority(
            installation_id=installation_id,
            owner="econtrol",
            generation=1,
            import_revision=revision,
        )
    )
    await session.commit()
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    async with factory() as long_session, factory() as writer_session:
        cached = await long_session.get(WearBridgeAuthority, installation_id)
        await long_session.commit()
        writer = await writer_session.get(WearBridgeAuthority, installation_id)
        writer.owner = "legacy"
        writer.generation = 2
        await writer_session.commit()
        assert cached.owner == "econtrol"
        with pytest.raises(CutoverBlocked):
            await committed_cutover_receipt(long_session, installation_id, 1, revision, b"k" * 32)
        assert cached.owner == "legacy"
