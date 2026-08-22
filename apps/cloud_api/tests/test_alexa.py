"""M7 Alexa account-linking, discovery, mapping and isolation tests."""

import json
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.alexa import (
    _command,
    _digest,
    capabilities,
    discovery_endpoint,
    endpoint_id,
    state_properties,
)
from apps.cloud_api.app.database import get_database_session
from apps.cloud_api.app.domain.models import (
    AlexaAccountLink,
    AlexaDiscoveryDelivery,
    AlexaDiscoverySnapshot,
    AlexaOAuthToken,
    Entity,
)
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


def _property_value(entity: Entity, namespace: str, name: str) -> object | None:
    return next(
        (
            item["value"]
            for item in state_properties(entity)
            if item["namespace"] == namespace and item["name"] == name
        ),
        None,
    )


def test_light_state_properties_omit_null_brightness() -> None:
    entity = Entity(
        installation_id=uuid4(),
        ha_entity_id="light.null_brightness",
        ha_domain="light",
        attributes_json={"brightness": None},
    )

    assert _property_value(entity, "Alexa.BrightnessController", "brightness") is None


def test_light_state_properties_omit_missing_brightness() -> None:
    entity = Entity(
        installation_id=uuid4(),
        ha_entity_id="light.missing_brightness",
        ha_domain="light",
        attributes_json={},
    )

    assert _property_value(entity, "Alexa.BrightnessController", "brightness") is None


def test_light_state_properties_convert_valid_numeric_brightness() -> None:
    entity = Entity(
        installation_id=uuid4(),
        ha_entity_id="light.valid_brightness",
        ha_domain="light",
        attributes_json={"brightness": 128},
    )

    assert _property_value(entity, "Alexa.BrightnessController", "brightness") == 50


def test_state_properties_omit_other_invalid_numeric_attributes() -> None:
    entities_and_properties = [
        (
            Entity(
                installation_id=uuid4(),
                ha_entity_id="light.invalid_color",
                ha_domain="light",
                attributes_json={
                    "rgb_color": [255, None, 0],
                    "color_temp_kelvin": "unknown",
                },
            ),
            (
                ("Alexa.ColorController", "color"),
                ("Alexa.ColorTemperatureController", "colorTemperatureInKelvin"),
            ),
        ),
        (
            Entity(
                installation_id=uuid4(),
                ha_entity_id="cover.invalid_position",
                ha_domain="cover",
                supported_features=4,
                attributes_json={"current_position": None},
            ),
            (("Alexa.RangeController", "rangeValue"),),
        ),
        (
            Entity(
                installation_id=uuid4(),
                ha_entity_id="fan.invalid_percentage",
                ha_domain="fan",
                attributes_json={"percentage": "unknown"},
            ),
            (("Alexa.PercentageController", "percentage"),),
        ),
        (
            Entity(
                installation_id=uuid4(),
                ha_entity_id="climate.invalid_temperature",
                ha_domain="climate",
                attributes_json={"temperature": None},
            ),
            (("Alexa.ThermostatController", "targetSetpoint"),),
        ),
    ]

    for entity, properties in entities_and_properties:
        for namespace, name in properties:
            assert _property_value(entity, namespace, name) is None


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("open", "position.open"),
        ("closed", "position.closed"),
        ("unknown", None),
        ("opening", None),
        ("closing", None),
        ("unavailable", None),
        (None, None),
    ],
)
def test_discrete_cover_reports_only_determinable_mode(
    state: str | None, expected: str | None
) -> None:
    entity = Entity(
        installation_id=uuid4(),
        ha_entity_id="cover.discrete_state",
        ha_domain="cover",
        supported_features=3,
        alexa_cover_mode="discrete",
        state=state,
    )

    modes = [
        item
        for item in state_properties(entity)
        if item["namespace"] == "Alexa.ModeController" and item["name"] == "mode"
    ]

    assert [item["value"] for item in modes] == ([] if expected is None else [expected])


def test_unknown_assumed_state_cover_omits_mode_property() -> None:
    entity = Entity(
        installation_id=uuid4(),
        ha_entity_id="cover.assumed_state",
        ha_domain="cover",
        supported_features=11,
        alexa_cover_mode="discrete",
        state="unknown",
        attributes_json={"assumed_state": True, "is_closed": None},
    )

    assert not any(
        item["namespace"] == "Alexa.ModeController" and item["name"] == "mode"
        for item in state_properties(entity)
    )
    assert not any(
        item["namespace"] == "Alexa.PowerController" for item in state_properties(entity)
    )


