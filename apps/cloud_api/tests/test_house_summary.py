from apps.cloud_api.app.conversation import ConversationEngine, EntitySnapshot, ReplyStatus


def test_house_summary_prioritizes_and_limits_findings() -> None:
    entities = (
        EntitySnapshot("sensor.offline", "Centrale", "sensor", None, available=False),
        EntitySnapshot(
            "binary_sensor.garage", "Porta garage", "binary_sensor", "on", category="opening_status"
        ),
        EntitySnapshot("lock.front", "Porta ingresso", "lock", "unlocked"),
        EntitySnapshot("sensor.battery", "Batteria telecomando", "sensor", "12", "%", "battery"),
    )

    reply = ConversationEngine().ask("C'è qualcosa da controllare?", entities)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.intent == "house_summary"
    assert "Centrale non è disponibile" in reply.speech
    assert "Porta garage risulta aperta" in reply.speech
    assert "Porta ingresso risulta sbloccata" in reply.speech
    assert "1 altre segnalazioni" in reply.speech
    assert len(reply.evidence) == 3


def test_house_summary_reports_all_clear_for_normal_authorized_entities() -> None:
    entities = (
        EntitySnapshot(
            "sensor.temperature", "Temperatura sala", "sensor", "21", "°C", "temperature"
        ),
        EntitySnapshot("sensor.battery", "Batteria", "sensor", "85", "%", "battery"),
        EntitySnapshot(
            "binary_sensor.window", "Finestra", "binary_sensor", "off", category="opening_status"
        ),
    )

    reply = ConversationEngine().ask("Fammi il riepilogo della casa", entities)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech.startswith("La casa è in ordine")
    assert reply.evidence == ()


def test_house_summary_normalizes_power_units() -> None:
    entity = EntitySnapshot(
        "sensor.consumption",
        "Consumo casa",
        "sensor",
        "5.6",
        "kW",
        "power",
        category="consumption_power",
    )

    reply = ConversationEngine().ask("Come va la casa?", [entity])

    assert "5.6 kilowatt" in reply.speech
