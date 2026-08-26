from uuid import uuid4

import pytest

from apps.cloud_api.app.alexa import _command, discovery_endpoint, state_properties
from apps.cloud_api.app.alexa_device_types import (
    allowed_alexa_device_types,
    validate_alexa_device_type,
)
from apps.cloud_api.app.domain.models import Entity


def _switch(*, alexa_device_type: str | None = None, state: str = "off") -> Entity:
    return Entity(
        id=uuid4(),
        installation_id=uuid4(),
        ha_entity_id="switch.paperino",
        ha_domain="switch",
        friendly_name="Paperino",
        voice_aliases=[],
        alexa_device_type=alexa_device_type,
        supported_features=0,
        state=state,
        attributes_json={},
        available=True,
    )


def test_switch_device_types_are_bounded_by_domain() -> None:
    entity = _switch()
    assert allowed_alexa_device_types(entity) == ("switch", "light", "outlet", "gate")
    assert validate_alexa_device_type(entity, "gate") == "gate"

    entity.ha_domain = "climate"
    with pytest.raises(ValueError):
        validate_alexa_device_type(entity, "gate")


def test_gate_override_discovers_amazon_toggle_open_close_contract() -> None:
    entity = _switch(alexa_device_type="gate")
    endpoint = discovery_endpoint(entity)

    assert endpoint["displayCategories"] == ["OTHER"]
    interfaces = [item["interface"] for item in endpoint["capabilities"]]
    assert "Alexa.ToggleController" in interfaces
    assert "Alexa.ModeController" not in interfaces
    assert "Alexa.PowerController" not in interfaces

    toggle = next(
        item for item in endpoint["capabilities"] if item["interface"] == "Alexa.ToggleController"
    )
    assert toggle == {
        "type": "AlexaInterface",
        "interface": "Alexa.ToggleController",
        "version": "3",
        "properties": {
            "supported": [{"name": "toggleState"}],
            "proactivelyReported": True,
            "retrievable": True,
        },
        "instance": "Gate.Opening",
        "capabilityResources": {
            "friendlyNames": [
                {"@type": "asset", "value": {"assetId": "Alexa.Setting.Opening"}},
                {"@type": "text", "value": {"text": "Cancello", "locale": "it-IT"}},
            ]
        },
        "semantics": {
            "actionMappings": [
                {
                    "@type": "ActionsToDirective",
                    "actions": ["Alexa.Actions.Close"],
                    "directive": {"name": "TurnOff", "payload": {}},
                },
                {
                    "@type": "ActionsToDirective",
                    "actions": ["Alexa.Actions.Open"],
                    "directive": {"name": "TurnOn", "payload": {}},
                },
            ],
            "stateMappings": [
                {
                    "@type": "StatesToValue",
                    "states": ["Alexa.States.Closed"],
                    "value": "OFF",
                },
                {
                    "@type": "StatesToValue",
                    "states": ["Alexa.States.Open"],
                    "value": "ON",
                },
            ],
        },
    }
    actions = {
        action for mapping in toggle["semantics"]["actionMappings"] for action in mapping["actions"]
    }
    assert {"Alexa.Actions.Open", "Alexa.Actions.Close"} <= actions


def test_gate_open_close_maps_to_switch_power() -> None:
    entity = _switch(alexa_device_type="gate")
    assert _command("Alexa.ToggleController", "TurnOn", {}, entity) == {"operation": "power_on"}
    assert _command("Alexa.ToggleController", "TurnOff", {}, entity) == {"operation": "power_off"}


def test_gate_state_reports_open_closed_toggle() -> None:
    entity = _switch(alexa_device_type="gate", state="off")
    props = state_properties(entity)
    toggle = next(item for item in props if item["namespace"] == "Alexa.ToggleController")
    assert toggle["instance"] == "Gate.Opening"
    assert toggle["value"] == "OFF"

    entity.state = "on"
    props = state_properties(entity)
    toggle = next(item for item in props if item["namespace"] == "Alexa.ToggleController")
    assert toggle["value"] == "ON"


def test_visual_switch_overrides_keep_power_controller() -> None:
    for device_type, category in (
        ("switch", "SWITCH"),
        ("light", "LIGHT"),
        ("outlet", "SMARTPLUG"),
    ):
        endpoint = discovery_endpoint(_switch(alexa_device_type=device_type))
        assert endpoint["displayCategories"] == [category]
        assert "Alexa.PowerController" in [item["interface"] for item in endpoint["capabilities"]]


def test_gate_contract_does_not_change_standard_switch_light_outlet_or_cover() -> None:
    normal_switch = discovery_endpoint(_switch())
    assert normal_switch["displayCategories"] == ["SWITCH"]
    assert "Alexa.PowerController" in [item["interface"] for item in normal_switch["capabilities"]]
    assert "Alexa.ModeController" not in [
        item["interface"] for item in normal_switch["capabilities"]
    ]

    light = _switch()
    light.ha_domain = "light"
    assert discovery_endpoint(light)["displayCategories"] == ["LIGHT"]
    assert "Alexa.PowerController" in [
        item["interface"] for item in discovery_endpoint(light)["capabilities"]
    ]

    outlet = discovery_endpoint(_switch(alexa_device_type="outlet"))
    assert outlet["displayCategories"] == ["SMARTPLUG"]
    assert "Alexa.PowerController" in [item["interface"] for item in outlet["capabilities"]]

    cover = _switch()
    cover.ha_entity_id = "cover.standard"
    cover.ha_domain = "cover"
    cover.alexa_device_type = None
    cover.supported_features = 11
    cover.alexa_cover_mode = "discrete"
    cover_capabilities = [item["interface"] for item in discovery_endpoint(cover)["capabilities"]]
    assert "Alexa.ModeController" in cover_capabilities
    assert "Alexa.PlaybackController" in cover_capabilities


def test_gate_generic_openable_contract_is_distinct_from_discrete_cover() -> None:
    gate_endpoint = discovery_endpoint(_switch(alexa_device_type="gate"))
    gate_toggle = next(
        item
        for item in gate_endpoint["capabilities"]
        if item["interface"] == "Alexa.ToggleController"
    )

    cover = _switch()
    cover.ha_entity_id = "cover.discrete"
    cover.ha_domain = "cover"
    cover.supported_features = 11
    cover.alexa_cover_mode = "discrete"
    cover_endpoint = discovery_endpoint(cover)
    cover_mode = next(
        item
        for item in cover_endpoint["capabilities"]
        if item["interface"] == "Alexa.ModeController"
    )

    assert gate_endpoint["displayCategories"] == cover_endpoint["displayCategories"] == ["OTHER"]
    assert gate_toggle["instance"] == "Gate.Opening"
    assert cover_mode["instance"] == "PositionCommand"
    assert gate_toggle["capabilityResources"] != cover_mode["capabilityResources"]
    assert {
        action
        for mapping in gate_toggle["semantics"]["actionMappings"]
        for action in mapping["actions"]
    } == {"Alexa.Actions.Open", "Alexa.Actions.Close"}
    assert "Alexa.PowerController" not in [
        item["interface"] for item in gate_endpoint["capabilities"]
    ]
    assert "Alexa.PowerController" not in [
        item["interface"] for item in cover_endpoint["capabilities"]
    ]