@pytest.mark.parametrize(
    ("device_class", "category"),
    [
        ("blind", "INTERIOR_BLIND"),
        ("shade", "INTERIOR_BLIND"),
        ("curtain", "INTERIOR_BLIND"),
        ("window", "EXTERIOR_BLIND"),
        ("awning", "EXTERIOR_BLIND"),
        ("shutter", "EXTERIOR_BLIND"),
        ("door", "DOOR"),
        (None, "OTHER"),
    ],
)
def test_cover_display_category_matches_home_assistant(
    device_class: str | None, category: str
) -> None:
    entity = Entity(
        id=uuid4(),
        installation_id=uuid4(),
        ha_entity_id="cover.category",
        ha_domain="cover",
        supported_features=11,
        alexa_cover_mode="discrete",
        attributes_json={"device_class": device_class} if device_class else {},
    )

    assert discovery_endpoint(entity)["displayCategories"] == [category]


async def test_report_state_response_omits_null_brightness(
    session: AsyncSession, seeded_domain: object
) -> None:
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_domain = "light"
    entity.ha_registry_id = "stable-null-brightness"
    entity.state = "on"
    entity.attributes_json = {"brightness": None}
    await session.commit()
    token = await _access(session, seeded_domain, "eaa_null_brightness")
    client = await _client(session)

    response = await client.post(
        "/alexa/v1/directive",
        json=_directive(token, "Alexa", "ReportState", endpoint_id(entity)),
    )

    assert response.status_code == 200
    properties = response.json()["context"]["properties"]
    assert all(item["namespace"] != "Alexa.BrightnessController" for item in properties)
    await client.aclose()


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
    snapshot = await session.scalar(
        select(AlexaDiscoverySnapshot).where(
            AlexaDiscoverySnapshot.installation_id == seeded_domain.installation_a_id  # type: ignore[attr-defined]
        )
    )
    assert snapshot is not None
    assert snapshot.endpoint_count == 1
    assert snapshot.endpoints_json == [
        {"endpoint_id": stable, "voice_name": "Kitchen", "domain": "light"}
    ]
    assert snapshot.changes_json[0]["change"] == "new"
    delivery = await session.scalar(
        select(AlexaDiscoveryDelivery).where(AlexaDiscoveryDelivery.alexa_endpoint_id == stable)
    )
    assert delivery is not None
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
    await session.refresh(snapshot)
    assert snapshot.changes_json == [
        {
            "endpoint_id": stable,
            "voice_name": "luce ufficio",
            "domain": "light",
            "change": "renamed",
            "previous_voice_name": "Kitchen",
        }
    ]
    persisted = json.dumps({"endpoints": snapshot.endpoints_json, "changes": snapshot.changes_json})
    assert token not in persisted
    assert token not in delivery.representation_fingerprint
    assert (
        await session.scalar(
            select(AlexaDiscoverySnapshot).where(
                AlexaDiscoverySnapshot.tenant_id == seeded_domain.tenant_b_id  # type: ignore[attr-defined]
            )
        )
        is None
    )
    entity.deleted_at = datetime.now(UTC)
    await session.commit()
    removed_body = _directive(token, "Alexa.Discovery", "Discover")
    removed_body["directive"]["header"]["messageId"] = "removed-discovery"  # type: ignore[index]
    removed_response = await client.post("/alexa/v1/directive", json=removed_body)
    assert removed_response.json()["event"]["payload"]["endpoints"] == []
    await session.refresh(snapshot)
    assert snapshot.endpoint_count == 0
    assert snapshot.changes_json == [
        {
            "endpoint_id": stable,
            "voice_name": "luce ufficio",
            "domain": "light",
            "change": "removed",
        }
    ]
    await session.refresh(delivery)
    assert delivery.removed_at is not None
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
    invalid = await client.post(
        "/alexa/v1/directive",
        json=_directive("eaa_invalid", "Alexa.Discovery", "Discover"),
    )
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "INVALID_AUTHORIZATION_CREDENTIAL"
    token = await _access(session, seeded_domain, "eaa_revoked")
    row = await session.scalar(
        __import__("sqlalchemy")
        .select(AlexaOAuthToken)
        .where(AlexaOAuthToken.access_hash == _digest(token))
    )
    assert row is not None
    row.access_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    expired = await client.post(
        "/alexa/v1/directive", json=_directive(token, "Alexa.Discovery", "Discover")
    )
    assert expired.status_code == 401
    assert expired.json()["detail"] == "EXPIRED_AUTHORIZATION_CREDENTIAL"
    row.revoked_at = datetime.now(UTC)
    await session.commit()
    revoked = await client.post(
        "/alexa/v1/directive", json=_directive(token, "Alexa.Discovery", "Discover")
    )
    assert revoked.status_code == 401
    assert revoked.json()["detail"] == "INVALID_AUTHORIZATION_CREDENTIAL"
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
    cover = Entity(
        installation_id=uuid4(),
        ha_entity_id="cover.positioned",
        ha_domain="cover",
        supported_features=4,
    )
    assert _command("Alexa.RangeController", "SetRangeValue", {"rangeValue": 70}, cover) == {
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
        supported_features=15,
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
        supported_features=3,
        state="closed",
    )
    assert any(item["interface"] == "Alexa.ModeController" for item in capabilities(binary))
    assert _command("Alexa.ModeController", "SetMode", {"mode": "position.open"}, binary) == {
        "operation": "open"
    }
    assert _command("Alexa.ModeController", "SetMode", {"mode": "position.closed"}, binary) == {
        "operation": "close"
    }
    assert _command("Alexa.PowerController", "TurnOn", {}, binary) == {"operation": "open"}
    assert _command("Alexa.PowerController", "TurnOff", {}, binary) == {"operation": "close"}


