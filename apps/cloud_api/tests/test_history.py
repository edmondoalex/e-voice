"""State-history deduplication and retention tests."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.config import Settings
from apps.cloud_api.app.domain.models import Entity, EntityStateHistory, OperationalEvent
from apps.cloud_api.app.history import StateHistoryService, cleanup_expired


async def test_history_records_only_changes_and_honors_exclusions(
    session: AsyncSession, seeded_domain: object
) -> None:
    entity = await session.scalar(select(Entity).where(Entity.ha_entity_id == "light.kitchen"))
    assert entity is not None
    tenant_id = seeded_domain.tenant_a_id  # type: ignore[attr-defined]
    service = StateHistoryService(session, Settings(state_history_excluded_domains="sensor"))
    entity.state = "off"
    assert await service.record_change(
        entity, tenant_id=tenant_id, previous_state=None, previous_available=True
    )
    await session.commit()
    assert not await service.record_change(
        entity, tenant_id=tenant_id, previous_state="off", previous_available=True
    )
    entity.state = "on"
    assert await service.record_change(
        entity, tenant_id=tenant_id, previous_state="off", previous_available=True
    )
    entity.ha_domain = "sensor"
    entity.state = "42"
    assert not await service.record_change(
        entity, tenant_id=tenant_id, previous_state="on", previous_available=True
    )
    await session.commit()
    count = await session.scalar(select(func.count(EntityStateHistory.id)))
    assert count == 2


async def test_cleanup_deletes_expired_and_preserves_recent(
    session: AsyncSession, seeded_domain: object
) -> None:
    entity = await session.scalar(select(Entity).where(Entity.ha_entity_id == "light.kitchen"))
    assert entity is not None
    now = datetime.now(UTC)
    tenant_id = seeded_domain.tenant_a_id  # type: ignore[attr-defined]
    session.add_all(
        [
            EntityStateHistory(
                tenant_id=tenant_id,
                installation_id=entity.installation_id,
                entity_id=entity.id,
                state="old",
                available=True,
                recorded_at=now - timedelta(days=31),
            ),
            EntityStateHistory(
                tenant_id=tenant_id,
                installation_id=entity.installation_id,
                entity_id=entity.id,
                state="new",
                available=True,
                recorded_at=now,
            ),
            OperationalEvent(
                tenant_id=tenant_id,
                installation_id=entity.installation_id,
                event_type="connector_session",
                source="connector",
                outcome="disconnected",
                created_at=now - timedelta(days=31),
            ),
        ]
    )
    await session.commit()
    result = await cleanup_expired(session, Settings(), now=now)
    assert result.state_history == 1
    assert result.operational_events == 1
    assert await session.scalar(select(func.count(EntityStateHistory.id))) == 1
