import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.config import get_settings
from apps.cloud_api.app.database import get_database_session
from apps.cloud_api.app.domain.models import (
    AuditEvent,
    Entity,
    ExternalInstallationBinding,
    InstallationBoundSession,
    UserInstallationAssignment,
)
from apps.cloud_api.app.evcp import CommandResultPayload, sessions
from apps.cloud_api.app.installation_bound import InstallationBoundService, encode_launch
from apps.cloud_api.app.installation_bound_api import bounded_event_payload
from apps.cloud_api.app.main import app


async def _client(session: AsyncSession) -> httpx.AsyncClient:
    async def database_override():
        yield session

    app.dependency_overrides[get_database_session] = database_override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


async def _setup(session, seeded):
    settings = get_settings()
    session.add_all([
        ExternalInstallationBinding(
            issuer=settings.econtrol_launch_issuer,
            external_installation_id="external-a",
            tenant_id=seeded.tenant_a_id,
            installation_id=seeded.installation_a_id,
        ),
        UserInstallationAssignment(
            tenant_id=seeded.tenant_a_id,
            user_id=seeded.user_a_id,
            installation_id=seeded.installation_a_id,
            access_level="manage",
            granted_by_user_id=seeded.user_a_id,
        ),
    ])
    await session.commit()
    now = datetime.now(UTC)
    claims = {
        "issuer": settings.econtrol_launch_issuer,
        "audience": settings.econtrol_launch_audience,
        "subject": str(seeded.user_a_id),
        "tenant_id": str(seeded.tenant_a_id),
        "installation_id": "external-a",
        "requested_capabilities": ["readonly", "use", "manage", "command", "export", "stream"],
        "issued_at": int(now.timestamp()),
        "expires_at": int((now + timedelta(minutes=2)).timestamp()),
        "jti": "api-jti-a",
        "correlation_id": "api-corr-a",
    }
    return encode_launch(claims, settings.econtrol_launch_secret.encode())


async def test_exchange_cookie_is_path_restricted_and_global_cookie_cannot_amplify(session, seeded_domain):
    launch = await _setup(session, seeded_domain)
    client = await _client(session)
    client.cookies.set("ekonex_portal_session", "global-session-must-not-amplify")
    response = await client.post("/bounded/exchange", json={"launch": launch})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert "Path=/bounded" in cookie
    assert "Max-Age=28800" in cookie
    assert response.json()["effective_installation_id"] == str(seeded_domain.installation_a_id)
    own = await client.get(f"/bounded/installations/{seeded_domain.installation_a_id}/entities")
    assert own.status_code == 200
    assert [item["id"] for item in own.json()] == [str(seeded_domain.entity_a_id)]
    foreign = await client.get(f"/bounded/installations/{seeded_domain.installation_b_id}/entities")
    assert foreign.status_code == 404
    await client.aclose()
    app.dependency_overrides.clear()


async def test_prepare_and_open_use_opaque_single_use_code(session, seeded_domain):
    launch = await _setup(session, seeded_domain)
    client = await _client(session)
    prepared = await client.post("/bounded/prepare", json={"launch": launch})
    assert prepared.status_code == 200
    launch_url = prepared.json()["launch_url"]
    assert launch not in launch_url
    assert "code=" in launch_url
    path = launch_url.removeprefix("https://voice.e-control.tech")
    opened = await client.get(path, follow_redirects=False)
    assert opened.status_code == 200
    assert 'content="0;url=/bounded/app"' in opened.text
    assert "location" not in opened.headers
    assert "HttpOnly" in opened.headers["set-cookie"]
    assert "SameSite=strict" in opened.headers["set-cookie"]
    assert opened.headers["cache-control"] == "no-store"
    assert opened.headers["content-security-policy"] == "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    page = await client.get("/bounded/app")
    assert page.status_code == 200
    assert "Sessione e-Voice legata a questo impianto" in page.text
    assert "Comandi diretti" in page.text
    assert "Aspirapolvere" in page.text
    assert "Dispositivi vocali" not in page.text
    assert "<aside>" not in page.text
    assert str(seeded_domain.installation_b_id) not in page.text
    assert (await client.get("/bounded/context")).status_code == 200
    replay = await client.get(path, follow_redirects=False)
    assert replay.status_code == 403
    await client.aclose()
    app.dependency_overrides.clear()


