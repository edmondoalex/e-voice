from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.auth import TenantContext
from apps.cloud_api.app.conversation import ReplyStatus
from apps.cloud_api.app.conversation_service import ConversationEntityService
from apps.cloud_api.app.domain.enums import TenantRole
from apps.cloud_api.app.domain.models import Entity
from apps.cloud_api.app.services import ResourceNotFoundError

NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


async def test_reads_real_persisted_entity_for_authorized_installation(
    session: AsyncSession, seeded_domain: object
) -> None:
    entity = Entity(
        installation_id=seeded_domain.installation_a_id,  # type: ignore[attr-defined]
        ha_entity_id="sensor.pv_power",
        ha_domain="sensor",
        friendly_name="Produzione fotovoltaico",
        voice_aliases=["fotovoltaico"],
        device_class="power",
        state="3812",
        attributes_json={"unit_of_measurement": "W", "state_class": "measurement"},
        available=True,
        last_seen_at=NOW,
    )
    session.add(entity)
    await session.commit()
    context = TenantContext(
        user_id=seeded_domain.user_a_id,  # type: ignore[attr-defined]
        tenant_id=seeded_domain.tenant_a_id,  # type: ignore[attr-defined]
        role=TenantRole.OWNER,
    )

    reply = await ConversationEntityService(session).ask(
        context,
        seeded_domain.installation_a_id,  # type: ignore[attr-defined]
        "Quanto produce il fotovoltaico?",
        now=NOW,
    )

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == "In questo momento il fotovoltaico sta producendo 3812 W."
    assert reply.evidence[0].entity_id == "sensor.pv_power"


async def test_cannot_read_another_tenants_installation(
    session: AsyncSession, seeded_domain: object
) -> None:
    context = TenantContext(
        user_id=seeded_domain.user_a_id,  # type: ignore[attr-defined]
        tenant_id=seeded_domain.tenant_a_id,  # type: ignore[attr-defined]
        role=TenantRole.OWNER,
    )

    with pytest.raises(ResourceNotFoundError):
        await ConversationEntityService(session).ask(
            context,
            seeded_domain.installation_b_id,  # type: ignore[attr-defined]
            "Quanto produce il fotovoltaico?",
            now=NOW,
        )


async def test_deleted_entity_is_not_visible_to_conversation(
    session: AsyncSession, seeded_domain: object
) -> None:
    deleted = Entity(
        installation_id=seeded_domain.installation_a_id,  # type: ignore[attr-defined]
        ha_entity_id="sensor.deleted_battery",
        ha_domain="sensor",
        friendly_name="Batteria eliminata",
        device_class="battery",
        state="90",
        attributes_json={"unit_of_measurement": "%"},
        deleted_at=NOW,
    )
    session.add(deleted)
    await session.commit()
    context = TenantContext(
        user_id=seeded_domain.user_a_id,  # type: ignore[attr-defined]
        tenant_id=seeded_domain.tenant_a_id,  # type: ignore[attr-defined]
        role=TenantRole.OWNER,
    )

    reply = await ConversationEntityService(session).ask(
        context,
        seeded_domain.installation_a_id,  # type: ignore[attr-defined]
        "A quanto è la batteria?",
        now=NOW,
    )

    assert reply.status is ReplyStatus.NOT_FOUND
