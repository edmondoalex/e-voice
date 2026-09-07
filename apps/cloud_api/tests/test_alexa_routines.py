"""Alexa routine speaker discovery, triggering and group isolation tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from apps.cloud_api.app.alexa_routines import (
    RoutineTriggerRequest,
    _destination_speakers,
    _dispatch_announcement,
    _mode_entity,
    _speakers,
    _volume_entity,
    routines_page,
    trigger_routine,
)
from apps.cloud_api.app.auth import TenantContext
from apps.cloud_api.app.command_dispatch import DispatchOutcome
from apps.cloud_api.app.domain.enums import TenantRole
from apps.cloud_api.app.domain.models import (
    AlexaSpeakerGroup,
    AlexaSpeakerGroupMember,
    AlexaVoiceRoutine,
    Entity,
    Installation,
)


async def _echo_pair(
    session: AsyncSession, installation_id: object, name: str, device_id: str
) -> tuple[Entity, Entity]:
    announce = Entity(
        installation_id=installation_id,
        ha_entity_id=f"notify.{name}_announce",
        ha_registry_id=f"registry-{name}-announce",
        ha_domain="notify",
        friendly_name=f"{name} Announce",
        device_id=device_id,
    )
    speak = Entity(
        installation_id=installation_id,
        ha_entity_id=f"notify.{name}_speak",
        ha_registry_id=f"registry-{name}-speak",
        ha_domain="notify",
        friendly_name=f"{name} Speak",
        device_id=device_id,
    )
    session.add_all([announce, speak])
    await session.flush()
    return announce, speak


async def test_discovers_one_canonical_entry_per_echo_and_resolves_speak_sibling(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation_id = seeded_domain.installation_a_id  # type: ignore[attr-defined]
    announce, speak = await _echo_pair(session, installation_id, "echo_cucina", "device-1")
    media = Entity(
        installation_id=installation_id,
        ha_entity_id="media_player.echo_cucina",
        ha_registry_id="registry-echo-cucina-media",
        ha_domain="media_player",
        friendly_name="Echo cucina",
        device_id="device-1",
    )
    session.add(media)
    await session.commit()

    assert [item.id for item in await _speakers(session, installation_id)] == [announce.id]
    assert await _mode_entity(session, installation_id, announce, "announce") == announce
    assert await _mode_entity(session, installation_id, announce, "speak") == speak
    assert await _volume_entity(session, installation_id, announce) == media


async def test_custom_group_and_all_are_installation_and_tenant_scoped(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation_id = seeded_domain.installation_a_id  # type: ignore[attr-defined]
    tenant_id = seeded_domain.tenant_a_id  # type: ignore[attr-defined]
    user_id = seeded_domain.user_a_id  # type: ignore[attr-defined]
    first, _ = await _echo_pair(session, installation_id, "echo_cucina", "device-1")
    second, _ = await _echo_pair(session, installation_id, "echo_camera", "device-2")
    group = AlexaSpeakerGroup(
        tenant_id=tenant_id,
        installation_id=installation_id,
        name="Piano terra",
        slug="piano_terra",
        members=[AlexaSpeakerGroupMember(entity_id=first.id)],
    )
    session.add(group)
    await session.commit()
    installation = await session.get(Installation, installation_id)
    assert installation is not None
    context = TenantContext(user_id=user_id, tenant_id=tenant_id, role=TenantRole.OWNER)

    all_speakers = await _destination_speakers(session, context.tenant_id, installation, "all")
    grouped = await _destination_speakers(
        session, context.tenant_id, installation, f"group:{group.id}"
    )
    assert {item.id for item in all_speakers} == {first.id, second.id}
    assert [item.id for item in grouped] == [first.id]


async def test_connector_trigger_uses_default_or_ha_rendered_message(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation_id = seeded_domain.installation_a_id  # type: ignore[attr-defined]
    tenant_id = seeded_domain.tenant_a_id  # type: ignore[attr-defined]
    installation = await session.get(Installation, installation_id)
    assert installation is not None
    routine = AlexaVoiceRoutine(
        tenant_id=tenant_id,
        installation_id=installation_id,
        name="Avviso cancello",
        slug="avviso_cancello",
        mode="announce",
        destination_type="all",
        destination_id=None,
        default_message="Il cancello è aperto",
    )
    session.add(routine)
    await session.commit()
    auth = AsyncMock(return_value=SimpleNamespace(installation=installation))
    dispatch = AsyncMock(
        return_value=[SimpleNamespace(status="success"), SimpleNamespace(status="success")]
    )
    with (
        patch("apps.cloud_api.app.alexa_routines._laboratory_only"),
        patch("apps.cloud_api.app.alexa_routines.authenticate_connector_secret", new=auth),
        patch("apps.cloud_api.app.alexa_routines._dispatch_announcement", new=dispatch),
    ):
        default = await trigger_routine(
            "avviso_cancello", RoutineTriggerRequest(), "Bearer evc_test", session
        )
        dynamic = await trigger_routine(
            "avviso_cancello",
            RoutineTriggerRequest(message="Temperatura 23 gradi"),
            "Bearer evc_test",
            session,
        )

    assert default.status == "success" and default.succeeded == 2
    assert dynamic.status == "success" and dynamic.attempted == 2
    assert dispatch.await_args_list[0].args[-2] == "Il cancello è aperto"
    assert dispatch.await_args_list[1].args[-2] == "Temperatura 23 gradi"


async def test_routine_sets_each_echo_volume_before_speech(
    session: AsyncSession, seeded_domain: object
) -> None:
    installation_id = seeded_domain.installation_a_id  # type: ignore[attr-defined]
    tenant_id = seeded_domain.tenant_a_id  # type: ignore[attr-defined]
    installation = await session.get(Installation, installation_id)
    assert installation is not None
    announce, _ = await _echo_pair(session, installation_id, "echo_studio", "echo-device")
    media = Entity(
        installation_id=installation_id,
        ha_entity_id="media_player.echo_studio",
        ha_registry_id="registry-echo-studio-media",
        ha_domain="media_player",
        device_id="echo-device",
    )
    session.add(media)
    await session.commit()
    dispatched = AsyncMock(
        side_effect=[
            DispatchOutcome(announce.id, "success", None),
            DispatchOutcome(media.id, "success", None),
        ]
    )
    with patch("apps.cloud_api.app.alexa_routines.CommandDispatchService.dispatch", new=dispatched):
        outcomes = await _dispatch_announcement(
            session, tenant_id, installation, f"entity:{announce.id}", "announce", "Prova", 40
        )

    assert len(outcomes) == 1
    assert dispatched.await_count == 2
    assert dispatched.await_args_list[0].args[1] == media.ha_registry_id
    assert dispatched.await_args_list[0].args[2].operation == "set_volume"
    assert dispatched.await_args_list[0].args[2].volume_percent == 40
    assert dispatched.await_args_list[1].args[1] == announce.ha_registry_id
    assert dispatched.await_args_list[1].args[2].operation == "announce"


async def test_create_routine_form_exposes_volume_control(
    session: AsyncSession, seeded_domain: object
) -> None:
    """Volume must be selectable when creating, not only when editing, a routine."""
    installation_id = seeded_domain.installation_a_id  # type: ignore[attr-defined]
    tenant_id = seeded_domain.tenant_a_id  # type: ignore[attr-defined]
    user_id = seeded_domain.user_a_id  # type: ignore[attr-defined]
    await _echo_pair(session, installation_id, "echo_cucina", "device-1")
    session.add(
        Entity(
            installation_id=installation_id,
            ha_entity_id="media_player.echo_cucina",
            ha_registry_id="registry-echo-cucina-media",
            ha_domain="media_player",
            device_id="device-1",
        )
    )
    await session.commit()
    request = Request({"type": "http", "query_string": b"", "headers": []})
    context = TenantContext(user_id=user_id, tenant_id=tenant_id, role=TenantRole.OWNER)

    with patch("apps.cloud_api.app.alexa_routines._laboratory_only"):
        response = await routines_page(request, context, session)

    html = bytes(response.body).decode()
    assert html.count('name="volume_percent"') == 2
    assert "Crea routine richiamabile da Home Assistant" in html