def test_cover_modes_are_feature_safe_stable_and_support_expected_directives() -> None:
    entity = Entity(
        id=uuid4(),
        installation_id=uuid4(),
        ha_entity_id="cover.office",
        ha_registry_id="stable-office-cover",
        ha_domain="cover",
        supported_features=15,
        state="open",
        attributes_json={"current_position": 45},
    )
    stable = endpoint_id(entity)

    entity.alexa_cover_mode = "discrete"
    discrete = discovery_endpoint(entity)
    discrete_interfaces = {item["interface"] for item in discrete["capabilities"]}
    assert "Alexa.ModeController" in discrete_interfaces
    assert "Alexa.PowerController" in discrete_interfaces
    assert "Alexa.RangeController" not in discrete_interfaces
    mode = next(
        item for item in discrete["capabilities"] if item["interface"] == "Alexa.ModeController"
    )
    assert {item["value"] for item in mode["configuration"]["supportedModes"]} == {
        "position.open",
        "position.closed",
        "position.custom",
    }
    assert _command("Alexa.ModeController", "SetMode", {"mode": "Position.Stopped"}, entity) is None
    playback = next(
        item for item in discrete["capabilities"] if item["interface"] == "Alexa.PlaybackController"
    )
    assert playback == {
        "type": "AlexaInterface",
        "interface": "Alexa.PlaybackController",
        "version": "3",
        "instance": "cover.stop",
        "supportedOperations": ["Stop"],
    }
    assert _command("Alexa.PlaybackController", "Pause", {}, entity) == {"operation": "stop"}
    assert _command("Alexa.PlaybackController", "Stop", {}, entity) == {"operation": "stop"}
    assert _command("Alexa.ModeController", "SetMode", {"mode": "position.custom"}, entity) == {
        "operation": "stop"
    }
    assert _command("Alexa.ModeController", "SetMode", {"mode": "position.open"}, entity) == {
        "operation": "open"
    }
    assert _command("Alexa.ModeController", "SetMode", {"mode": "position.closed"}, entity) == {
        "operation": "close"
    }

    entity.alexa_cover_mode = "percentage"
    percentage = discovery_endpoint(entity)
    percentage_interfaces = {item["interface"] for item in percentage["capabilities"]}
    assert "Alexa.RangeController" in percentage_interfaces
    assert "Alexa.ModeController" not in percentage_interfaces
    assert "Alexa.PowerController" not in percentage_interfaces

    entity.alexa_cover_mode = "hybrid"
    hybrid = discovery_endpoint(entity)
    hybrid_controllers = [
        item
        for item in hybrid["capabilities"]
        if item["interface"] in {"Alexa.ModeController", "Alexa.RangeController"}
    ]
    assert {item["interface"] for item in hybrid_controllers} == {
        "Alexa.ModeController",
        "Alexa.RangeController",
    }
    assert not any(item["interface"] == "Alexa.PowerController" for item in hybrid["capabilities"])
    assert "semantics" not in next(
        item for item in hybrid_controllers if item["interface"] == "Alexa.RangeController"
    )
    assert endpoint_id(entity) == stable
    assert discrete != percentage != hybrid


