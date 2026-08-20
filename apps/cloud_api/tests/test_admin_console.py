"""Authenticated console, tenant isolation and safe command tests."""

import re
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.admin_console import _database_size_mb
from apps.cloud_api.app.database import get_database_session
from apps.cloud_api.app.domain.models import (
    AlexaDiscoverySnapshot,
    AuditEvent,
    Entity,
    Installation,
    MaintenanceRun,
)
from apps.cloud_api.app.evcp import CommandResultPayload, sessions
from apps.cloud_api.app.main import app
from apps.cloud_api.tests.conftest import SeededDomain


async def _client(session: AsyncSession) -> httpx.AsyncClient:
    async def database_override():  # type: ignore[no-untyped-def]
        yield session

    app.dependency_overrides[get_database_session] = database_override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


def _csrf(page: httpx.Response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match
    return match.group(1)


async def _login(client: httpx.AsyncClient, email: str, password: str) -> None:
    page = await client.get("/login")
    response = await client.post(
        "/login",
        data={"csrf_token": _csrf(page), "email": email, "password": password},
    )
    assert response.status_code == 303


async def test_dashboard_is_tenant_scoped_and_requires_admin(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    client = await _client(session)
    anonymous = await client.get("/dashboard", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"
    await _login(client, "owner@example.test", "owner-password-123")
    page = await client.get("/dashboard")
    assert page.status_code == 200
    assert "Home A" in page.text
    assert "Home B" not in page.text
    assert '<img class="brand-logo" src="/static/ekonex-cloud-voice.png"' in page.text
    assert 'alt="Ekonex Cloud Voice"' in page.text
    assert ">Impianti<" in page.text
    assert "Home Assistant" not in page.text
    for href in ("/dashboard", "/installations", "/activity", "/system", "/pair"):
        assert f'href="{href}"' in page.text
    assert 'href="/dashboard" class="active" aria-current="page"' in page.text
    assert (
        await client.get(f"/installations/{seeded_domain.installation_b_id}")
    ).status_code == 404
    await client.aclose()

    readonly = await _client(session)
    await _login(readonly, "readonly@example.test", "readonly-password-123")
    assert (await readonly.get("/dashboard")).status_code == 403
    await readonly.aclose()


async def test_installations_page_is_real_tenant_scoped_and_links_to_detail(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    installation = await session.get(Installation, seeded_domain.installation_a_id)
    assert installation is not None
    installation.ha_version = "2026.8"
    installation.connector_version = "0.1.5"
    installation.last_seen_at = datetime.now(UTC)
    await session.commit()
    client = await _client(session)
    await _login(client, "owner@example.test", "owner-password-123")
    page = await client.get("/installations", follow_redirects=False)
    assert page.status_code == 200
    assert "Home A" in page.text
    assert "Home B" not in page.text
    assert "Versione e-Control" in page.text
    assert "Versione Connector" in page.text
    assert "Entità esposte" in page.text
    assert "online" in page.text
    assert "2026.8" in page.text
    assert "0.1.5" in page.text
    assert f'href="/installations/{seeded_domain.installation_a_id}"' in page.text
    assert 'href="/installations" class="active" aria-current="page"' in page.text
    assert (
        await client.get(f"/installations/{seeded_domain.installation_b_id}")
    ).status_code == 404
    await client.aclose()


async def test_command_is_csrf_and_tenant_scoped_and_audited(
    session: AsyncSession, seeded_domain: SeededDomain, monkeypatch: object
) -> None:
    entity = await session.get(Entity, seeded_domain.entity_a_id)
    assert entity is not None
    entity.ha_registry_id = "registry-light-kitchen"
    entity.available = True
    await session.commit()
    client = await _client(session)
    await _login(client, "owner@example.test", "owner-password-123")
    page = await client.get(f"/installations/{seeded_domain.installation_a_id}")
    assert "Home Assistant" not in page.text
    csrf = _csrf(page)
    payload = {"csrf_token": csrf, "entity_id": str(entity.id), "operation": "power_on"}
    assert (
        await client.post(
            f"/installations/{seeded_domain.installation_a_id}/commands",
            data={**payload, "csrf_token": "bad"},
        )
    ).status_code == 403

    async def dispatch(installation_id, command_id, registry_id, command, timeout_seconds):  # type: ignore[no-untyped-def]
        assert installation_id == seeded_domain.installation_a_id
        assert registry_id == "registry-light-kitchen"
        return CommandResultPayload(session_id=uuid4(), command_id=command_id, status="success")

    monkeypatch.setattr(sessions, "dispatch", dispatch)  # type: ignore[attr-defined]
    response = await client.post(
        f"/installations/{seeded_domain.installation_a_id}/commands", data=payload
    )
    assert response.status_code == 200
    assert "Esito: success" in response.text
    audits = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.source == "admin_console")
                .order_by(AuditEvent.created_at)
            )
        ).all()
    )
    assert [event.result for event in audits] == ["pending", "success"]
    assert all(event.user_id == seeded_domain.user_a_id for event in audits)
    await client.aclose()


