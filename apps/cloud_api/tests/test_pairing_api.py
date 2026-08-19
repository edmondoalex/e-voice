"""Authenticated pairing claim API and minimal portal tests."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.database import get_database_session
from apps.cloud_api.app.domain.models import PairingSession
from apps.cloud_api.app.main import app


async def _client(session: AsyncSession) -> httpx.AsyncClient:
    async def database_override():  # type: ignore[no-untyped-def]
        yield session

    app.dependency_overrides[get_database_session] = database_override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _headers(
    seeded_domain: object, *, readonly: bool = False, cross_tenant: bool = False
) -> dict[str, str]:
    user_id = (
        seeded_domain.user_readonly_id if readonly else seeded_domain.user_a_id  # type: ignore[attr-defined]
    )
    tenant_id = (
        seeded_domain.tenant_b_id if cross_tenant else seeded_domain.tenant_a_id  # type: ignore[attr-defined]
    )
    return {
        "X-Ekonex-User-ID": str(user_id),
        "X-Ekonex-Tenant-ID": str(tenant_id),
        "X-Ekonex-Ingress-Secret": "development-only-trusted-ingress-secret",
    }


async def _start(client: httpx.AsyncClient, suffix: str) -> dict[str, str]:
    response = await client.post(
        "/connector/v1/pairing/sessions",
        json={"installation_nonce": f"haos-portal-test-{suffix:0<16}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    return {str(key): str(value) for key, value in payload.items()}


async def test_valid_claim_hides_credential_and_poll_delivers_it_once(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    started = await _start(client, "valid")
    response = await client.post(
        "/connector/v1/pairing/claims",
        json={"code": started["code"], "installation_name": "Casa Rossi"},
        headers=_headers(seeded_domain),
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


async def test_invalid_expired_replay_and_tenant_isolation(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    invalid = await client.post(
        "/connector/v1/pairing/claims",
        json={"code": "AAAA-AAAA", "installation_name": "Invalid"},
        headers=_headers(seeded_domain),
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
        headers=_headers(seeded_domain),
    )
    assert response.status_code == 410

    replay = await _start(client, "replay")
    payload = {"code": replay["code"], "installation_name": "Once"}
    assert (
        await client.post(
            "/connector/v1/pairing/claims", json=payload, headers=_headers(seeded_domain)
        )
    ).status_code == 200
    assert (
        await client.post(
            "/connector/v1/pairing/claims", json=payload, headers=_headers(seeded_domain)
        )
    ).status_code == 400

    cross = await _start(client, "cross")
    response = await client.post(
        "/connector/v1/pairing/claims",
        json={"code": cross["code"], "installation_name": "Cross"},
        headers=_headers(seeded_domain, cross_tenant=True),
    )
    assert response.status_code == 403
    await client.aclose()


async def test_claim_rate_limit_and_readonly_role(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    for index in range(5):
        response = await client.post(
            "/connector/v1/pairing/claims",
            json={"code": f"AAAA-AAA{index + 2}", "installation_name": "Guess"},
            headers=_headers(seeded_domain),
        )
        assert response.status_code == 400
    limited = await client.post(
        "/connector/v1/pairing/claims",
        json={"code": "BBBB-BBBB", "installation_name": "Limited"},
        headers=_headers(seeded_domain),
    )
    assert limited.status_code == 429

    started = await _start(client, "readonly")
    forbidden = await client.post(
        "/connector/v1/pairing/claims",
        json={"code": started["code"], "installation_name": "Readonly"},
        headers=_headers(seeded_domain, readonly=True),
    )
    assert forbidden.status_code == 403
    await client.aclose()


async def test_pair_page_render_and_submit_success_and_error(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    headers = _headers(seeded_domain)
    page = await client.get("/pair", headers=headers)
    assert page.status_code == 200
    assert "Collega Home Assistant" in page.text
    assert "XXXX-XXXX" in page.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert csrf is not None
    cookie = page.cookies.get("ekonex_pair_csrf")
    assert cookie

    failed = await client.post(
        "/pair",
        data={"csrf_token": csrf.group(1), "code": "AAAA-AAAA", "installation_name": "Bad"},
        headers=headers,
    )
    assert failed.status_code == 400
    assert "Codice non valido o già utilizzato" in failed.text

    started = await _start(client, "portal")
    page = await client.get("/pair", headers=headers)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert csrf is not None
    succeeded = await client.post(
        "/pair",
        data={
            "csrf_token": csrf.group(1),
            "code": started["code"],
            "installation_name": "Casa Portale",
        },
        headers=headers,
    )
    assert succeeded.status_code == 200
    assert "Home Assistant collegato correttamente" in succeeded.text
    assert "evc_" not in succeeded.text
    assert started["polling_secret"] not in succeeded.text
    await client.aclose()


async def test_pair_portal_rejects_untrusted_identity_and_bad_csrf(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    assert (await client.get("/pair")).status_code == 401
    headers = _headers(seeded_domain)
    page = await client.get("/pair", headers=headers)
    response = await client.post(
        "/pair",
        data={
            "csrf_token": "forged",
            "code": "AAAA-AAAA",
            "installation_name": "Forged",
        },
        headers=headers,
    )
    assert response.status_code == 403
    assert "polling_secret" not in response.text
    assert page.status_code == 200
    await client.aclose()
