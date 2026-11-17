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
