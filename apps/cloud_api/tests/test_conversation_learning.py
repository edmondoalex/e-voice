import asyncio
from types import SimpleNamespace
from uuid import uuid4

from apps.cloud_api.app.ai_interpreter import OpenAIQuestionInterpreter
from apps.cloud_api.app.conversation import ConversationEngine
from apps.cloud_api.app.conversation_service import ConversationEntityService


class FakeLearningStore:
    def __init__(self) -> None:
        self.values: dict[tuple[object, object, str], str] = {}

    async def get(self, tenant_id: object, installation_id: object, utterance: str) -> str | None:
        return self.values.get((tenant_id, installation_id, utterance.casefold()))

    async def remember(
        self,
        tenant_id: object,
        installation_id: object,
        utterance: str,
        canonical: str,
        intent: str,
    ) -> None:
        self.values[(tenant_id, installation_id, utterance.casefold())] = canonical


def test_first_unknown_phrase_uses_ai_and_second_uses_learning(monkeypatch) -> None:
    calls = 0

    async def interpret(*args, **kwargs) -> str:
        nonlocal calls
        calls += 1
        return "quanto produce il fotovoltaico SAS"

    monkeypatch.setattr(OpenAIQuestionInterpreter, "interpret", interpret)
    monkeypatch.setattr(
        "apps.cloud_api.app.conversation_service.get_settings",
        lambda: SimpleNamespace(
            conversation_learning_enabled=True,
            openai_api_key="test",
            openai_model="test",
            openai_timeout_seconds=1,
        ),
    )
    entity = SimpleNamespace(
        deleted_at=None,
        voice_name="Fotovoltaico SAS",
        display_name=None,
        friendly_name=None,
        ha_entity_id="sensor.pv_sas",
        ha_domain="sensor",
        state="850",
        attributes_json={"unit_of_measurement": "W"},
        device_class="power",
        area_name=None,
        voice_aliases=["produzione SAS"],
        available=True,
        last_seen_at=None,
        last_changed_at=None,
    )

    class Entities:
        async def list_for_installation(self, **kwargs):
            return [entity]

    store = FakeLearningStore()
    service = object.__new__(ConversationEntityService)
    service._entities = Entities()
    service._engine = ConversationEngine()
    service._learning_store = store
    tenant_id = uuid4()
    installation_id = uuid4()

    async def exercise() -> tuple[str, str]:
        first = await service.ask_for_scope(
            tenant_id, installation_id, "Come siamo messi con il sole SAS?", named_only=True
        )
        second = await service.ask_for_scope(
            tenant_id, installation_id, "Come siamo messi con il sole SAS?", named_only=True
        )
        return first.diagnostics["interpretation"], second.diagnostics["interpretation"]

    assert asyncio.run(exercise()) == ("ai", "learned")
    assert calls == 1


def test_combined_site_query_is_visible_in_learning_store(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.cloud_api.app.conversation_service.get_settings",
        lambda: SimpleNamespace(conversation_learning_enabled=True),
    )
    entities = [
        SimpleNamespace(
            deleted_at=None,
            voice_name=f"Fotovoltaico {site}",
            display_name=None,
            friendly_name=None,
            ha_entity_id=f"sensor.pv_{site.casefold()}",
            ha_domain="sensor",
            state=value,
            attributes_json={"unit_of_measurement": "W"},
            device_class="power",
            area_name=None,
            voice_aliases=[],
            available=True,
            last_seen_at=None,
            last_changed_at=None,
        )
        for site, value in (("SAS", "5600"), ("Privato", "3200"))
    ]

    class Entities:
        async def list_for_installation(self, **kwargs):
            return entities

    store = FakeLearningStore()
    service = object.__new__(ConversationEntityService)
    service._entities = Entities()
    service._engine = ConversationEngine()
    service._learning_store = store
    tenant_id = uuid4()
    installation_id = uuid4()
    utterance = "Dimmi la produzione del fotovoltaico SAS e privato"

    reply = asyncio.run(
        service.ask_for_scope(tenant_id, installation_id, utterance, named_only=True)
    )

    assert "mentre quello privato" in reply.speech
    assert store.values[(tenant_id, installation_id, utterance.casefold())] == utterance
