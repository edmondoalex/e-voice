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


def test_gate_override_discovers_open_close_mode_controller() -> None:
    entity = _switch(alexa_device_type="gate")
    endpoint = discovery_endpoint(entity)

    assert endpoint["displayCategories"] == ["GARAGE_DOOR"]
    interfaces = [item["interface"] for item in endpoint["capabilities"]]
    assert "Alexa.ModeController" in interfaces
    assert "Alexa.PowerController" not in interfaces

    mode = next(
        item for item in endpoint["capabilities"] if item["interface"] == "Alexa.ModeController"
    )
    assert mode == {
        "type": "AlexaInterface",
        "interface": "Alexa.ModeController",
        "version": "3",
        "properties": {
            "supported": [{"name": "mode"}],
            "proactivelyReported": True,
            "retrievable": True,
        },
        "instance": "GarageDoor.Position",
        "capabilityResources": {
            "friendlyNames": [{"@type": "asset", "value": {"assetId": "Alexa.Setting.Mode"}}]
        },
        "configuration": {
            "ordered": False,
            "supportedModes": [
                {
                    "value": "Position.Up",
                    "modeResources": {
                        "friendlyNames": [
                            {"@type": "asset", "value": {"assetId": "Alexa.Value.Open"}},
                            {
                                "@type": "text",
                                "value": {"text": "Open", "locale": "en-US"},
                            },
                        ]
                    },
                },
                {
                    "value": "Position.Down",
                    "modeResources": {
                        "friendlyNames": [
                            {"@type": "asset", "value": {"assetId": "Alexa.Value.Close"}},
                            {
                                "@type": "text",
                                "value": {"text": "Closed", "locale": "en-US"},
                            },
                        ]
                    },
                },
            ],
        },
        "semantics": {
            "actionMappings": [
                {
                    "@type": "ActionsToDirective",
                    "actions": ["Alexa.Actions.Open", "Alexa.Actions.Raise"],
                    "directive": {"name": "SetMode", "payload": {"mode": "Position.Up"}},
                },
                {
                    "@type": "ActionsToDirective",
                    "actions": ["Alexa.Actions.Close", "Alexa.Actions.Lower"],
                    "directive": {"name": "SetMode", "payload": {"mode": "Position.Down"}},
                },
            ],
            "stateMappings": [
                {
                    "@type": "StatesToValue",
                    "states": ["Alexa.States.Open"],
                    "value": "Position.Up",
                },
                {
                    "@type": "StatesToValue",
                    "states": ["Alexa.States.Closed"],
                    "value": "Position.Down",
                },
            ],
        },
    }
    actions = {
        action for mapping in mode["semantics"]["actionMappings"] for action in mapping["actions"]
    }
    assert {"Alexa.Actions.Open", "Alexa.Actions.Close"} <= actions


def test_gate_open_close_maps_to_switch_power() -> None:
    entity = _switch(alexa_device_type="gate")
    assert _command("Alexa.ModeController", "SetMode", {"mode": "Position.Up"}, entity) == {
        "operation": "power_on"
    }
    assert _command("Alexa.ModeController", "SetMode", {"mode": "Position.Down"}, entity) == {
        "operation": "power_off"
    }


def test_gate_state_reports_open_closed_mode() -> None:
    entity = _switch(alexa_device_type="gate", state="off")
    props = state_properties(entity)
    mode = next(item for item in props if item["namespace"] == "Alexa.ModeController")
    assert mode["instance"] == "GarageDoor.Position"
    assert mode["value"] == "Position.Down"

    entity.state = "on"
    props = state_properties(entity)
    mode = next(item for item in props if item["namespace"] == "Alexa.ModeController")
    assert mode["value"] == "Position.Up"


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
