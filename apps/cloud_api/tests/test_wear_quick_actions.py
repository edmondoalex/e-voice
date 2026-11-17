import pytest
from pydantic import ValidationError

from apps.cloud_api.app.wear_api import QuickActionsRequest


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
                },
                {
                    "slot": 2,
                    "label": "Spegni",
                    "utterance": "Spegni luce ufficio Alex",
                    "icon": "power",
                    "color": "blue",
                    "confirmation": False,
                },
            ]
        }
    )
    assert payload.actions[0].confirmation is True
    assert payload.actions[1].confirmation is False


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
