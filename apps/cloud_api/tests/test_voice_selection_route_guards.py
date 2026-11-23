from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from apps.cloud_api.app import admin_console, voice_scope
from apps.cloud_api.app.domain.models import ConnectorCredential, Entity
from apps.cloud_api.app.voice_selection_store import (
    VoiceSelectionFence,
    persist_selection,
    register_selection_session,
)
from apps.cloud_api.tests.test_admin_console import _client as admin_client, _csrf, _login
from apps.cloud_api.tests.test_alexa import _access, _client as alexa_client, _directive


async def _strict_selection(session, seeded_domain, registry_ids):
    credential = ConnectorCredential(
        installation_id=seeded_domain.installation_a_id, secret_hash="a" * 64
    )
    session.add(credential)
    await session.flush()
    session_id = uuid4()
    await register_selection_session(
        session,
        seeded_domain.installation_a_id,
        seeded_domain.tenant_a_id,
        session_id,
        credential.id,
    )
    await persist_selection(
        session,
        seeded_domain.installation_a_id,
        seeded_domain.tenant_a_id,
        session_id,
        1,
        registry_ids,
    )
    await session.commit()
    return session_id


@pytest.mark.parametrize(
    "mode,expected", [("unverified", 404), ("unselected", 404), ("selected", 200), ("legacy", 200)]
)
async def test_entity_edit_route_obeys_strict_selection(
    session, seeded_domain, monkeypatch, mode, expected
):
    flags = admin_console.get_settings().model_copy(
        update={
            "voice_selection_v1_enabled": mode != "legacy",
            "voice_selection_projection_enabled": mode != "legacy",
        }
    )
    monkeypatch.setattr(voice_scope, "get_settings", lambda: flags)
    monkeypatch.setattr(admin_console, "reconcile_discovery_safely", AsyncMock())
    entity = await session.get(Entity, seeded_domain.entity_a_id)
    entity.ha_registry_id = "synthetic-id"
    entity.voice_name = "OriginalName"
    entity.control_exposed = False
    if mode in {"unselected", "selected"}:
        await _strict_selection(
            session, seeded_domain, ["synthetic-id"] if mode == "selected" else []
        )
    else:
        await session.commit()
    client = await admin_client(session)
    try:
        await _login(client, "owner@example.test", "owner-password-123")
        csrf = _csrf(await client.get("/dashboard"))
        route = f"/installations/{seeded_domain.installation_a_id}/entities/{entity.id}/edit"
        page = await client.get(route)
        assert page.status_code == expected
        if expected == 200:
            csrf = _csrf(page)
        response = await client.post(route, data={"csrf_token": csrf, "action": "reset"})
        assert response.status_code == expected
        await session.refresh(entity)
        assert entity.voice_name == ("OriginalName" if expected == 404 else None)
    finally:
        await client.aclose()


async def test_entity_edit_post_rechecks_selection_after_writer_lock(
    session, seeded_domain, monkeypatch
):
    from apps.cloud_api.app import wear_bridge

    flags = admin_console.get_settings().model_copy(
        update={"voice_selection_v1_enabled": True, "voice_selection_projection_enabled": True}
    )
    monkeypatch.setattr(voice_scope, "get_settings", lambda: flags)
    entity = await session.get(Entity, seeded_domain.entity_a_id)
    entity.ha_registry_id = "synthetic-id"
    entity.voice_name = "OriginalName"
    await _strict_selection(session, seeded_domain, ["synthetic-id"])
    client = await admin_client(session)
    try:
        await _login(client, "owner@example.test", "owner-password-123")
        route = f"/installations/{seeded_domain.installation_a_id}/entities/{entity.id}/edit"
        csrf = _csrf(await client.get(route))
        original = wear_bridge.lock_wear_writer

        async def disable_during_lock(*args, **kwargs):
            result = await original(*args, **kwargs)
            fence = await session.get(VoiceSelectionFence, seeded_domain.installation_a_id)
            fence.active = False
            await session.flush()
            return result

        monkeypatch.setattr(wear_bridge, "lock_wear_writer", disable_during_lock)
        assert (
            await client.post(route, data={"csrf_token": csrf, "action": "reset"})
        ).status_code == 404
        await session.refresh(entity)
        assert entity.voice_name == "OriginalName"
    finally:
        await client.aclose()


async def test_unselected_entity_is_absent_from_page_and_alexa_discovery(
    session, seeded_domain, monkeypatch
):
    flags = admin_console.get_settings().model_copy(
        update={"voice_selection_v1_enabled": True, "voice_selection_projection_enabled": True}
    )
    monkeypatch.setattr(voice_scope, "get_settings", lambda: flags)
    entity = await session.get(Entity, seeded_domain.entity_a_id)
    entity.ha_registry_id = "synthetic-registry"
    entity.control_exposed = True
    entity.friendly_name = "SyntheticUnselectedLight"
    await _strict_selection(session, seeded_domain, [])
    portal = await admin_client(session)
    try:
        await _login(portal, "owner@example.test", "owner-password-123")
        base = f"/installations/{seeded_domain.installation_a_id}"
        assert (await portal.get(base + f"/entities/{entity.id}/edit")).status_code == 404
    finally:
        await portal.aclose()
    token = await _access(session, seeded_domain)
    alexa = await alexa_client(session)
    try:
        response = await alexa.post(
            "/alexa/v1/directive", json=_directive(token, "Alexa.Discovery", "Discover")
        )
        assert response.status_code == 200
        endpoints = response.json()["event"]["payload"]["endpoints"]
        assert all(item["endpointId"] != "ev1_" + entity.id.hex for item in endpoints)
    finally:
        await alexa.aclose()
