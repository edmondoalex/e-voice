from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.database import get_database_session
from apps.cloud_api.app.domain.enums import TenantRole
from apps.cloud_api.app.domain.models import Tenant, TenantMembership
from apps.cloud_api.app.main import app


async def _client(session: AsyncSession) -> httpx.AsyncClient:
    async def database_override():  # type: ignore[no-untyped-def]
        yield session

    app.dependency_overrides[get_database_session] = database_override
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    )


def _authorize_params() -> dict[str, str]:
    return {
        "response_type": "code",
        "client_id": "ekonex-alexa-development",
        "redirect_uri": "https://pitangui.amazon.com/api/skill/link/DEVELOPMENT",
        "state": "alexa-csrf-state",
        "scope": "alexa:smart_home",
    }


async def test_authorize_shows_ekonex_login_when_not_authenticated(
    session: AsyncSession,
) -> None:
    client = await _client(session)
    response = await client.get("/oauth/authorize", params=_authorize_params())

    assert response.status_code == 200
    assert "Collega Alexa a e-Control" in response.text
    assert 'action="/oauth/alexa/login"' in response.text
    assert "ekonex_login_csrf" in response.cookies
    await client.aclose()


async def test_alexa_login_reuses_portal_credentials_and_issues_code(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    params = _authorize_params()
    start = await client.get("/oauth/authorize", params=params)
    csrf = start.cookies["ekonex_login_csrf"]

    login = await client.post(
        "/oauth/alexa/login",
        data={
            **params,
            "csrf_token": csrf,
            "email": "owner@example.test",
            "password": "owner-password-123",
        },
        cookies={"ekonex_login_csrf": csrf},
    )
    assert login.status_code == 303
    assert login.headers["location"].startswith("/oauth/authorize?")
    assert "ekonex_portal_session" in login.cookies

    resumed = await client.get(
        login.headers["location"],
        cookies={"ekonex_portal_session": login.cookies["ekonex_portal_session"]},
    )
    assert resumed.status_code == 302
    location = urlparse(resumed.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == params["redirect_uri"]
    query = parse_qs(location.query)
    assert query["state"] == [params["state"]]
    assert query["code"][0].startswith("eac_")
    tokens = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": params["redirect_uri"],
            "client_id": params["client_id"],
            "client_secret": "change-me",
        },
    )
    assert tokens.status_code == 200
    assert tokens.json()["access_token"].startswith("eaa_")
    assert tokens.json()["refresh_token"].startswith("ear_")
    await client.aclose()


async def test_invalid_redirect_is_rejected_before_login(session: AsyncSession) -> None:
    client = await _client(session)
    params = _authorize_params() | {"redirect_uri": "https://attacker.example/callback"}
    response = await client.get("/oauth/authorize", params=params)
    assert response.status_code == 400
    missing_state = _authorize_params()
    del missing_state["state"]
    assert (await client.get("/oauth/authorize", params=missing_state)).status_code == 400
    await client.aclose()


async def test_invalid_login_does_not_create_portal_session(session: AsyncSession) -> None:
    client = await _client(session)
    params = _authorize_params()
    start = await client.get("/oauth/authorize", params=params)
    csrf = start.cookies["ekonex_login_csrf"]
    response = await client.post(
        "/oauth/alexa/login",
        data={
            **params,
            "csrf_token": csrf,
            "email": "owner@example.test",
            "password": "wrong-password-123",
        },
        cookies={"ekonex_login_csrf": csrf},
    )
    assert response.status_code == 401
    assert "Credenziali non valide" in response.text
    assert "ekonex_portal_session" not in response.cookies
    await client.aclose()


async def test_login_rate_limit_is_reused_by_alexa_flow(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    params = _authorize_params()
    start = await client.get("/oauth/authorize", params=params)
    csrf = start.cookies["ekonex_login_csrf"]
    data = {
        **params,
        "csrf_token": csrf,
        "email": "owner@example.test",
        "password": "wrong-password-123",
    }
    for _ in range(5):
        assert (
            await client.post("/oauth/alexa/login", data=data, cookies={"ekonex_login_csrf": csrf})
        ).status_code == 401
    limited = await client.post(
        "/oauth/alexa/login", data=data, cookies={"ekonex_login_csrf": csrf}
    )
    assert limited.status_code == 429
    assert "Troppi tentativi" in limited.text
    await client.aclose()


async def test_multi_tenant_choice_preserves_state_and_rejects_foreign_tenant(
    session: AsyncSession, seeded_domain: object
) -> None:
    tenant_a = await session.get(Tenant, seeded_domain.tenant_a_id)  # type: ignore[attr-defined]
    assert tenant_a is not None
    tenant_c = Tenant(dealer_id=tenant_a.dealer_id, name="Tenant C", slug="tenant-c")
    session.add(tenant_c)
    await session.flush()
    session.add(
        TenantMembership(
            tenant_id=tenant_c.id,
            user_id=seeded_domain.user_a_id,  # type: ignore[attr-defined]
            role=TenantRole.OWNER,
        )
    )
    await session.commit()

    client = await _client(session)
    params = _authorize_params()
    start = await client.get("/oauth/authorize", params=params)
    login_csrf = start.cookies["ekonex_login_csrf"]
    login = await client.post(
        "/oauth/alexa/login",
        data={
            **params,
            "csrf_token": login_csrf,
            "email": "owner@example.test",
            "password": "owner-password-123",
        },
        cookies={"ekonex_login_csrf": login_csrf},
    )
    portal_session = login.cookies["ekonex_portal_session"]
    choice = await client.get(
        login.headers["location"], cookies={"ekonex_portal_session": portal_session}
    )
    assert choice.status_code == 200
    assert "Tenant A" in choice.text and "Tenant C" in choice.text
    assert f'name="state" value="{params["state"]}"' in choice.text
    choice_csrf = choice.cookies["ekonex_login_csrf"]
    form = {**params, "csrf_token": choice_csrf}

    forged = await client.post(
        "/oauth/alexa/tenant",
        data={**form, "tenant_id": str(seeded_domain.tenant_b_id)},  # type: ignore[attr-defined]
        cookies={
            "ekonex_portal_session": portal_session,
            "ekonex_login_csrf": choice_csrf,
        },
    )
    assert forged.status_code == 403
    missing_csrf = await client.post(
        "/oauth/alexa/tenant",
        data={**params, "tenant_id": str(tenant_a.id)},
        cookies={"ekonex_portal_session": portal_session},
    )
    assert missing_csrf.status_code == 403

    selected = await client.post(
        "/oauth/alexa/tenant",
        data={**form, "tenant_id": str(tenant_c.id)},
        cookies={
            "ekonex_portal_session": portal_session,
            "ekonex_login_csrf": choice_csrf,
        },
    )
    assert selected.status_code == 302
    query = parse_qs(urlparse(selected.headers["location"]).query)
    assert query["state"] == [params["state"]]
    await client.aclose()