async def test_command_rejects_cross_tenant_entity(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    client = await _client(session)
    await _login(client, "owner@example.test", "owner-password-123")
    page = await client.get(f"/installations/{seeded_domain.installation_a_id}")
    response = await client.post(
        f"/installations/{seeded_domain.installation_a_id}/commands",
        data={
            "csrf_token": _csrf(page),
            "entity_id": str(seeded_domain.entity_b_id),
            "operation": "power_on",
        },
    )
    assert response.status_code == 404
    await client.aclose()


async def test_entity_names_dashboard_edit_reset_audit_and_tenant_isolation(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    client = await _client(session)
    await _login(client, "owner@example.test", "owner-password-123")
    edit_url = (
        f"/installations/{seeded_domain.installation_a_id}/entities/"
        f"{seeded_domain.entity_a_id}/edit"
    )
    page = await client.get(edit_url)
    assert page.status_code == 200
    assert "Nome e-Control" in page.text
    assert "Kitchen" in page.text
    assert 'name="friendly_name"' not in page.text
    assert "Ripristina nomi personalizzati" in page.text
    assert (
        await client.post(
            edit_url,
            data={"csrf_token": "forged", "action": "save", "display_name": "Wrong"},
        )
    ).status_code == 403

    response = await client.post(
        edit_url,
        data={
            "csrf_token": _csrf(page),
            "action": "save",
            "display_name": "  Ufficio Alex  ",
            "voice_name": " luce ufficio ",
            "voice_aliases": "ufficio\nUFFICIO\nluce alex\n<script>alert(1)</script>",
        },
    )
    assert response.status_code == 200
    assert "Nomi salvati" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    entity = await session.get(Entity, seeded_domain.entity_a_id)
    assert entity is not None
    assert entity.display_name == "Ufficio Alex"
    assert entity.voice_name == "luce ufficio"
    assert entity.voice_aliases == ["ufficio", "luce alex", "<script>alert(1)</script>"]
    detail = await client.get(f"/installations/{seeded_domain.installation_a_id}")
    assert "Ufficio Alex" in detail.text
    assert "Nome e-Control: Kitchen" in detail.text

    foreign = await client.get(
        f"/installations/{seeded_domain.installation_b_id}/entities/"
        f"{seeded_domain.entity_b_id}/edit"
    )
    assert foreign.status_code == 404
    reset_page = await client.get(edit_url)
    reset = await client.post(edit_url, data={"csrf_token": _csrf(reset_page), "action": "reset"})
    assert reset.status_code == 200
    await session.refresh(entity)
    assert entity.display_name is None
    assert entity.voice_name is None
    assert entity.voice_aliases == []
    audits = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type.in_(["entity_names.updated", "entity_names.reset"]))
                .order_by(AuditEvent.created_at)
            )
        ).all()
    )
    assert [event.event_type for event in audits] == [
        "entity_names.updated",
        "entity_names.reset",
    ]
    assert audits[0].payload_redacted_json == {
        "entity_id": str(entity.id),
        "changed_fields": ["display_name", "voice_name", "voice_aliases"],
    }
    await client.aclose()


