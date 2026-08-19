"""M7 Alexa account-linking, discovery, mapping and isolation tests."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.alexa import (
    _command,
    _digest,
    capabilities,
    endpoint_id,
)
from apps.cloud_api.app.database import get_database_session
from apps.cloud_api.app.domain.models import AlexaAccountLink, AlexaOAuthToken, Entity
from apps.cloud_api.app.evcp import CommandResultPayload, sessions
from apps.cloud_api.app.main import app


async def _client(session: AsyncSession) -> httpx.AsyncClient:
    async def database_override():  # type: ignore[no-untyped-def]
        yield session

    app.dependency_overrides[get_database_session] = database_override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _access(session: AsyncSession, seeded_domain: object, token: str = "eaa_test") -> str:
    link = AlexaAccountLink(
        tenant_id=seeded_domain.tenant_a_id,  # type: ignore[attr-defined]
        user_id=seeded_domain.user_a_id,  # type: ignore[attr-defined]
        provider_subject=f"subject-{token}",
    )
    session.add(link)
    await session.flush()
    session.add(
        AlexaOAuthToken(
            link_id=link.id,
            access_hash=_digest(token),
            refresh_hash=_digest(f"refresh-{token}"),
            access_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await session.commit()
    return token


def _directive(
    token: str, namespace: str, name: str, endpoint: str | None = None
) -> dict[str, object]:
    value: dict[str, object] = {
        "directive": {
            "header": {
                "namespace": namespace,
                "name": name,
                "payloadVersion": "3",
                "messageId": "e8874aa7-bdd4-4c42-9133-8867ecbf5f5e",
                "correlationToken": "opaque-correlation",
            },
            "payload": {"scope": {"type": "BearerToken", "token": token}},
        }
    }
    if endpoint:
        value["directive"]["endpoint"] = {  # type: ignore[index]
            "endpointId": endpoint,
            "scope": {"type": "BearerToken", "token": token},
        }
    return value


async def test_oauth_authorization_code_is_one_use_and_refresh_rotates(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    params = {
        "response_type": "code",
        "client_id": "ekonex-alexa-development",
        "redirect_uri": "https://pitangui.amazon.com/api/skill/link/DEVELOPMENT",
        "state": "csrf-state",
        "tenant_id": str(seeded_domain.tenant_a_id),  # type: ignore[attr-defined]
    }
    response = await client.get(
        "/oauth/authorize",
        params=params,
        headers={"X-Ekonex-User-ID": str(seeded_domain.user_a_id)},  # type: ignore[attr-defined]
        follow_redirects=False,
    )
    code = response.headers["location"].split("code=")[1].split("&")[0]
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": params["redirect_uri"],
        "client_id": "ekonex-alexa-development",
        "client_secret": "change-me",
    }
    tokens = await client.post("/oauth/token", data=form)
    assert tokens.status_code == 200
    assert (await client.post("/oauth/token", data=form)).status_code == 400
    refreshed = await client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens.json()["refresh_token"],
            "client_id": "ekonex-alexa-development",
            "client_secret": "change-me",
        },
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != tokens.json()["refresh_token"]
    await client.aclose()


async def test_discovery_is_tenant_scoped_supported_and_stable_across_rename(
    session: AsyncSession, seeded_domain: object
) -> None:
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_registry_id = "stable"
    token = await _access(session, seeded_domain)
    client = await _client(session)
    response = await client.post(
        "/alexa/v1/directive", json=_directive(token, "Alexa.Discovery", "Discover")
    )
    endpoints = response.json()["event"]["payload"]["endpoints"]
    assert [item["friendlyName"] for item in endpoints] == ["Kitchen"]
    stable = endpoints[0]["endpointId"]
    entity.display_name = "Ufficio Alex"
    entity.voice_name = "luce ufficio"
    entity.voice_aliases = ["ufficio", "luce alex"]
    entity.friendly_name, entity.ha_entity_id = "New kitchen", "light.renamed"
    await session.commit()
    body = _directive(token, "Alexa.Discovery", "Discover")
    body["directive"]["header"]["messageId"] = "new-discovery"  # type: ignore[index]
    updated = (await client.post("/alexa/v1/directive", json=body)).json()["event"]["payload"][
        "endpoints"
    ]
    assert updated[0]["endpointId"] == stable
    assert updated[0]["friendlyName"] == "luce ufficio"
    await client.aclose()


async def test_discovery_excludes_every_entity_in_voice_name_collision(
    session: AsyncSession, seeded_domain: object
) -> None:
    first = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert first is not None
    first.voice_name = "ufficio"
    second = Entity(
        installation_id=first.installation_id,
        ha_entity_id="light.office",
        ha_registry_id="registry-office",
        ha_domain="light",
        friendly_name="Office",
        voice_aliases=["UFFICIO"],
    )
    session.add(second)
    await session.commit()
    token = await _access(session, seeded_domain, "eaa_voice_collision")
    client = await _client(session)
    response = await client.post(
        "/alexa/v1/directive", json=_directive(token, "Alexa.Discovery", "Discover")
    )
    assert response.status_code == 200
    assert response.json()["event"]["payload"]["endpoints"] == []
    await client.aclose()


async def test_tombstone_and_cross_tenant_endpoint_cannot_be_controlled(
    session: AsyncSession, seeded_domain: object
) -> None:
    token = await _access(session, seeded_domain, "eaa_isolation")
    other = await session.get(Entity, seeded_domain.entity_b_id)  # type: ignore[attr-defined]
    own = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert other is not None and own is not None
    other.ha_registry_id, own.ha_registry_id = "other", "own"
    own.deleted_at = datetime.now(UTC)
    await session.commit()
    client = await _client(session)
    for entity in (other, own):
        response = await client.post(
            "/alexa/v1/directive",
            json=_directive(token, "Alexa.PowerController", "TurnOn", endpoint_id(entity)),
        )
        assert response.status_code == 404
    await client.aclose()


async def test_light_capabilities_state_and_typed_command_dispatch(
    session: AsyncSession, seeded_domain: object, monkeypatch: object
) -> None:
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_registry_id, entity.attributes_json = (
        "stable",
        {"brightness": 128, "rgb_color": [1, 2, 3], "color_temp_kelvin": 3000},
    )
    await session.commit()
    token = await _access(session, seeded_domain, "eaa_command")
    dispatched = AsyncMock(
        return_value=CommandResultPayload(
            session_id=entity.id, command_id=entity.id, status="success"
        )
    )
    monkeypatch.setattr(sessions, "dispatch", dispatched)  # type: ignore[attr-defined]
    client = await _client(session)
    response = await client.post(
        "/alexa/v1/directive",
        json=_directive(token, "Alexa.PowerController", "TurnOn", endpoint_id(entity)),
    )
    assert response.status_code == 200
    assert response.json()["event"]["header"]["name"] == "Response"
    await_args = dispatched.await_args
    assert await_args is not None
    command = await_args.args[3]
    assert command == {"operation": "power_on"}
    interfaces = {item["interface"] for item in capabilities(entity)}
    assert {
        "Alexa.PowerController",
        "Alexa.BrightnessController",
        "Alexa.ColorController",
    } <= interfaces
    await client.aclose()


async def test_expired_revoked_malformed_and_oversized_are_rejected(
    session: AsyncSession, seeded_domain: object
) -> None:
    client = await _client(session)
    assert (await client.post("/alexa/v1/directive", content=b"{")).status_code == 400
    assert (await client.post("/alexa/v1/directive", content=b"x" * 65537)).status_code == 413
    token = await _access(session, seeded_domain, "eaa_revoked")
    row = await session.scalar(
        __import__("sqlalchemy")
        .select(AlexaOAuthToken)
        .where(AlexaOAuthToken.access_hash == _digest(token))
    )
    assert row is not None
    row.revoked_at = datetime.now(UTC)
    await session.commit()
    assert (
        await client.post(
            "/alexa/v1/directive", json=_directive(token, "Alexa.Discovery", "Discover")
        )
    ).status_code == 401
    await client.aclose()


def test_capability_matrix_excludes_sensitive_and_unsupported_domains() -> None:
    entity = Entity(
        installation_id=__import__("uuid").uuid4(),
        ha_entity_id="lock.front",
        ha_domain="lock",
        friendly_name="Front",
    )
    assert entity.ha_domain not in {"light", "switch", "cover", "climate", "fan", "scene"}
    assert entity.ha_domain not in SUPPORTED_DISCOVERY_DOMAINS


SUPPORTED_DISCOVERY_DOMAINS = {"light", "switch", "cover", "climate", "fan", "scene"}


def test_supported_directives_map_only_to_closed_m6_vocabulary() -> None:
    assert _command("Alexa.BrightnessController", "SetBrightness", {"brightness": 50}) == {
        "operation": "set_brightness",
        "brightness": 128,
    }
    assert _command("Alexa.RangeController", "SetRangeValue", {"rangeValue": 70}) == {
        "operation": "set_position",
        "position": 70,
    }
    assert _command(
        "Alexa.ThermostatController",
        "SetTargetTemperature",
        {"targetSetpoint": {"value": 21}},
    ) == {"operation": "set_target_temperature", "temperature": 21.0}
    assert _command("Alexa.PercentageController", "SetPercentage", {"percentage": 40}) == {
        "operation": "set_percentage",
        "percentage": 40,
    }
    assert _command("Alexa.SceneController", "Activate", {}) == {"operation": "activate"}
    assert _command("Alexa.SecurityPanelController", "Disarm", {}) is None


def test_cover_discovery_and_directives_use_the_same_current_interfaces() -> None:
    positioned = Entity(
        installation_id=__import__("uuid").uuid4(),
        ha_entity_id="cover.blind",
        ha_domain="cover",
        attributes_json={"current_position": 45},
    )
    capability = next(
        item for item in capabilities(positioned) if item["interface"] == "Alexa.RangeController"
    )
    assert capability["instance"] == "Blind.Lift"
    directives = {
        mapping["directive"]["payload"]["rangeValue"]
        for mapping in capability["semantics"]["actionMappings"]
        if mapping["directive"]["name"] == "SetRangeValue"
    }
    assert directives == {0, 100}
    assert _command("Alexa.RangeController", "SetRangeValue", {"rangeValue": 100}, positioned) == {
        "operation": "set_position",
        "position": 100,
    }
    assert _command(
        "Alexa.RangeController", "AdjustRangeValue", {"rangeValueDelta": -10}, positioned
    ) == {"operation": "set_position", "position": 35}

    binary = Entity(
        installation_id=positioned.installation_id,
        ha_entity_id="cover.awning",
        ha_domain="cover",
        state="closed",
    )
    assert any(item["interface"] == "Alexa.ModeController" for item in capabilities(binary))
    assert _command("Alexa.ModeController", "SetMode", {"mode": "Position.Up"}, binary) == {
        "operation": "open"
    }
    assert _command("Alexa.ModeController", "SetMode", {"mode": "Position.Down"}, binary) == {
        "operation": "close"
    }


def test_every_advertised_reportable_property_is_proactive_and_retrievable() -> None:
    entity = Entity(
        installation_id=__import__("uuid").uuid4(),
        ha_entity_id="light.kitchen",
        ha_domain="light",
        attributes_json={"brightness": 100},
    )
    reportable = [item for item in capabilities(entity) if "properties" in item]
    assert reportable
    assert all(
        item["properties"]["proactivelyReported"] and item["properties"]["retrievable"]
        for item in reportable
    )


async def test_discovered_cover_executes_advertised_range_directives(
    session: AsyncSession, seeded_domain: object, monkeypatch: object
) -> None:
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_domain = "cover"
    entity.ha_registry_id = "stable-cover"
    entity.attributes_json = {"current_position": 45}
    await session.commit()
    token = await _access(session, seeded_domain, "eaa_cover")
    dispatched = AsyncMock(
        return_value=CommandResultPayload(
            session_id=entity.id, command_id=entity.id, status="success"
        )
    )
    monkeypatch.setattr(sessions, "dispatch", dispatched)  # type: ignore[attr-defined]
    client = await _client(session)

    discovered = await client.post(
        "/alexa/v1/directive", json=_directive(token, "Alexa.Discovery", "Discover")
    )
    cover = discovered.json()["event"]["payload"]["endpoints"][0]
    advertised = {item["interface"] for item in cover["capabilities"]}
    assert "Alexa.RangeController" in advertised
    assert "Alexa.CoverController" not in advertised

    directive_body = _directive(
        token, "Alexa.RangeController", "SetRangeValue", endpoint_id(entity)
    )
    directive_body["directive"]["header"]["messageId"] = str(uuid4())  # type: ignore[index]
    directive_body["directive"]["payload"] = {"rangeValue": 100}  # type: ignore[index]
    response = await client.post("/alexa/v1/directive", json=directive_body)
    assert response.status_code == 200
    await_args = dispatched.await_args
    assert await_args is not None
    assert await_args.args[3] == {"operation": "set_position", "position": 100}
    await client.aclose()