async def test_bounded_app_renders_safe_controls_and_dispatches_with_csrf(
    session, seeded_domain, monkeypatch
):
    launch = await _setup(session, seeded_domain)
    entity = await session.get(Entity, seeded_domain.entity_a_id)
    assert entity is not None
    entity.ha_registry_id = "registry-a"
    await session.commit()
    monkeypatch.setattr(
        sessions,
        "dispatch",
        AsyncMock(return_value=CommandResultPayload(
            session_id=uuid4(), command_id=uuid4(), status="success"
        )),
    )
    client = await _client(session)
    assert (await client.post("/bounded/exchange", json={"launch": launch})).status_code == 200
    page = await client.get("/bounded/app")
    assert page.status_code == 200
    assert ">ON<" in page.text and ">OFF<" in page.text
    assert 'class="entity-group"' in page.text
    assert "Categoria vocale" in page.text
    assert "Comandi diretti" in page.text
    assert "Dispositivi vocali" not in page.text
    assert "<aside>" not in page.text
    assert 'href="/bounded/context"' not in page.text
    csrf = re.search(r'name="csrf_token" value="([0-9a-f]{64})"', page.text)
    assert csrf is not None
    denied = await client.post("/bounded/ui/commands", data={
        "csrf_token": "0" * 64, "entity_id": str(seeded_domain.entity_a_id),
        "operation": "power_on",
    })
    assert denied.status_code == 403
    foreign = await client.post("/bounded/ui/commands", data={
        "csrf_token": csrf.group(1), "entity_id": str(seeded_domain.entity_b_id),
        "operation": "power_on",
    })
    assert foreign.status_code == 404
    accepted = await client.post("/bounded/ui/commands", data={
        "csrf_token": csrf.group(1), "entity_id": str(seeded_domain.entity_a_id),
        "operation": "power_on",
    }, follow_redirects=False)
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/bounded/app?result=success"
    sessions.dispatch.assert_awaited_once()
    feedback = await client.get(accepted.headers["location"])
    assert "Comando eseguito" in feedback.text
    await client.aclose()
    app.dependency_overrides.clear()


def test_frame_policy_allows_only_explicit_http_origins(monkeypatch):
    from apps.cloud_api.app import installation_bound_api

    monkeypatch.setattr(
        installation_bound_api,
        "get_settings",
        lambda: SimpleNamespace(
            econtrol_bounded_frame_ancestors=
            "http://192.168.3.24:8125,http://192.168.3.24:8123,https://manager-easas.e-control.tech"
        ),
    )
    ancestors, same_site = installation_bound_api._frame_policy()
    assert ancestors == (
        "http://192.168.3.24:8125 http://192.168.3.24:8123 "
        "https://manager-easas.e-control.tech"
    )
    assert same_site == "none"

async def test_export_stream_and_foreign_command_are_bounded(session, seeded_domain):
    launch = await _setup(session, seeded_domain)
    client = await _client(session)
    assert (await client.post("/bounded/exchange", json={"launch": launch})).status_code == 200
    export = await client.get(f"/bounded/installations/{seeded_domain.installation_a_id}/activity/export")
    assert export.status_code == 200
    stream = await client.get(f"/bounded/installations/{seeded_domain.installation_a_id}/events/stream")
    assert stream.status_code == 200
    denied = await client.post("/bounded/commands", json={
        "installation_id": str(seeded_domain.installation_b_id),
        "registry_id": "entity-b",
        "command": {"operation": "power_on"},
    })
    assert denied.status_code == 404
    await client.aclose()
    app.dependency_overrides.clear()


async def test_revocation_blocks_export_and_stream(session, seeded_domain):
    launch = await _setup(session, seeded_domain)
    client = await _client(session)
    assert (await client.post("/bounded/exchange", json={"launch": launch})).status_code == 200
    row = await session.scalar(select(InstallationBoundSession))
    row.revoked_at = datetime.now(UTC)
    await session.commit()
    export = await client.get(f"/bounded/installations/{seeded_domain.installation_a_id}/activity/export")
    stream = await client.get(f"/bounded/installations/{seeded_domain.installation_a_id}/events/stream")
    assert export.status_code == 401
    assert stream.status_code == 401
    await client.aclose()
    app.dependency_overrides.clear()


