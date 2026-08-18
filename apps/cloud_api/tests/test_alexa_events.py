"""Proactive Alexa Event Gateway authorization and delivery tests."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.alexa_events import AlexaEventGateway
from apps.cloud_api.app.config import Settings
from apps.cloud_api.app.domain.models import (
    AlexaAccountLink,
    AlexaEventAuthorization,
    Entity,
    Installation,
)


async def test_change_report_refresh_retry_and_idempotency(
    session: AsyncSession, seeded_domain: object, monkeypatch: object
) -> None:
    link = AlexaAccountLink(
        tenant_id=seeded_domain.tenant_a_id,  # type: ignore[attr-defined]
        user_id=seeded_domain.user_a_id,  # type: ignore[attr-defined]
        provider_subject="event-subject",
    )
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_registry_id = "stable-light"
    entity.state = "on"
    session.add(link)
    await session.commit()

    event_attempts: list[httpx.Request] = []
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.host == "api.amazon.com":
            token_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"lwa-access-{token_calls}",
                    "refresh_token": f"lwa-refresh-{token_calls}",
                    "expires_in": 3600,
                },
            )
        event_attempts.append(request)
        return httpx.Response(503 if len(event_attempts) < 3 else 202)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("apps.cloud_api.app.alexa_events.asyncio.sleep", no_sleep)  # type: ignore[attr-defined]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        alexa_token_encryption_key="unit-test-encryption-key",
        alexa_event_gateway_url="https://api.eu.amazonalexa.com/v3/events",
    )
    gateway = AlexaEventGateway(session, client=client, settings=settings)
    await gateway.accept_grant(link, "one-use-amazon-grant")
    stored = await session.scalar(
        select(AlexaEventAuthorization).where(AlexaEventAuthorization.link_id == link.id)
    )
    assert stored is not None
    assert b"lwa-access" not in stored.access_token_encrypted
    assert b"lwa-refresh" not in stored.refresh_token_encrypted
    assert await gateway.report_entity(entity) == 1
    assert len(event_attempts) == 3
    sent = json.loads(event_attempts[-1].content)
    assert sent["event"]["header"]["name"] == "ChangeReport"
    assert sent["event"]["payload"]["change"]["properties"]
    assert "lwa-access" not in str(sent).replace(sent["event"]["endpoint"]["scope"]["token"], "")
    assert await gateway.report_entity(entity) == 0
    assert len(event_attempts) == 3

    # Expiry forces LWA refresh before the next changed report.
    authorization = link.id
    row = await session.scalar(
        select(AlexaEventAuthorization).where(AlexaEventAuthorization.link_id == authorization)
    )
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    entity.state = "off"
    await session.commit()
    assert await gateway.report_entity(entity) == 1
    assert token_calls == 2
    await client.aclose()


async def test_no_event_authorization_means_no_advertised_delivery_target(
    session: AsyncSession, seeded_domain: object
) -> None:
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    installation = await session.get(
        Installation,
        seeded_domain.installation_a_id,  # type: ignore[attr-defined]
    )
    assert entity is not None and installation is not None
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    gateway = AlexaEventGateway(
        session,
        client=client,
        settings=Settings(alexa_token_encryption_key=str(uuid4())),
    )
    assert await gateway.report_entity(entity) == 0
    await client.aclose()