def test_cover_mode_does_not_advertise_unsupported_stop_or_position() -> None:
    entity = Entity(
        installation_id=uuid4(),
        ha_entity_id="cover.basic",
        ha_domain="cover",
        supported_features=3,
        alexa_cover_mode="discrete",
    )
    capability = next(
        item for item in capabilities(entity) if item["interface"] == "Alexa.ModeController"
    )
    modes = {item["value"] for item in capability["configuration"]["supportedModes"]}
    assert "Position.Stopped" not in modes
    assert _command("Alexa.ModeController", "SetMode", {"mode": "Position.Stopped"}, entity) is None
    assert _command("Alexa.RangeController", "SetRangeValue", {"rangeValue": 50}, entity) is None


@pytest.mark.parametrize("state", ["unknown", "open", "closed", "opening", "closing"])
@pytest.mark.parametrize(
    ("namespace", "name", "payload", "operation"),
    [
        ("Alexa.ModeController", "SetMode", {"mode": "position.open"}, "open"),
        ("Alexa.PowerController", "TurnOn", {}, "open"),
        ("Alexa.PlaybackController", "Pause", {}, "stop"),
        ("Alexa.PlaybackController", "Stop", {}, "stop"),
        ("Alexa.ModeController", "SetMode", {"mode": "position.custom"}, "stop"),
        ("Alexa.ModeController", "SetMode", {"mode": "position.closed"}, "close"),
        ("Alexa.PowerController", "TurnOff", {}, "close"),
    ],
)
def test_assumed_state_discrete_cover_commands_are_stateless(
    state: str, namespace: str, name: str, payload: dict[str, object], operation: str
) -> None:
    entity = Entity(
        installation_id=uuid4(),
        ha_entity_id="cover.assumed_commands",
        ha_domain="cover",
        supported_features=11,
        alexa_cover_mode="discrete",
        state=state,
        attributes_json={"assumed_state": True, "is_closed": None},
    )

    assert _command(namespace, name, payload, entity) == {"operation": operation}


def test_cover_auto_default_is_derived_conservatively_from_real_features() -> None:
    discrete = Entity(
        installation_id=uuid4(), ha_entity_id="cover.d", ha_domain="cover", supported_features=3
    )
    percentage = Entity(
        installation_id=uuid4(), ha_entity_id="cover.p", ha_domain="cover", supported_features=4
    )
    hybrid = Entity(
        installation_id=uuid4(), ha_entity_id="cover.h", ha_domain="cover", supported_features=7
    )
    assert {item["interface"] for item in capabilities(discrete)} >= {"Alexa.ModeController"}
    assert {item["interface"] for item in capabilities(percentage)} >= {"Alexa.RangeController"}
    interfaces = {item["interface"] for item in capabilities(hybrid)}
    assert "Alexa.RangeController" in interfaces
    assert "Alexa.ModeController" not in interfaces


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
    entity.supported_features = 7
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

    unsupported = _directive(token, "Alexa.ModeController", "SetMode", endpoint_id(entity))
    unsupported["directive"]["header"]["messageId"] = str(uuid4())  # type: ignore[index]
    unsupported["directive"]["payload"] = {"mode": "Position.Stopped"}  # type: ignore[index]
    invalid = await client.post("/alexa/v1/directive", json=unsupported)
    assert invalid.status_code == 200
    assert invalid.json()["event"]["header"]["name"] == "ErrorResponse"
    assert invalid.json()["event"]["payload"]["type"] == "INVALID_DIRECTIVE"
    assert dispatched.await_count == 1
    await client.aclose()


