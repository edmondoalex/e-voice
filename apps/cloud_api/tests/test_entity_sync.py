"""M5 cloud persistence and reconciliation tests."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.domain.models import Entity, Installation
from apps.cloud_api.app.entity_sync import EntitySyncService, StaleSyncError
from apps.cloud_api.app.evcp import (
    EntityItem,
    InventoryAccumulator,
    InventoryFull,
    InventoryPayload,
    StateUpdate,
    _apply_entity_sync,
    inbound_adapter,
)


def item(registry_id: str = "registry-light-1", state: str = "on") -> dict[str, object]:
    return {
        "registry_id": registry_id,
        "entity_id": "light.kitchen",
        "domain": "light",
        "icon": "mdi:ceiling-light",
        "friendly_name": "Kitchen",
        "area_id": None,
        "area_name": None,
        "device_id": None,
        "device_name": None,
        "device_class": None,
        "supported_features": 1,
        "state": state,
        "available": True,
        "attributes": {"brightness": 100},
        "last_changed_at": datetime.now(UTC).isoformat(),
        "removed": False,
    }


async def test_final_authorization_removal_snapshot_tombstones_entity(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation = await session.get(Installation, seeded_domain.installation_a_id)  # type: ignore[attr-defined]
    assert installation is not None
    service = EntitySyncService(session, installation)
    await service.apply_full(1, [item()])
    entity = (
        await session.scalars(select(Entity).where(Entity.ha_registry_id == "registry-light-1"))
    ).one()
    assert entity.state == "on"
    assert entity.attributes_json == {"brightness": 100}
    assert entity.icon == "mdi:ceiling-light"
    await service.apply_full(2, [])
    assert entity.deleted_at is not None
    assert not entity.available


async def test_inventory_commit_triggers_proactive_discovery_reconciliation(
    session: AsyncSession, seeded_domain: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = await session.get(
        Installation,
        seeded_domain.installation_a_id,  # type: ignore[attr-defined]
    )
    assert installation is not None
    reconcile = AsyncMock(return_value=0)
    monkeypatch.setattr("apps.cloud_api.app.alexa_events.reconcile_discovery_safely", reconcile)
    await EntitySyncService(session, installation).apply_full(1, [item()])
    reconcile.assert_awaited_once_with(session, installation)


async def test_name_change_updates_metadata_without_duplicate_identity(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation = await session.get(Installation, seeded_domain.installation_a_id)  # type: ignore[attr-defined]
    assert installation is not None
    service = EntitySyncService(session, installation)
    await service.apply_full(1, [item()])
    renamed = {**item(), "friendly_name": "Current HA name"}
    await service.apply_full(2, [renamed])
    entities = (
        await session.scalars(
            select(Entity).where(
                Entity.installation_id == installation.id,
                Entity.ha_registry_id == "registry-light-1",
            )
        )
    ).all()
    assert len(entities) == 1
    assert entities[0].friendly_name == "Current HA name"


async def test_connector_rename_preserves_cloud_name_overrides(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation = await session.get(Installation, seeded_domain.installation_a_id)  # type: ignore[attr-defined]
    assert installation is not None
    service = EntitySyncService(session, installation)
    await service.apply_full(1, [item()])
    entity = (
        await session.scalars(select(Entity).where(Entity.ha_registry_id == "registry-light-1"))
    ).one()
    original_id, original_registry_id = entity.ha_entity_id, entity.ha_registry_id
    entity.display_name = "Ufficio Alex"
    entity.voice_name = "luce ufficio"
    entity.voice_aliases = ["ufficio", "luce alex"]
    await session.commit()

    await service.apply_full(2, [{**item(), "friendly_name": "Luce Ufficio Alex evoice"}])

    assert entity.friendly_name == "Luce Ufficio Alex evoice"
    assert entity.display_name == "Ufficio Alex"
    assert entity.voice_name == "luce ufficio"
    assert entity.voice_aliases == ["ufficio", "luce alex"]
    assert entity.ha_entity_id == original_id
    assert entity.ha_registry_id == original_registry_id


async def test_stale_and_unauthorized_state_are_rejected(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation = await session.get(Installation, seeded_domain.installation_a_id)  # type: ignore[attr-defined]
    assert installation is not None
    service = EntitySyncService(session, installation)
    await service.apply_full(1, [item()])
    with pytest.raises(StaleSyncError):
        await service.apply_full(1, [item(state="off")])
    with pytest.raises(StaleSyncError):
        await service.apply_state(2, [item("not-authorized")])


async def test_same_registry_id_is_isolated_by_installation(
    session: AsyncSession, seeded_domain: object
) -> None:
    first = await session.get(Installation, seeded_domain.installation_a_id)  # type: ignore[attr-defined]
    second = await session.get(Installation, seeded_domain.installation_b_id)  # type: ignore[attr-defined]
    assert first is not None and second is not None
    await EntitySyncService(session, first).apply_full(1, [item(state="on")])
    await EntitySyncService(session, second).apply_full(1, [item(state="off")])
    entities = (
        await session.scalars(select(Entity).where(Entity.ha_registry_id == "registry-light-1"))
    ).all()
    assert {entity.installation_id for entity in entities} == {first.id, second.id}


def test_inventory_batches_require_consistent_order_and_allow_exact_duplicate() -> None:
    accumulator = InventoryAccumulator()
    installation_id, session_id = uuid4(), uuid4()
    entity = EntityItem.model_validate({**item(), "last_changed_at": datetime.now(UTC)})
    first = InventoryPayload(
        session_id=session_id,
        revision=1,
        batch_index=0,
        batch_count=2,
        entities=[entity],
    )
    assert accumulator.add(installation_id, first) is None
    assert accumulator.add(installation_id, first) is None
    with pytest.raises(ValueError, match="batch count"):
        accumulator.add(
            installation_id,
            first.model_copy(update={"batch_count": 3}),
        )


def test_inventory_batches_reject_missing_or_out_of_order_batch() -> None:
    accumulator = InventoryAccumulator()
    entity = EntityItem.model_validate({**item(), "last_changed_at": datetime.now(UTC)})
    payload = InventoryPayload(
        session_id=uuid4(),
        revision=1,
        batch_index=1,
        batch_count=2,
        entities=[entity],
    )
    with pytest.raises(ValueError, match="out-of-order"):
        accumulator.add(uuid4(), payload)


async def test_multibatch_full_inventory_is_applied_atomically_after_final_batch(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation = await session.get(Installation, seeded_domain.installation_a_id)  # type: ignore[attr-defined]
    assert installation is not None
    first_item = item("registry-light-1")
    second_item = {
        **item("registry-switch-2", state="off"),
        "entity_id": "switch.second",
        "domain": "switch",
    }
    service = EntitySyncService(session, installation)
    await service.apply_full(1, [first_item, second_item])

    accumulator = InventoryAccumulator()
    session_id = uuid4()
    first_batch = InventoryPayload.model_validate(
        {
            "session_id": session_id,
            "revision": 2,
            "batch_index": 0,
            "batch_count": 2,
            "entities": [{**first_item, "last_changed_at": datetime.now(UTC)}],
        }
    )
    second_batch = InventoryPayload.model_validate(
        {
            "session_id": session_id,
            "revision": 2,
            "batch_index": 1,
            "batch_count": 2,
            "entities": [{**second_item, "last_changed_at": datetime.now(UTC)}],
        }
    )

    assert accumulator.add(installation.id, first_batch) is None
    before_complete = (
        await session.scalars(select(Entity).where(Entity.installation_id == installation.id))
    ).all()
    assert len(before_complete) == 2
    assert all(entity.deleted_at is None for entity in before_complete)
    assert installation.sync_revision == 1

    complete_snapshot = accumulator.add(installation.id, second_batch)
    assert complete_snapshot is not None
    await service.apply_full(2, complete_snapshot)
    after_complete = (
        await session.scalars(select(Entity).where(Entity.installation_id == installation.id))
    ).all()
    assert len(after_complete) == 2
    assert all(entity.deleted_at is None for entity in after_complete)
    assert installation.sync_revision == 2


async def test_evcp_inventory_contract_reaches_installation_scoped_persistence(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation = await session.get(Installation, seeded_domain.installation_a_id)  # type: ignore[attr-defined]
    assert installation is not None
    session_id = uuid4()
    message = inbound_adapter.validate_json(
        json.dumps(
            {
                "version": 1,
                "type": "inventory_full",
                "id": uuid4(),
                "timestamp": datetime.now(UTC),
                "payload": {
                    "session_id": session_id,
                    "revision": 1,
                    "batch_index": 0,
                    "batch_count": 1,
                    "entities": [item()],
                },
            },
            default=str,
        )
    )
    assert isinstance(message, InventoryFull)

    await _apply_entity_sync(session, installation, installation.id, message)

    persisted = (
        await session.scalars(
            select(Entity).where(
                Entity.installation_id == installation.id,
                Entity.ha_registry_id == "registry-light-1",
            )
        )
    ).one()
    assert persisted.ha_entity_id == "light.kitchen"
    assert installation.sync_revision == 1

    state_message = inbound_adapter.validate_json(
        json.dumps(
            {
                "version": 1,
                "type": "state_update",
                "id": uuid4(),
                "timestamp": datetime.now(UTC),
                "payload": {
                    "session_id": session_id,
                    "revision": 2,
                    "batch_index": 0,
                    "batch_count": 1,
                    "entities": [{**item(state="off"), "available": False}],
                },
            },
            default=str,
        )
    )
    assert isinstance(state_message, StateUpdate)
    await _apply_entity_sync(session, installation, installation.id, state_message)
    assert persisted.state == "off"
    assert persisted.available is False


async def test_evcp_zero_inventory_tombstones_only_own_installation(
    session: AsyncSession, seeded_domain: object
) -> None:
    first = await session.get(Installation, seeded_domain.installation_a_id)  # type: ignore[attr-defined]
    second = await session.get(Installation, seeded_domain.installation_b_id)  # type: ignore[attr-defined]
    assert first is not None and second is not None
    await EntitySyncService(session, first).apply_full(1, [item()])
    await EntitySyncService(session, second).apply_full(1, [item()])
    message = InventoryFull.model_validate(
        {
            "version": 1,
            "type": "inventory_full",
            "id": uuid4(),
            "timestamp": datetime.now(UTC),
            "payload": {
                "session_id": uuid4(),
                "revision": 2,
                "batch_index": 0,
                "batch_count": 1,
                "entities": [],
            },
        }
    )
    await _apply_entity_sync(session, first, first.id, message)
    entities = (
        await session.scalars(select(Entity).where(Entity.ha_registry_id == "registry-light-1"))
    ).all()
    by_installation = {entity.installation_id: entity for entity in entities}
    assert by_installation[first.id].deleted_at is not None
    assert by_installation[second.id].deleted_at is None
