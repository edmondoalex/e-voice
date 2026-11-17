import pytest
from pydantic import ValidationError

from apps.cloud_api.app.wear_api import (
    QuickActionsRequest,
    _is_activating,
    _resolve_toggle,
    _state_visual,
    _wear_operations,
)


def test_quick_actions_accept_per_button_confirmation():
    payload = QuickActionsRequest.model_validate(
        {
            "actions": [
                {
                    "slot": 1,
                    "label": "Luce ufficio",
                    "utterance": "Accendi luce ufficio Alex",
                    "icon": "light",
                    "color": "red",
                    "confirmation": True,
                    "confirmation_on_activate": False,
                },
                {
                    "slot": 2,
                    "label": "Spegni",
                    "utterance": "Spegni luce ufficio Alex",
                    "icon": "power",
                    "color": "blue",
                    "confirmation": False,
                    "confirmation_on_activate": True,
                },
            ]
        }
    )
    assert payload.actions[0].confirmation is True
    assert payload.actions[1].confirmation is False
    assert payload.actions[1].confirmation_on_activate is True


def test_quick_actions_accept_direct_entity_operations():
    payload = QuickActionsRequest.model_validate(
        {
            "actions": [
                {
                    "slot": 1,
                    "label": "Accendi ufficio",
                    "entity_id": "11111111-1111-1111-1111-111111111111",
                    "operation": "power_on",
                    "icon": "light",
                    "color": "blue",
                    "confirmation": False,
                },
                {
                    "slot": 2,
                    "label": "Scenario sera",
                    "entity_id": "22222222-2222-2222-2222-222222222222",
                    "operation": "activate",
                    "icon": "scene",
                    "color": "purple",
                    "confirmation": True,
                },
            ]
        }
    )
    assert payload.actions[0].operation == "power_on"
    assert payload.actions[0].utterance is None
    assert payload.actions[1].operation == "activate"


def test_quick_actions_accept_toggle():
    payload = QuickActionsRequest.model_validate(
        {
            "actions": [
                {
                    "slot": 1,
                    "label": "Luce ufficio",
                    "entity_id": "11111111-1111-1111-1111-111111111111",
                    "operation": "toggle",
                }
            ]
        }
    )
    assert payload.actions[0].operation == "toggle"


def test_toggle_is_official_for_stateful_entities():
    assert ("toggle", "Inverti stato") in _wear_operations("light")
    assert ("toggle", "Inverti stato") in _wear_operations("switch")
    assert ("toggle", "Inverti stato") in _wear_operations("cover")
    assert ("toggle", "Inverti stato") in _wear_operations("lock")


@pytest.mark.parametrize(
    ("domain", "state_value", "expected"),
    [
        ("light", "on", "power_off"),
        ("light", "off", "power_on"),
        ("cover", "open", "close"),
        ("cover", "closed", "open"),
        ("lock", "locked", "unlock"),
        ("lock", "unlocked", "lock"),
    ],
)
def test_toggle_resolves_from_authoritative_state(domain, state_value, expected):
    assert _resolve_toggle(domain, state_value) == expected


@pytest.mark.parametrize("state_value", [None, "", "unknown", "unavailable"])
def test_toggle_rejects_unknown_state(state_value):
    with pytest.raises(ValueError, match="quick_action_state_unknown"):
        _resolve_toggle("light", state_value)


def test_state_visuals_follow_entity_state_and_selected_semantics():
    assert _state_visual("light", "on", "light") == "light_on"
    assert _state_visual("light", "off", "light") == "light_off"
    assert _state_visual("cover", "open", "blind") == "cover_open"
    assert _state_visual("cover", "closed", "blind") == "cover_closed"
    assert _state_visual("lock", "locked", "lock") == "lock_closed"
    assert _state_visual("lock", "unlocked", "lock") == "lock_open"
    assert _state_visual("switch", "off", "lock") == "lock_closed"


def test_confirmation_on_activate_uses_real_state_for_toggle():
    assert _is_activating("light", "toggle", "off") is True
    assert _is_activating("light", "toggle", "on") is False
    assert _is_activating("cover", "toggle", "closed") is True
    assert _is_activating("cover", "toggle", "open") is False
    assert _is_activating("lock", "toggle", "locked") is True


@pytest.mark.parametrize(
    "action",
    [
        {"slot": 1, "label": "A", "entity_id": "11111111-1111-1111-1111-111111111111"},
        {"slot": 1, "label": "A", "operation": "power_on"},
        {
            "slot": 1,
            "label": "A",
            "entity_id": "11111111-1111-1111-1111-111111111111",
            "operation": "format_disk",
        },
    ],
)
def test_quick_actions_reject_incomplete_or_unknown_direct_targets(action):
    with pytest.raises(ValidationError):
        QuickActionsRequest.model_validate({"actions": [action]})


@pytest.mark.parametrize(
    "actions",
    [
        [{"slot": 1, "label": "A", "utterance": "Accendi A", "icon": "raw", "color": "red"}],
        [{"slot": 1, "label": "A", "utterance": "Accendi A", "icon": "light", "color": "pink"}],
        [
            {"slot": 1, "label": "A", "utterance": "Accendi A"},
            {"slot": 1, "label": "B", "utterance": "Accendi B"},
        ],
    ],
)
def test_quick_actions_reject_uncontrolled_values(actions):
    with pytest.raises(ValidationError):
        QuickActionsRequest.model_validate({"actions": actions})
