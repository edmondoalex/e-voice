import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cloud_api.app.config import get_settings
from apps.cloud_api.app.database import get_database_session
from apps.cloud_api.app.main import app

SKILL_ID = "amzn1.ask.skill.6f4ff736-deee-43b8-bf09-6399d0f0a4a2"


def launch(skill_id: str = SKILL_ID) -> dict[str, object]:
    return {
        "session": {"application": {"applicationId": skill_id}},
        "request": {"type": "LaunchRequest"},
    }


def intent(name: str, **slots: str) -> dict[str, object]:
    return {
        "session": {"application": {"applicationId": SKILL_ID}},
        "request": {
            "type": "IntentRequest",
            "intent": {
                "name": name,
                "slots": {
                    key: {"name": key, "value": value} for key, value in slots.items()
                },
            },
        },
    }


@pytest.fixture
async def client(session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    async def database_override():  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setenv("EKONEX_ALEXA_LABORATORY_ENABLED", "true")
    monkeypatch.setenv("EKONEX_ALEXA_LABORATORY_SKILL_ID", SKILL_ID)
    monkeypatch.setenv("EKONEX_ALEXA_LABORATORY_BACKEND_TOKEN", "lab-secret")
    get_settings.cache_clear()
    app.dependency_overrides[get_database_session] = database_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value
    app.dependency_overrides.clear()
    get_settings.cache_clear()


async def test_launch_opens_read_only_conversation(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/alexa/laboratory", json=launch(), headers={"Authorization": "Bearer lab-secret"}
    )
    assert response.status_code == 200
    assert response.json()["response"]["shouldEndSession"] is False


async def test_wrong_skill_id_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/alexa/laboratory",
        json=launch("amzn1.ask.skill.other"),
        headers={"Authorization": "Bearer lab-secret"},
    )
    assert response.status_code == 403


async def test_missing_lambda_credential_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/alexa/laboratory", json=launch())
    assert response.status_code == 401


def test_structured_temperature_intent_builds_canonical_utterance() -> None:
    from apps.cloud_api.app.alexa_laboratory import _utterance

    alexa_intent = intent("TemperatureIntent", sensor="acqua calda")["request"]["intent"]

    assert _utterance(alexa_intent) == "temperatura acqua calda"


def test_combined_photovoltaic_intent_preserves_both_sites() -> None:
    from apps.cloud_api.app.alexa_laboratory import _utterance

    alexa_intent = intent("CombinedPhotovoltaicIntent")["request"]["intent"]

    assert _utterance(alexa_intent) == "quanto produce il fotovoltaico SAS e privato"


def test_collective_intents_build_generic_engine_queries() -> None:
    from apps.cloud_api.app.alexa_laboratory import _utterance

    expected = {
        "BatterySummaryIntent": "percentuale di tutte le batterie",
        "ConsumptionSummaryIntent": "tutti i consumi",
        "TemperatureSummaryIntent": "tutte le temperature",
        "GridPowerSummaryIntent": "potenza rete di tutti gli impianti",
        "ExportedEnergySummaryIntent": "energia oggi esportata da tutti gli impianti",
        "ImportedEnergySummaryIntent": "energia oggi importata da tutti gli impianti",
        "ProducedEnergySummaryIntent": "energia oggi prodotta da tutti gli impianti",
        "ConsumedEnergySummaryIntent": "energia oggi consumata da tutti gli impianti",
    }
    for intent_name, utterance in expected.items():
        alexa_intent = intent(intent_name)["request"]["intent"]
        assert _utterance(alexa_intent) == utterance


def test_site_less_energy_intent_falls_back_to_all_installations() -> None:
    from apps.cloud_api.app.alexa_laboratory import _utterance

    alexa_intent = intent("ConsumedEnergyIntent")["request"]["intent"]

    assert _utterance(alexa_intent) == "energia oggi consumata da tutti gli impianti"


def test_structured_intent_prefers_alexa_canonical_slot_resolution() -> None:
    from apps.cloud_api.app.alexa_laboratory import _utterance

    alexa_intent = intent("ConsumptionIntent", site="s. a. s.")["request"]["intent"]
    alexa_intent["slots"]["site"]["resolutions"] = {
        "resolutionsPerAuthority": [
            {
                "status": {"code": "ER_SUCCESS_MATCH"},
                "values": [{"value": {"name": "SAS", "id": "sas"}}],
            }
        ]
    }

    assert _utterance(alexa_intent) == "quanto consuma SAS"


def test_lock_status_intent_preserves_requested_lock_name() -> None:
    from apps.cloud_api.app.alexa_laboratory import _utterance

    alexa_intent = intent("LockStatusIntent", lock="porta ufficio")["request"]["intent"]

    assert _utterance(alexa_intent) == "stato serratura porta ufficio"


def test_lock_summary_intent_builds_summary_utterance() -> None:
    from apps.cloud_api.app.alexa_laboratory import _utterance

    alexa_intent = intent("LockSummaryIntent")["request"]["intent"]

    assert _utterance(alexa_intent) == "stato di tutte le serrature"


async def test_fallback_gives_a_useful_example(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/alexa/laboratory",
        json=intent("AMAZON.FallbackIntent"),
        headers={"Authorization": "Bearer lab-secret"},
    )

    assert response.status_code == 200
    assert "quanto produce" in response.json()["response"]["outputSpeech"]["text"]
