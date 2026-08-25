from uuid import uuid4

import pytest

from apps.cloud_api.app.alexa import _command, capabilities, discovery_endpoint, state_properties
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

    assert endpoint["displayCategories"] == ["OTHER"]
    interfaces = [item["interface"] for item in endpoint["capabilities"]]
    assert "Alexa.ModeController" in interfaces
    assert "Alexa.PowerController" not in interfaces

    mode = next(
        item for item in endpoint["capabilities"] if item["interface"] == "Alexa.ModeController"
    )
    assert mode["instance"] == "Gate.Position"
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
    assert mode["instance"] == "Gate.Position"
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
