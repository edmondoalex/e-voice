from datetime import UTC, datetime, timedelta
from typing import Any

from apps.cloud_api.app.conversation import (
    ConversationEngine,
    ConversationSession,
    EntitySnapshot,
    ReplyStatus,
)

NOW = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def entity(
    entity_id: str,
    name: str,
    state: str | None,
    unit: str,
    device_class: str,
    **kwargs: Any,
) -> EntitySnapshot:
    observed_at = kwargs.pop("observed_at", NOW)
    return EntitySnapshot(
        entity_id=entity_id,
        name=name,
        domain="sensor",
        state=state,
        unit=unit,
        device_class=device_class,
        observed_at=observed_at,
        **kwargs,
    )


def test_answers_photovoltaic_power_with_evidence() -> None:
    pv = entity(
        "sensor.pv_power",
        "Produzione fotovoltaico",
        "3.8",
        "kW",
        "power",
        aliases=("fotovoltaico",),
    )

    reply = ConversationEngine().ask(
        "Quanta potenza sta producendo il fotovoltaico?", [pv], now=NOW
    )

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == "In questo momento il fotovoltaico sta producendo 3.8 kW."
    assert reply.evidence[0].entity_id == "sensor.pv_power"


def test_resolves_acs_alias_instead_of_room_temperature() -> None:
    acs = entity(
        "sensor.acs",
        "Temperatura accumulo sanitario",
        "54",
        "°C",
        "temperature",
        aliases=("acqua calda", "acs"),
    )
    room = entity(
        "sensor.room",
        "Temperatura soggiorno",
        "22",
        "°C",
        "temperature",
        area="soggiorno",
    )

    reply = ConversationEngine().ask("Quanto è calda l'acqua calda?", [room, acs], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.intent == "acs_temperature"
    assert reply.evidence[0].entity_id == "sensor.acs"


def test_asks_for_clarification_when_temperature_is_ambiguous() -> None:
    kitchen = entity("sensor.kitchen", "Temperatura cucina", "23", "°C", "temperature")
    bedroom = entity("sensor.bedroom", "Temperatura camera", "21", "°C", "temperature")

    reply = ConversationEngine().ask("Qual è la temperatura?", [kitchen, bedroom], now=NOW)

    assert reply.status is ReplyStatus.AMBIGUOUS
    assert set(reply.candidates) == {"Temperatura cucina", "Temperatura camera"}


def test_area_disambiguates_temperature() -> None:
    kitchen = entity(
        "sensor.kitchen", "Temperatura cucina", "23", "°C", "temperature", area="cucina"
    )
    bedroom = entity(
        "sensor.bedroom", "Temperatura camera", "21", "°C", "temperature", area="camera"
    )

    reply = ConversationEngine().ask(
        "Quanti gradi ci sono in cucina?", [bedroom, kitchen], now=NOW
    )

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.evidence[0].entity_id == "sensor.kitchen"


def test_unavailable_value_is_never_presented_as_measurement() -> None:
    battery = entity("sensor.battery", "Batteria casa", "unknown", "%", "battery")

    reply = ConversationEngine().ask("A quanto è la batteria?", [battery], now=NOW)

    assert reply.status is ReplyStatus.UNAVAILABLE
    assert "unknown" not in reply.speech


def test_stale_reading_is_disclosed() -> None:
    old = entity(
        "sensor.pv_power",
        "Produzione fotovoltaico",
        "2.1",
        "kW",
        "power",
        observed_at=NOW - timedelta(minutes=20),
    )

    reply = ConversationEngine().ask("Quanto produce il fotovoltaico?", [old], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.diagnostics["freshness"] == "stale"
    assert "potrebbe non essere aggiornato" in reply.speech


def test_does_not_answer_from_wrong_device_class() -> None:
    energy = entity(
        "sensor.pv_energy_today", "Produzione fotovoltaico oggi", "18", "kWh", "energy"
    )

    reply = ConversationEngine().ask("Quanto produce il fotovoltaico?", [energy], now=NOW)

    assert reply.status is ReplyStatus.NOT_FOUND


def test_unsupported_request_cannot_become_a_command() -> None:
    reply = ConversationEngine().ask("Disattiva l'allarme e apri il cancello", [], now=NOW)

    assert reply.status is ReplyStatus.UNSUPPORTED
    assert not reply.evidence


def test_session_resolves_short_follow_up_after_ambiguity() -> None:
    acs = entity(
        "sensor.acs",
        "Temperatura ACS",
        "54",
        "°C",
        "temperature",
        aliases=("acqua calda", "acs"),
    )
    living = entity(
        "sensor.living",
        "Temperatura soggiorno",
        "22.4",
        "°C",
        "temperature",
        area="soggiorno",
    )
    session = ConversationSession()

    first = session.ask("Qual è la temperatura?", [acs, living], now=NOW)
    second = session.ask("soggiorno", [acs, living], now=NOW)

    assert first.status is ReplyStatus.AMBIGUOUS
    assert second.status is ReplyStatus.ANSWERED
    assert second.evidence[0].entity_id == "sensor.living"


def test_session_discards_context_after_answer() -> None:
    kitchen = entity(
        "sensor.kitchen", "Temperatura cucina", "23", "°C", "temperature", area="cucina"
    )
    living = entity(
        "sensor.living",
        "Temperatura soggiorno",
        "22.4",
        "°C",
        "temperature",
        area="soggiorno",
    )
    session = ConversationSession()

    session.ask("Qual è la temperatura?", [kitchen, living], now=NOW)
    session.ask("soggiorno", [kitchen, living], now=NOW)
    unrelated = session.ask("soggiorno", [kitchen, living], now=NOW)

    assert unrelated.status is ReplyStatus.UNSUPPORTED
