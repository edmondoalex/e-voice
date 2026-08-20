from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.database import get_database_session
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
    await client.aclose()


async def test_invalid_redirect_is_rejected_before_login(session: AsyncSession) -> None:
    client = await _client(session)
    params = _authorize_params() | {"redirect_uri": "https://attacker.example/callback"}
    response = await client.get("/oauth/authorize", params=params)
    assert response.status_code == 400
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
