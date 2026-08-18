"""M5 cloud persistence and reconciliation tests."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.domain.models import Entity, Installation
from apps.cloud_api.app.entity_sync import EntitySyncService, StaleSyncError
from apps.cloud_api.app.evcp import EntityItem, InventoryAccumulator, InventoryPayload


def item(registry_id: str = "registry-light-1", state: str = "on") -> dict[str, object]:
    return {
        "registry_id": registry_id,
        "entity_id": "light.kitchen",
        "domain": "light",
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


async def test_full_inventory_upserts_and_deselection_tombstones(
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
    await service.apply_full(2, [])
    assert entity.deleted_at is not None
    assert not entity.available


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