async def test_installation_table_renders_effective_voice_names_and_aliases(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    customized = await session.get(Entity, seeded_domain.entity_a_id)
    assert customized is not None
    customized.voice_name = "Luce ufficio contabilità"
    customized.voice_aliases = ["ufficio", "luce contabilità", "lampada contabilità"]
    session.add_all(
        [
            Entity(
                installation_id=seeded_domain.installation_a_id,
                ha_entity_id="light.entrance",
                ha_registry_id="registry-entrance",
                ha_domain="light",
                friendly_name="Luce ingresso sincronizzata",
                display_name="Lampada ingresso",
            ),
            Entity(
                installation_id=seeded_domain.installation_a_id,
                ha_entity_id="light.terrace",
                ha_registry_id="registry-terrace",
                ha_domain="light",
                friendly_name="Luce terrazza",
            ),
        ]
    )
    await session.commit()

    client = await _client(session)
    await _login(client, "owner@example.test", "owner-password-123")
    page = await client.get(f"/installations/{seeded_domain.installation_a_id}")

    assert page.status_code == 200
    assert "Nome vocale: Luce ufficio contabilità" in page.text
    assert "Alias: ufficio · luce contabilità · lampada contabilità" in page.text
    assert "Nome vocale: Lampada ingresso" in page.text
    assert "Nome vocale: Luce terrazza" in page.text
    assert page.text.count("Alias: —") >= 2
    await client.aclose()


async def test_installation_renders_latest_alexa_discovery_and_proactive_reports(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    endpoint_value = f"ev1_{seeded_domain.entity_a_id.hex}"
    session.add(
        AlexaDiscoverySnapshot(
            tenant_id=seeded_domain.tenant_a_id,
            installation_id=seeded_domain.installation_a_id,
            endpoint_count=1,
            discovered_at=datetime(2026, 8, 20, 14, 5, tzinfo=UTC),
            endpoints_json=[
                {
                    "endpoint_id": endpoint_value,
                    "voice_name": "Luce ufficio Alex",
                    "domain": "light",
                }
            ],
            changes_json=[
                {
                    "endpoint_id": endpoint_value,
                    "voice_name": "Luce ufficio Alex",
                    "domain": "light",
                    "change": "new",
                },
                {
                    "endpoint_id": "ev1_removed",
                    "voice_name": "Vecchia luce",
                    "domain": "light",
                    "change": "removed",
                },
            ],
        )
    )
    session.add_all(
        [
            AuditEvent(
                tenant_id=seeded_domain.tenant_a_id,
                installation_id=seeded_domain.installation_a_id,
                source="alexa_event_gateway",
                event_type="alexa.discovery.add_or_update",
                payload_redacted_json={"endpoint_id": endpoint_value},
                result="success",
            ),
            AuditEvent(
                tenant_id=seeded_domain.tenant_a_id,
                installation_id=seeded_domain.installation_a_id,
                source="alexa_event_gateway",
                event_type="alexa.discovery.delete",
                payload_redacted_json={"endpoint_id": "ev1_removed"},
                result="error",
            ),
        ]
    )
    await session.commit()
    client = await _client(session)
    await _login(client, "owner@example.test", "owner-password-123")
    page = await client.get(f"/installations/{seeded_domain.installation_a_id}")
    assert page.status_code == 200
    assert "Alexa - ultima sincronizzazione" in page.text
    assert "Ultima Discovery: 20/08/2026 14:05" in page.text
    assert "Dispositivi inviati: 1" in page.text
    assert "Luce ufficio Alex" in page.text
    assert endpoint_value in page.text
    assert "Nuovo" in page.text
    assert "Rimosso" in page.text
    assert "Ultimo AddOrUpdateReport" in page.text
    assert "Ultimo DeleteReport" in page.text
    assert "esito success" in page.text
    assert "esito error" in page.text
    assert "ev1_private" not in page.text
    await client.aclose()


async def test_installation_renders_no_alexa_discovery_tenant_safely(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    session.add(
        AlexaDiscoverySnapshot(
            tenant_id=seeded_domain.tenant_b_id,
            installation_id=seeded_domain.installation_b_id,
            endpoint_count=1,
            endpoints_json=[
                {
                    "endpoint_id": "ev1_private",
                    "voice_name": "Segreto tenant B",
                    "domain": "light",
                }
            ],
            changes_json=[],
        )
    )
    await session.commit()
    client = await _client(session)
    await _login(client, "owner@example.test", "owner-password-123")
    page = await client.get(f"/installations/{seeded_domain.installation_a_id}")
    assert page.status_code == 200
    assert "Nessuna sincronizzazione Alexa registrata" in page.text
    assert "Segreto tenant B" not in page.text
    await client.aclose()


async def test_entity_name_edit_rejects_voice_collision(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    second = Entity(
        installation_id=seeded_domain.installation_a_id,
        ha_entity_id="light.office",
        ha_registry_id="registry-office",
        ha_domain="light",
        friendly_name="Office",
        voice_name="ufficio",
    )
    session.add(second)
    await session.commit()
    client = await _client(session)
    await _login(client, "owner@example.test", "owner-password-123")
    edit_url = (
        f"/installations/{seeded_domain.installation_a_id}/entities/"
        f"{seeded_domain.entity_a_id}/edit"
    )
    page = await client.get(edit_url)
    response = await client.post(
        edit_url,
        data={
            "csrf_token": _csrf(page),
            "action": "save",
            "display_name": "",
            "voice_name": "UFFICIO",
            "voice_aliases": "",
        },
    )
    assert response.status_code == 409
    assert "già utilizzato" in response.text
    entity = await session.get(Entity, seeded_domain.entity_a_id)
    assert entity is not None and entity.voice_name is None
    await client.aclose()


async def test_activity_and_system_views_remain_tenant_scoped(
    session: AsyncSession, seeded_domain: SeededDomain
) -> None:
    session.add(
        AuditEvent(
            tenant_id=seeded_domain.tenant_b_id,
            installation_id=seeded_domain.installation_b_id,
            user_id=seeded_domain.user_b_id,
            source="admin_console",
            event_type="private_foreign_event",
            payload_redacted_json={},
            result="success",
        )
    )
    session.add(
        MaintenanceRun(
            kind="retention_cleanup",
            status="ok",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            duration_ms=42,
            deleted_counts_json={"state_history": 1},
        )
    )
    await session.commit()
    client = await _client(session)
    await _login(client, "owner@example.test", "owner-password-123")
    activity = await client.get("/activity", params={"event_type": "admin_login"})
    assert activity.status_code == 200
    assert 'href="/activity" class="active" aria-current="page"' in activity.text
    assert "Home Assistant" not in activity.text
    assert "admin_login" in activity.text
    assert "private_foreign_event" not in activity.text
    system = await client.get("/system")
    assert system.status_code == 200
    assert 'href="/system" class="active" aria-current="page"' in system.text
    assert "Home Assistant" not in system.text
    assert "Campioni storico" in system.text
    assert "Dimensione reale DB" in system.text
    assert "Ultima pulizia" in system.text
    assert "Esito ultima pulizia" in system.text
    assert "Prossima pulizia prevista" in system.text
    assert "Storico stati: 30 giorni" in system.text
    assert "Audit amministrativo: 365 giorni" in system.text
    await client.aclose()


async def test_database_size_uses_postgresql_authoritative_function_and_decimal_mb() -> None:
    fake = MagicMock(spec=AsyncSession)
    fake.get_bind.return_value.dialect.name = "postgresql"
    fake.scalar = AsyncMock(return_value=12_500_000)
    size = await _database_size_mb(cast(AsyncSession, fake))
    assert size == 12.5
    statement = str(fake.scalar.await_args.args[0])
    assert "pg_database_size" in statement
    assert "current_database" in statement
