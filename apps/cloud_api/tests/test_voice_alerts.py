"""Alexa one-shot voice alert tests."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.domain.models import AlexaVoiceAlert, Entity, Installation
from apps.cloud_api.app.voice_alerts import (
    create_voice_alert,
    evaluate_voice_alerts,
    parse_alert_request,
)


def test_parses_supported_state_condition() -> None:
    assert parse_alert_request("quando il cancello è chiuso") == ("cancello", "closed")
    assert parse_alert_request("appena luce garage diventa accesa") == (
        "luce garage",
        "on",
    )


async def test_voice_alert_binds_echo_and_dispatches_once(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation_id = seeded_domain.installation_a_id  # type: ignore[attr-defined]
    tenant_id = seeded_domain.tenant_a_id  # type: ignore[attr-defined]
    installation = await session.get(Installation, installation_id)
    assert installation is not None
    target = Entity(
        installation_id=installation_id,
        ha_entity_id="binary_sensor.cancello",
        ha_registry_id="registry-cancello",
        ha_domain="binary_sensor",
        voice_name="Cancello",
        state="on",
    )
    voice_event = Entity(
        installation_id=installation_id,
        ha_entity_id="event.echo_ufficio_voce",
        ha_registry_id="registry-echo-voice",
        ha_domain="event",
        device_id="echo-device",
        state=datetime.now(UTC).isoformat(),
        last_changed_at=datetime.now(UTC),
    )
    speaker = Entity(
        installation_id=installation_id,
        ha_entity_id="notify.echo_ufficio_annuncio",
        ha_registry_id="registry-echo-announce",
        ha_domain="notify",
        device_id="echo-device",
    )
    session.add_all([target, voice_event, speaker])
    await session.commit()

    created, reply = await create_voice_alert(
        session, tenant_id, installation, "quando il cancello è chiuso"
    )
    assert created is True
    assert "avviserò" in reply
    alert = await session.scalar(select(AlexaVoiceAlert))
    assert alert is not None and alert.source_device_id == "echo-device"

    target.state = "off"
    await session.commit()
    dispatch = AsyncMock(return_value=[SimpleNamespace(status="success")])
    with patch("apps.cloud_api.app.alexa_routines._dispatch_announcement", new=dispatch):
        await evaluate_voice_alerts(session, installation_id, [target.id])
        await evaluate_voice_alerts(session, installation_id, [target.id])

    await session.refresh(alert)
    assert alert.status == "triggered"
    assert dispatch.await_count == 1
    call = dispatch.await_args
    assert call is not None
    assert call.args[3] == f"entity:{speaker.id}"
