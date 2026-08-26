"""Proactive Alexa Event Gateway authorization and delivery tests."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.alexa_events import AlexaEventGateway
from apps.cloud_api.app.config import Settings
from apps.cloud_api.app.domain.models import (
    AlexaAccountLink,
    AlexaDiscoveryDelivery,
    AlexaEventAuthorization,
    AuditEvent,
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
    entity.attributes_json = {"brightness": None}
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
    assert all(
        item["namespace"] != "Alexa.BrightnessController"
        for item in sent["event"]["payload"]["change"]["properties"]
    )
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
    assert await gateway.reconcile_discovery(installation) == 0
    await client.aclose()


async def test_proactive_discovery_add_rename_irrelevant_change_and_delete(
    session: AsyncSession, seeded_domain: object
) -> None:
    link = AlexaAccountLink(
        tenant_id=seeded_domain.tenant_a_id,  # type: ignore[attr-defined]
        user_id=seeded_domain.user_a_id,  # type: ignore[attr-defined]
        provider_subject="proactive-discovery-subject",
    )
    installation = await session.get(
        Installation,
        seeded_domain.installation_a_id,  # type: ignore[attr-defined]
    )
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert installation is not None and entity is not None
    session.add(link)
    await session.commit()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "amazon-access-secret",
                    "refresh_token": "amazon-refresh-secret",
                    "expires_in": 3600,
                },
            )
        requests.append(request)
        return httpx.Response(
            202,
            json={"accepted": True, "credentials": {"token": "amazon-access-secret"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = AlexaEventGateway(
        session,
        client=client,
        settings=Settings(alexa_token_encryption_key="proactive-test-encryption-key"),
    )
    await gateway.accept_grant(link, "one-use-grant")

    assert await gateway.reconcile_discovery(installation) == 1
    added = json.loads(requests[-1].content)
    assert added["event"]["header"]["name"] == "AddOrUpdateReport"
    assert added["event"]["payload"]["endpoints"][0]["friendlyName"] == "Kitchen"
    assert "Private" not in str(added)
    endpoint_value = added["event"]["payload"]["endpoints"][0]["endpointId"]
    assert await gateway.reconcile_discovery(installation) == 0
    assert len(requests) == 1

    assert await gateway.reconcile_discovery(installation, force=True) == 1
    forced = json.loads(requests[-1].content)
    assert forced["event"]["header"]["name"] == "AddOrUpdateReport"
    assert forced["event"]["payload"]["endpoints"][0]["endpointId"] == endpoint_value
    assert len(requests) == 2
    assert requests[-1].headers["content-type"] == "application/json"
    diagnostic = (
        await session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "alexa.event_gateway.add_or_update")
        )
    ).all()[-1]
    diagnostic_payload = diagnostic.payload_redacted_json
    message_id = forced["event"]["header"]["messageId"]
    assert diagnostic.source == "alexa_event_gateway"
    assert diagnostic.result == "success"
    assert diagnostic.request_id == message_id
    assert diagnostic_payload["correlation_id"] == message_id
    assert diagnostic_payload["message_id"] == message_id
    assert diagnostic_payload["endpoint_id"] == endpoint_value
    assert diagnostic_payload["endpoint_ids"] == [endpoint_value]
    assert diagnostic_payload["request_payload"]["event"]["payload"]["scope"]["token"] == (
        "[REDACTED]"
    )
    assert (
        diagnostic_payload["request_payload"]["event"]["payload"]["endpoints"]
        == (forced["event"]["payload"]["endpoints"])
    )
    assert diagnostic_payload["http_status"] == 202
    assert diagnostic_payload["response_body"] == {
        "accepted": True,
        "credentials": {"token": "[REDACTED]"},
    }
    assert diagnostic_payload["error"] is None
    assert "amazon-access-secret" not in json.dumps(diagnostic_payload)

    entity.state = "on"
    await session.commit()
    assert await gateway.reconcile_discovery(installation) == 0
    assert len(requests) == 2

    entity.voice_name = "luce cucina nuova"
    await session.commit()
    assert await gateway.reconcile_discovery(installation) == 1
    renamed = json.loads(requests[-1].content)
    assert renamed["event"]["header"]["name"] == "AddOrUpdateReport"
    assert renamed["event"]["payload"]["endpoints"][0]["friendlyName"] == "luce cucina nuova"

    entity.ha_domain = "cover"
    entity.supported_features = 3
    entity.attributes_json = {"current_position": 45}
    entity.alexa_cover_mode = "discrete"
    await session.commit()
    assert await gateway.reconcile_discovery(installation) == 1
    discrete = json.loads(requests[-1].content)["event"]["payload"]["endpoints"][0]
    assert discrete["endpointId"] == endpoint_value
    assert "Alexa.ModeController" in {item["interface"] for item in discrete["capabilities"]}
    assert "Position.Stopped" not in json.dumps(discrete)
    discrete_mode = next(
        item for item in discrete["capabilities"] if item["interface"] == "Alexa.ModeController"
    )
    discrete_actions = [
        action
        for mapping in discrete_mode["semantics"]["actionMappings"]
        for action in mapping["actions"]
    ]
    assert discrete_actions.count("Alexa.Actions.Open") == 1
    assert discrete_actions.count("Alexa.Actions.Close") == 1

    entity.alexa_cover_mode = "hybrid"
    entity.supported_features = 7
    await session.commit()
    assert await gateway.reconcile_discovery(installation) == 1
    hybrid = json.loads(requests[-1].content)["event"]["payload"]["endpoints"][0]
    assert hybrid["endpointId"] == endpoint_value
    assert {"Alexa.ModeController", "Alexa.RangeController"} <= {
        item["interface"] for item in hybrid["capabilities"]
    }

    entity.deleted_at = datetime.now(UTC)
    await session.commit()
    assert await gateway.reconcile_discovery(installation) == 1
    deleted = json.loads(requests[-1].content)
    assert deleted["event"]["header"]["name"] == "DeleteReport"
    assert deleted["event"]["payload"]["endpoints"] == [{"endpointId": endpoint_value}]
    assert "amazon-access-secret" not in str(deleted).replace(
        deleted["event"]["payload"]["scope"]["token"], ""
    )
    delivery = (await session.scalars(select(AlexaDiscoveryDelivery))).one()
    assert delivery.removed_at is not None
    audits = list(
        (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.source == "alexa_event_gateway")
            )
        ).all()
    )
    reports = [event for event in audits if event.event_type.startswith("alexa.discovery.")]
    diagnostics = [
        event for event in audits if event.event_type == "alexa.event_gateway.add_or_update"
    ]
    assert [event.result for event in reports] == ["success"] * 6
    assert [event.result for event in diagnostics] == ["success"] * 5
    assert all("token" not in json.dumps(event.payload_redacted_json) for event in reports)
    assert all(
        "amazon-access-secret" not in json.dumps(event.payload_redacted_json)
        for event in diagnostics
    )
    await client.aclose()


async def test_proactive_discovery_gateway_error_is_secret_free_and_retryable(
    session: AsyncSession,
    seeded_domain: object,
    monkeypatch: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    link = AlexaAccountLink(
        tenant_id=seeded_domain.tenant_a_id,  # type: ignore[attr-defined]
        user_id=seeded_domain.user_a_id,  # type: ignore[attr-defined]
        provider_subject="gateway-error-subject",
    )
    installation = await session.get(
        Installation,
        seeded_domain.installation_a_id,  # type: ignore[attr-defined]
    )
    assert installation is not None
    session.add(link)
    await session.commit()
    event_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal event_attempts
        if request.url.host == "api.amazon.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "never-log-amazon-access",
                    "refresh_token": "never-log-amazon-refresh",
                    "expires_in": 3600,
                },
            )
        event_attempts += 1
        raise httpx.ConnectError("gateway unavailable", request=request)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("apps.cloud_api.app.alexa_events.asyncio.sleep", no_sleep)  # type: ignore[attr-defined]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = AlexaEventGateway(
        session,
        client=client,
        settings=Settings(alexa_token_encryption_key="gateway-error-encryption-key"),
    )
    await gateway.accept_grant(link, "one-use-grant")
    assert await gateway.reconcile_discovery(installation) == 0
    assert event_attempts == 3
    assert (await session.scalars(select(AlexaDiscoveryDelivery))).all() == []
    diagnostic = await session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "alexa.event_gateway.add_or_update")
    )
    assert diagnostic is not None
    assert diagnostic.result == "error"
    assert diagnostic.payload_redacted_json["http_status"] is None
    assert diagnostic.payload_redacted_json["response_body"] is None
    assert diagnostic.payload_redacted_json["error_type"] == "ConnectError"
    assert diagnostic.payload_redacted_json["error"] == "gateway unavailable"
    assert diagnostic.payload_redacted_json["attempts"] == 3
    assert (
        diagnostic.payload_redacted_json["request_payload"]["event"]["payload"]["scope"]["token"]
        == "[REDACTED]"
    )
    assert "never-log-amazon" not in json.dumps(diagnostic.payload_redacted_json)
    assert "never-log-amazon" not in caplog.text
    await client.aclose()
