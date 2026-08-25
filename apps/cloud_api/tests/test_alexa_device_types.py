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


def test_gate_override_discovers_binary_range_controller() -> None:
    entity = _switch(alexa_device_type="gate")
    endpoint = discovery_endpoint(entity)

    assert endpoint["displayCategories"] == ["OTHER"]
    interfaces = [item["interface"] for item in endpoint["capabilities"]]
    assert "Alexa.RangeController" in interfaces
    assert "Alexa.ModeController" not in interfaces
    assert "Alexa.PowerController" not in interfaces

    controller = next(
        item for item in endpoint["capabilities"] if item["interface"] == "Alexa.RangeController"
    )
    assert controller == {
        "type": "AlexaInterface",
        "interface": "Alexa.RangeController",
        "version": "3",
        "properties": {
            "supported": [{"name": "rangeValue"}],
            "proactivelyReported": True,
            "retrievable": True,
        },
        "instance": "Gate.Position",
        "capabilityResources": {
            "friendlyNames": [
                {"@type": "asset", "value": {"assetId": "Alexa.Setting.Opening"}},
                {"@type": "text", "value": {"text": "Cancello", "locale": "it-IT"}},
            ]
        },
        "configuration": {
            "supportedRange": {"minimumValue": 0, "maximumValue": 100, "precision": 100}
        },
        "semantics": {
            "actionMappings": [
                {
                    "@type": "ActionsToDirective",
                    "actions": ["Alexa.Actions.Open"],
                    "directive": {"name": "SetRangeValue", "payload": {"rangeValue": 100}},
                },
                {
                    "@type": "ActionsToDirective",
                    "actions": ["Alexa.Actions.Close"],
                    "directive": {"name": "SetRangeValue", "payload": {"rangeValue": 0}},
                },
            ],
            "stateMappings": [
                {
                    "@type": "StatesToValue",
                    "states": ["Alexa.States.Open"],
                    "value": 100,
                },
                {
                    "@type": "StatesToValue",
                    "states": ["Alexa.States.Closed"],
                    "value": 0,
                },
            ],
        },
    }
    actions = {
        action
        for mapping in controller["semantics"]["actionMappings"]
        for action in mapping["actions"]
    }
    assert {"Alexa.Actions.Open", "Alexa.Actions.Close"} <= actions


def test_gate_open_close_maps_to_switch_power() -> None:
    entity = _switch(alexa_device_type="gate")
    assert _command("Alexa.RangeController", "SetRangeValue", {"rangeValue": 100}, entity) == {
        "operation": "power_on"
    }
    assert _command("Alexa.RangeController", "SetRangeValue", {"rangeValue": 0}, entity) == {
        "operation": "power_off"
    }
    for invalid in (1, 50, 99, -1, 101, True, None, "100", float("nan"), float("inf")):
        assert (
            _command("Alexa.RangeController", "SetRangeValue", {"rangeValue": invalid}, entity)
            is None
        )
    assert (
        _command("Alexa.RangeController", "AdjustRangeValue", {"rangeValueDelta": 100}, entity)
        is None
    )


def test_gate_state_reports_binary_range() -> None:
    entity = _switch(alexa_device_type="gate", state="off")
    props = state_properties(entity)
    position = next(item for item in props if item["namespace"] == "Alexa.RangeController")
    assert position["instance"] == "Gate.Position"
    assert position["value"] == 0

    entity.state = "on"
    props = state_properties(entity)
    position = next(item for item in props if item["namespace"] == "Alexa.RangeController")
    assert position["value"] == 100


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
    gate_range = next(
        item
        for item in gate_endpoint["capabilities"]
        if item["interface"] == "Alexa.RangeController"
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
    assert gate_range["instance"] == "Gate.Position"
    assert cover_mode["instance"] == "Blinds.Position"
    assert gate_range["capabilityResources"] != cover_mode["capabilityResources"]
    assert {
        action
        for mapping in gate_range["semantics"]["actionMappings"]
        for action in mapping["actions"]
    } == {"Alexa.Actions.Open", "Alexa.Actions.Close"}
    assert "Alexa.PowerController" not in [
        item["interface"] for item in gate_endpoint["capabilities"]
    ]
    assert "Alexa.PowerController" in [item["interface"] for item in cover_endpoint["capabilities"]]
