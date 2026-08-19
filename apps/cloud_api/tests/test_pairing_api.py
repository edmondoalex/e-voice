"""Authenticated pairing claim API and portal tests."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.database import get_database_session
from apps.cloud_api.app.domain.models import PairingSession, TenantMembership
from apps.cloud_api.app.main import app


async def _client(session: AsyncSession) -> httpx.AsyncClient:
    async def database_override():  # type: ignore[no-untyped-def]
        yield session

    app.dependency_overrides[get_database_session] = database_override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _csrf(page: httpx.Response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


async def _login(
    client: httpx.AsyncClient,
    email: str = "owner@example.test",
    password: str = "owner-password-123",
) -> httpx.Response:
    page = await client.get("/login")
    assert page.status_code == 200
    return await client.post(
        "/login",
        data={"csrf_token": _csrf(page), "email": email, "password": password},
        follow_redirects=False,
    )


async def _start(client: httpx.AsyncClient, suffix: str) -> dict[str, str]:
    response = await client.post(
        "/connector/v1/pairing/sessions",
        json={"installation_nonce": f"haos-portal-test-{suffix:0<16}"},
    )
    assert response.status_code == 200
    return {str(key): str(value) for key, value in response.json().items()}


async def test_valid_claim_hides_credential_and_poll_delivers_it_once(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    assert (await _login(client)).status_code == 303
    started = await _start(client, "valid")
    response = await client.post(
        "/connector/v1/pairing/claims",
        json={"code": started["code"], "installation_name": "Casa Rossi"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "paired"
    assert "connector_credential" not in response.json()
    assert "session_id" not in response.json()
    poll_headers = {"Authorization": f"Pairing {started['polling_secret']}"}
    first = await client.get(
        f"/connector/v1/pairing/sessions/{started['session_id']}", headers=poll_headers
    )
    second = await client.get(
        f"/connector/v1/pairing/sessions/{started['session_id']}", headers=poll_headers
    )
    assert first.json()["connector_credential"].startswith("evc_")
    assert second.json()["connector_credential"] is None
    await client.aclose()


async def test_invalid_expired_and_replay(session: AsyncSession, seeded_domain: object) -> None:
    client = await _client(session)
    assert (await _login(client)).status_code == 303
    invalid = await client.post(
        "/connector/v1/pairing/claims",
        json={"code": "AAAA-AAAA", "installation_name": "Invalid"},
    )
    assert invalid.status_code == 400
    expired = await _start(client, "expired")
    row = await session.scalar(
        select(PairingSession).where(PairingSession.id == UUID(expired["session_id"]))
    )
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    response = await client.post(
        "/connector/v1/pairing/claims",
        json={"code": expired["code"], "installation_name": "Expired"},
    )
    assert response.status_code == 410
    replay = await _start(client, "replay")
    payload = {"code": replay["code"], "installation_name": "Once"}
    assert (await client.post("/connector/v1/pairing/claims", json=payload)).status_code == 200
    assert (await client.post("/connector/v1/pairing/claims", json=payload)).status_code == 400
    await client.aclose()


async def test_claim_rate_limit_and_readonly_role(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    assert (await _login(client)).status_code == 303
    for index in range(5):
        response = await client.post(
            "/connector/v1/pairing/claims",
            json={"code": f"AAAA-AAA{index + 2}", "installation_name": "Guess"},
        )
        assert response.status_code == 400
    assert (
        await client.post(
            "/connector/v1/pairing/claims",
            json={"code": "BBBB-BBBB", "installation_name": "Limited"},
        )
    ).status_code == 429
    readonly = await _client(session)
    assert (
        await _login(readonly, "readonly@example.test", "readonly-password-123")
    ).status_code == 303
    started = await _start(readonly, "readonly")
    assert (
        await readonly.post(
            "/connector/v1/pairing/claims",
            json={"code": started["code"], "installation_name": "Readonly"},
        )
    ).status_code == 403
    await client.aclose()
    await readonly.aclose()


async def test_login_rate_limit_cookie_and_logout(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    login_page = await client.get("/login")
    assert '<img class="login-logo" src="/static/icon.png"' in login_page.text
    assert 'alt="Ekonex Cloud Voice"' in login_page.text
    assert ">EKONEX VOICE<" not in login_page.text
    assert "font-family:system-ui,sans-serif" in login_page.text
    assert "width:min(100%,440px);background:white;border-radius:18px" in login_page.text
    assert "background:var(--brand);color:white" in login_page.text
    for _ in range(5):
        assert (await _login(client, password="wrong-password-123")).status_code == 401
    assert (await _login(client, password="wrong-password-123")).status_code == 429
    authenticated = await _client(session)
    login = await _login(authenticated, "owner-b@example.test", "owner-b-password-123")
    assert login.status_code == 303
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    page = await authenticated.get("/pair")
    logout = await authenticated.post(
        "/logout", data={"csrf_token": _csrf(page)}, follow_redirects=False
    )
    assert logout.status_code == 303
    assert (await authenticated.get("/pair", follow_redirects=False)).status_code == 303
    await client.aclose()
    await authenticated.aclose()


async def test_tenant_selection_only_allows_memberships(
    session: AsyncSession, seeded_domain: object
) -> None:
    membership = await session.scalar(
        select(TenantMembership).where(
            TenantMembership.user_id == seeded_domain.user_a_id  # type: ignore[attr-defined]
        )
    )
    assert membership is not None
    session.add(
        TenantMembership(
            tenant_id=seeded_domain.tenant_b_id,  # type: ignore[attr-defined]
            user_id=seeded_domain.user_a_id,  # type: ignore[attr-defined]
            role=membership.role,
        )
    )
    await session.commit()
    client = await _client(session)
    assert (await _login(client)).status_code == 303
    page = await client.get("/pair")
    assert "Scegli cliente/tenant" in page.text
    denied = await client.post(
        "/pair/select-tenant",
        data={"csrf_token": _csrf(page), "tenant_id": str(uuid4())},
    )
    assert denied.status_code == 403
    page = await client.get("/pair")
    selected = await client.post(
        "/pair/select-tenant",
        data={
            "csrf_token": _csrf(page),
            "tenant_id": str(seeded_domain.tenant_b_id),  # type: ignore[attr-defined]
        },
        follow_redirects=False,
    )
    assert selected.status_code == 303
    assert "Collega Home Assistant" in (await client.get("/pair")).text
    await client.aclose()


async def test_pair_page_render_and_submit_success_and_error(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    assert (await client.get("/pair", follow_redirects=False)).status_code == 303
    assert (await _login(client)).status_code == 303
    page = await client.get("/pair")
    assert "Collega Home Assistant" in page.text and "XXXX-XXXX" in page.text
    assert '<img class="brand-logo" src="/static/icon.png"' in page.text
    assert 'alt="Ekonex Cloud Voice"' in page.text
    assert ">EKONEX VOICE<" not in page.text
    logo = await client.get("/static/icon.png")
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/png"
    failed = await client.post(
        "/pair",
        data={"csrf_token": _csrf(page), "code": "AAAA-AAAA", "installation_name": "Bad"},
    )
    assert failed.status_code == 400
    assert "Codice non valido" in failed.text
    started = await _start(client, "portal")
    page = await client.get("/pair")
    succeeded = await client.post(
        "/pair",
        data={
            "csrf_token": _csrf(page),
            "code": started["code"],
            "installation_name": "Casa Portale",
        },
    )
    assert succeeded.status_code == 200
    assert "Home Assistant collegato correttamente" in succeeded.text
    assert "evc_" not in succeeded.text
    assert started["polling_secret"] not in succeeded.text
    await client.aclose()


async def test_pair_portal_rejects_bad_csrf(session: AsyncSession, seeded_domain: object) -> None:
    client = await _client(session)
    assert (await _login(client)).status_code == 303
    response = await client.post(
        "/pair",
        data={"csrf_token": "forged", "code": "AAAA-AAAA", "installation_name": "Forged"},
    )
    assert response.status_code == 403
    assert "polling_secret" not in response.text
    await client.aclose()