async def test_cover_directive_logging_is_allowlisted(
    session: AsyncSession,
    seeded_domain: object,
    monkeypatch: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_domain = "cover"
    entity.ha_registry_id = "stable-cover-log"
    entity.supported_features = 11
    entity.alexa_cover_mode = "discrete"
    await session.commit()
    token = await _access(session, seeded_domain, "never-log-access-token")
    monkeypatch.setattr(  # type: ignore[attr-defined]
        sessions,
        "dispatch",
        AsyncMock(
            return_value=CommandResultPayload(
                session_id=entity.id, command_id=entity.id, status="success"
            )
        ),
    )
    client = await _client(session)
    body = _directive(token, "Alexa.ModeController", "SetMode", endpoint_id(entity))
    body["directive"]["header"]["instance"] = "cover.position"  # type: ignore[index]
    body["directive"]["header"]["messageId"] = str(uuid4())  # type: ignore[index]
    body["directive"]["payload"] = {  # type: ignore[index]
        "mode": "position.open",
        "scope": {"type": "BearerToken", "token": "never-log-payload-token"},
        "private_value": "never-log-private-payload",
    }
    caplog.set_level(logging.INFO, logger="apps.cloud_api.app.alexa")

    response = await client.post("/alexa/v1/directive", json=body)

    assert response.status_code == 200
    alexa_messages = [
        record.message for record in caplog.records if record.name == "apps.cloud_api.app.alexa"
    ]
    record = next(message for message in alexa_messages if "alexa_directive_received" in message)
    assert "Alexa.ModeController" in record
    assert "SetMode" in record
    assert "cover.position" in record
    assert "position.open" in record
    assert endpoint_id(entity) in record
    alexa_log = "\n".join(alexa_messages)
    assert "never-log-access-token" not in alexa_log
    assert "never-log-payload-token" not in alexa_log
    assert "never-log-private-payload" not in alexa_log
    assert "BearerToken" not in alexa_log
    await client.aclose()


async def test_discover_response_uses_canonical_discrete_blinds_json(
    session: AsyncSession, seeded_domain: object
) -> None:
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_domain = "cover"
    entity.ha_registry_id = "stable-discrete-cover"
    entity.supported_features = 15
    entity.alexa_cover_mode = "discrete"
    entity.attributes_json = {"current_position": 45, "device_class": "blind"}
    await session.commit()
    token = await _access(session, seeded_domain, "eaa_discrete_cover_json")
    client = await _client(session)

    response = await client.post(
        "/alexa/v1/directive", json=_directive(token, "Alexa.Discovery", "Discover")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["event"]["header"]["name"] == "Discover.Response"
    endpoint = body["event"]["payload"]["endpoints"][0]
    assert endpoint["endpointId"] == endpoint_id(entity)
    assert endpoint["displayCategories"] == ["INTERIOR_BLIND"]
    power = next(
        capability
        for capability in endpoint["capabilities"]
        if capability["interface"] == "Alexa.PowerController"
    )
    assert power["properties"] == {
        "supported": [{"name": "powerState"}],
        "proactivelyReported": True,
        "retrievable": True,
    }
    controllers = [
        capability
        for capability in endpoint["capabilities"]
        if capability["interface"]
        in {"Alexa.ModeController", "Alexa.RangeController", "Alexa.ToggleController"}
    ]
    assert [controller["interface"] for controller in controllers] == ["Alexa.ModeController"]
    controller = controllers[0]
    assert controller["instance"] == "cover.position"
    assert controller["capabilityResources"] == {
        "friendlyNames": [
            {"@type": "text", "value": {"text": "Position", "locale": "en-US"}},
            {"@type": "asset", "value": {"assetId": "Alexa.Setting.Opening"}},
        ]
    }
    assert controller["configuration"] == {
        "ordered": False,
        "supportedModes": [
            {
                "value": "position.open",
                "modeResources": {
                    "friendlyNames": [
                        {"@type": "asset", "value": {"assetId": "Alexa.Value.Open"}},
                    ]
                },
            },
            {
                "value": "position.closed",
                "modeResources": {
                    "friendlyNames": [
                        {"@type": "asset", "value": {"assetId": "Alexa.Value.Close"}},
                    ]
                },
            },
            {
                "value": "position.custom",
                "modeResources": {
                    "friendlyNames": [
                        {"@type": "text", "value": {"text": "Custom", "locale": "en-US"}},
                        {"@type": "asset", "value": {"assetId": "Alexa.Setting.Preset"}},
                    ]
                },
            },
        ],
    }
    playback = next(
        capability
        for capability in endpoint["capabilities"]
        if capability["interface"] == "Alexa.PlaybackController"
    )
    assert playback == {
        "type": "AlexaInterface",
        "interface": "Alexa.PlaybackController",
        "version": "3",
        "instance": "cover.stop",
        "supportedOperations": ["Stop"],
    }
    assert controller["semantics"]["actionMappings"] == [
        {
            "@type": "ActionsToDirective",
            "actions": ["Alexa.Actions.Lower", "Alexa.Actions.Close"],
            "directive": {"name": "SetMode", "payload": {"mode": "position.closed"}},
        },
        {
            "@type": "ActionsToDirective",
            "actions": ["Alexa.Actions.Raise", "Alexa.Actions.Open"],
            "directive": {"name": "SetMode", "payload": {"mode": "position.open"}},
        },
    ]
    assert controller["semantics"]["stateMappings"] == [
        {
            "@type": "StatesToValue",
            "states": ["Alexa.States.Closed"],
            "value": "position.closed",
        },
        {
            "@type": "StatesToValue",
            "states": ["Alexa.States.Open"],
            "value": "position.open",
        },
    ]
    await client.aclose()


async def test_discover_response_omits_stop_mode_without_stop_feature(
    session: AsyncSession, seeded_domain: object
) -> None:
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_domain = "cover"
    entity.ha_registry_id = "stable-discrete-cover-without-stop"
    entity.supported_features = 3
    entity.alexa_cover_mode = "discrete"
    await session.commit()
    token = await _access(session, seeded_domain, "eaa_discrete_cover_without_stop")
    client = await _client(session)

    response = await client.post(
        "/alexa/v1/directive", json=_directive(token, "Alexa.Discovery", "Discover")
    )

    assert response.status_code == 200
    endpoint = response.json()["event"]["payload"]["endpoints"][0]
    controller = next(
        capability
        for capability in endpoint["capabilities"]
        if capability["interface"] == "Alexa.ModeController"
    )
    assert [mode["value"] for mode in controller["configuration"]["supportedModes"]] == [
        "position.open",
        "position.closed",
    ]
    assert all(
        capability["interface"] != "Alexa.PlaybackController"
        for capability in endpoint["capabilities"]
    )
    await client.aclose()


async def test_assumed_state_cover_dispatches_mode_and_playback_directives(
    session: AsyncSession, seeded_domain: object, monkeypatch: object
) -> None:
    entity = await session.get(Entity, seeded_domain.entity_a_id)  # type: ignore[attr-defined]
    assert entity is not None
    entity.ha_domain = "cover"
    entity.ha_registry_id = "stable-assumed-cover-directives"
    entity.supported_features = 11
    entity.alexa_cover_mode = "discrete"
    entity.state = "unknown"
    entity.attributes_json = {"assumed_state": True, "is_closed": None}
    await session.commit()
    token = await _access(session, seeded_domain, "eaa_assumed_cover_directives")
    dispatched = AsyncMock(
        return_value=CommandResultPayload(
            session_id=entity.id, command_id=entity.id, status="success"
        )
    )
    monkeypatch.setattr(sessions, "dispatch", dispatched)  # type: ignore[attr-defined]
    client = await _client(session)

    directives = [
        ("Alexa.ModeController", "SetMode", "cover.position", {"mode": "position.open"}),
        ("Alexa.PlaybackController", "Pause", "cover.stop", {}),
        ("Alexa.PlaybackController", "Stop", "cover.stop", {}),
        ("Alexa.ModeController", "SetMode", "cover.position", {"mode": "position.closed"}),
    ]
    for namespace, name, instance, payload in directives:
        body = _directive(token, namespace, name, endpoint_id(entity))
        body["directive"]["header"]["instance"] = instance  # type: ignore[index]
        body["directive"]["header"]["messageId"] = str(uuid4())  # type: ignore[index]
        body["directive"]["payload"] = payload  # type: ignore[index]
        response = await client.post("/alexa/v1/directive", json=body)
        assert response.status_code == 200
        assert response.json()["event"]["header"]["name"] == "Response"

    assert [call.args[3] for call in dispatched.await_args_list] == [
        {"operation": "open"},
        {"operation": "stop"},
        {"operation": "stop"},
        {"operation": "close"},
    ]
    assert not any(
        item["namespace"] == "Alexa.ModeController" and item["name"] == "mode"
        for item in state_properties(entity)
    )
    await client.aclose()