async def test_positive_command_uses_router_after_scope_and_audits_redacted(
    session, seeded_domain, monkeypatch
):
    launch = await _setup(session, seeded_domain)
    entity = await session.get(Entity, seeded_domain.entity_a_id)
    assert entity is not None
    entity.ha_registry_id = "registry-a"
    await session.commit()
    command_id = uuid4()
    monkeypatch.setattr(
        sessions,
        "dispatch",
        AsyncMock(return_value=CommandResultPayload(
            session_id=uuid4(), command_id=command_id, status="success"
        )),
    )
    client = await _client(session)
    assert (await client.post("/bounded/exchange", json={"launch": launch})).status_code == 200
    response = await client.post("/bounded/commands", json={
        "installation_id": str(seeded_domain.installation_a_id),
        "registry_id": "registry-a",
        "command": {"operation": "power_on"},
    })
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    sessions.dispatch.assert_awaited_once()
    audits = list((await session.scalars(select(AuditEvent).where(
        AuditEvent.source == "econtrol_bounded"
    ).order_by(AuditEvent.created_at))).all())
    assert [item.event_type for item in audits] == ["bounded.launch", "bounded.command"]
    assert audits[0].request_id == "api-corr-a"
    assert "jti_hash" in audits[0].payload_redacted_json
    assert "synthetic" not in str(audits[0].payload_redacted_json)
    assert audits[1].payload_redacted_json == {
        "registry_id": "registry-a",
        "operation": "power_on",
    }
    await client.aclose()
    app.dependency_overrides.clear()


async def test_revocation_while_stream_is_open_stops_before_next_item(session, seeded_domain):
    launch = await _setup(session, seeded_domain)
    client = await _client(session)
    assert (await client.post("/bounded/exchange", json={"launch": launch})).status_code == 200
    raw = client.cookies.get("ekonex_installation_bound")
    settings = get_settings()
    service = InstallationBoundService(
        session,
        issuer=settings.econtrol_launch_issuer,
        audience=settings.econtrol_launch_audience,
        secret=settings.econtrol_launch_secret.encode(),
    )
    events = [
        type("Event", (), {"id": uuid4(), "event_type": "first"})(),
        type("Event", (), {"id": uuid4(), "event_type": "second"})(),
    ]
    stream = bounded_event_payload(service, raw, events)
    assert "first" in await anext(stream)
    await service.revoke(raw)
    await session.commit()
    try:
        await anext(stream)
        assert False, "revoked stream emitted another item"
    except StopAsyncIteration:
        pass
    await client.aclose()
    app.dependency_overrides.clear()


async def test_http_tampering_vectors_cannot_change_effective_installation(
    session, seeded_domain
):
    launch = await _setup(session, seeded_domain)
    client = await _client(session)
    assert (await client.post("/bounded/exchange", json={"launch": launch})).status_code == 200
    own_url = f"/bounded/installations/{seeded_domain.installation_a_id}/entities"
    own = await client.get(
        own_url,
        params={"installation_id": str(seeded_domain.installation_b_id)},
        headers={"X-Installation-ID": str(seeded_domain.installation_b_id)},
    )
    assert own.status_code == 200
    assert [item["id"] for item in own.json()] == [str(seeded_domain.entity_a_id)]
    assert (await client.get(
        f"/bounded/installations/{seeded_domain.installation_b_id}/entities"
    )).status_code == 404
    assert (await client.post("/bounded/commands", json={
        "installation_id": str(seeded_domain.installation_b_id),
        "registry_id": "registry-b",
        "command": {"operation": "power_on"},
    })).status_code == 404
    assert (await client.post(
        "/bounded/exchange",
        content=f"launch={launch}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )).status_code == 422
    assert (await client.get("/bounded/ws")).status_code == 404
    await client.aclose()
    app.dependency_overrides.clear()


async def test_denied_launch_log_contains_no_token(session, seeded_domain, caplog):
    client = await _client(session)
    secret_marker = "never-log-this-launch-token"
    response = await client.post("/bounded/exchange", json={"launch": secret_marker * 3})
    assert response.status_code == 403
    assert secret_marker not in caplog.text
    await client.aclose()
    app.dependency_overrides.clear()
