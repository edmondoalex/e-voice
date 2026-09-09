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
    domain = kwargs.pop("domain", "sensor")
    return EntitySnapshot(
        entity_id=entity_id,
        name=name,
        domain=domain,
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


def test_power_reply_infers_watts_when_unit_is_missing() -> None:
    pv = entity("sensor.pv_power", "Fotovoltaico SAS", "5600", None, "power")

    reply = ConversationEngine().ask("Quanto produce il fotovoltaico s. a. s.?", [pv], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == "In questo momento il fotovoltaico sta producendo 5600 W."


def test_answers_photovoltaic_power_for_both_sites() -> None:
    sas = entity("sensor.pv_sas", "Fotovoltaico SAS", "5600", "W", "power")
    private = entity("sensor.pv_private", "Fotovoltaico Privato", "3200", "W", "power")

    reply = ConversationEngine().ask(
        "Dimmi la produzione del fotovoltaico SAS e privato", [sas, private], now=NOW
    )

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == (
        "Il fotovoltaico SAS sta producendo 5600 W, mentre quello privato sta producendo 3200 W."
    )
    assert {item.entity_id for item in reply.evidence} == {
        "sensor.pv_sas",
        "sensor.pv_private",
    }


def test_all_photovoltaics_means_both_sites() -> None:
    sas = entity("sensor.pv_sas", "Fotovoltaico SAS", "5600", "W", "power")
    private = entity("sensor.pv_private", "Fotovoltaico Privato", "3200", "W", "power")

    reply = ConversationEngine().ask(
        "Dimmi la produzione di tutti i fotovoltaici", [sas, private], now=NOW
    )

    assert reply.status is ReplyStatus.ANSWERED
    assert "5600 W" in reply.speech
    assert "3200 W" in reply.speech


def test_summarizes_all_battery_percentages() -> None:
    sas = entity("sensor.battery_sas", "Batteria SAS", "81", "%", "battery")
    private = entity("sensor.battery_private", "Batteria Privato", "64", "%", "battery")

    reply = ConversationEngine().ask(
        "Dimmi la percentuale di tutte le batterie", [sas, private], now=NOW
    )

    assert reply.status is ReplyStatus.ANSWERED
    assert "Batteria SAS: 81%" in reply.speech
    assert "Batteria Privato: 64%" in reply.speech
    assert len(reply.evidence) == 2


def test_sas_and_private_exported_energy_returns_both_sites() -> None:
    sas = entity("sensor.export_sas", "Energia oggi SAS esportata", "4.2", "kWh", "energy")
    private = entity(
        "sensor.export_private", "Energia oggi privato esportata", "2.1", "kWh", "energy"
    )

    reply = ConversationEngine().ask(
        "L'energia SAS e privato oggi esportata", [sas, private], now=NOW
    )

    assert reply.status is ReplyStatus.ANSWERED
    assert "Energia oggi SAS esportata: 4,2 chilowattora" in reply.speech
    assert "Energia oggi privato esportata: 2,1 chilowattora" in reply.speech
    assert len(reply.evidence) == 2


def test_answers_textual_alarm_state() -> None:
    alarm = entity("sensor.alarm", "Allarme", "SOLO ESTERNO", None, "")

    reply = ConversationEngine().ask("Qual è lo stato dell'allarme?", [alarm], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == "Lo stato dell'allarme è SOLO ESTERNO."


def test_answers_exact_lock_state_for_named_door() -> None:
    lock = EntitySnapshot(
        entity_id="lock.porta_ufficio",
        name="Porta Ufficio",
        domain="lock",
        state="locked",
        observed_at=NOW,
    )

    reply = ConversationEngine().ask("stato serratura porta ufficio", [lock], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == "Porta Ufficio è chiusa a chiave."


def test_summarizes_all_authorized_locks() -> None:
    locks = (
        EntitySnapshot("lock.sala", "Serratura Sala", "lock", "locked"),
        EntitySnapshot("lock.scala", "Portoncino Scala", "lock", "unlocked"),
    )

    reply = ConversationEngine().ask("stato di tutte le serrature", locks, now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == "Portoncino Scala: aperto. Serratura Sala: chiusa a chiave."


def test_plural_lock_status_never_treats_textual_states_as_unavailable() -> None:
    locks = (
        EntitySnapshot("lock.sala", "Porta Sala", "lock", "unlocked", category="lock_status"),
        EntitySnapshot(
            "lock.ufficio", "Serratura Ufficio", "lock", "locked", category="lock_status"
        ),
    )

    reply = ConversationEngine().ask("stato serratura di tutte", locks, now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == "Porta Sala: aperta. Serratura Ufficio: chiusa a chiave."


def test_reports_only_open_authorized_openings() -> None:
    openings = (
        EntitySnapshot("binary_sensor.garage", "Porta Garage", "binary_sensor", "on"),
        EntitySnapshot("binary_sensor.dest", "Portone Destro", "binary_sensor", "off"),
        EntitySnapshot("binary_sensor.sin", "Portone Sinistro", "binary_sensor", "off"),
    )

    reply = ConversationEngine().ask("ci sono porte o portoni aperti", openings, now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == "Risultano aperti: Porta Garage."


def test_all_doors_uses_authorized_opening_summary() -> None:
    openings = (
        EntitySnapshot("binary_sensor.garage", "Porta Garage", "binary_sensor", "on"),
        EntitySnapshot("binary_sensor.gate", "Portone Destro", "binary_sensor", "off"),
    )

    reply = ConversationEngine().ask("controlla tutte le porte", openings, now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == "Risultano aperti: Porta Garage."


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


def test_thermal_category_summarizes_sensors_without_ha_device_class_or_temperature_name() -> None:
    sensors = (
        entity(
            "sensor.acs",
            "Acqua Calda",
            "69.125",
            "\N{DEGREE SIGN}C",
            "temperature",
            category="thermal_temperature",
        ),
        entity(
            "sensor.puffer",
            "Puffer Alto",
            "73.5625",
            "\N{DEGREE SIGN}C",
            "",
            category="thermal_temperature",
        ),
        entity(
            "sensor.volano",
            "Volano",
            "62.875",
            "\N{DEGREE SIGN}C",
            "",
            category="thermal_temperature",
        ),
    )

    reply = ConversationEngine().ask("tutte le temperature", sensors, now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert {item.entity_id for item in reply.evidence} == {
        "sensor.acs",
        "sensor.puffer",
        "sensor.volano",
    }
    assert "Volano: 63\N{DEGREE SIGN}C" in reply.speech


def test_ambient_temperature_summary_excludes_thermal_category() -> None:
    sensors = (
        entity(
            "sensor.living",
            "Temperatura soggiorno",
            "22.4",
            "°C",
            "temperature",
            category="temperature",
        ),
        entity(
            "sensor.puffer",
            "Puffer alto",
            "71",
            "°C",
            "temperature",
            category="thermal_temperature",
        ),
    )

    reply = ConversationEngine().ask("tutte le temperature ambiente", sensors, now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert "Temperatura soggiorno" in reply.speech
    assert "Puffer alto" not in reply.speech


def test_thermal_temperature_summary_excludes_ambient_category() -> None:
    sensors = (
        entity(
            "sensor.living",
            "Temperatura soggiorno",
            "22.4",
            "°C",
            "temperature",
            category="temperature",
        ),
        entity(
            "sensor.puffer",
            "Puffer alto",
            "71",
            "°C",
            "temperature",
            category="thermal_temperature",
        ),
    )

    reply = ConversationEngine().ask(
        "tutte le temperature della centrale termica", sensors, now=NOW
    )

    assert reply.status is ReplyStatus.ANSWERED
    assert "Puffer alto" in reply.speech
    assert "Temperatura soggiorno" not in reply.speech


def test_area_disambiguates_temperature() -> None:
    kitchen = entity(
        "sensor.kitchen", "Temperatura cucina", "23", "°C", "temperature", area="cucina"
    )
    bedroom = entity(
        "sensor.bedroom", "Temperatura camera", "21", "°C", "temperature", area="camera"
    )

    reply = ConversationEngine().ask("Quanti gradi ci sono in cucina?", [bedroom, kitchen], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.evidence[0].entity_id == "sensor.kitchen"


def test_authorized_temperature_alias_can_be_asked_without_saying_temperature() -> None:
    puffer = entity(
        "sensor.puffer",
        "puffer alto",
        "70.75",
        "°C",
        "temperature",
        aliases=("puffer",),
    )

    reply = ConversationEngine().ask("Puffer?", [puffer], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == "La temperatura è 71°C."
    assert reply.evidence[0].entity_id == "sensor.puffer"


def test_thermostat_temperature_keeps_one_decimal_with_italian_separator() -> None:
    thermostat = entity(
        "climate.living_room",
        "Termostato soggiorno",
        "22.44",
        "°C",
        "temperature",
        domain="climate",
        aliases=("temperatura soggiorno",),
    )

    reply = ConversationEngine().ask("temperatura soggiorno", (thermostat,), now=NOW)

    assert reply.speech == "La temperatura è 22,4°C."


def test_answers_consumption_by_mapped_name() -> None:
    consumption = entity(
        "sensor.consumption_sas",
        "consumo istantaneo SAS",
        "842",
        "W",
        "power",
        aliases=("consumo SAS",),
    )

    reply = ConversationEngine().ask("Consumo SAS?", [consumption], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.intent == "consumption_power"
    assert reply.speech == "Il consumo SAS in questo momento è 842 W."


def test_site_name_disambiguates_consumption_immediately() -> None:
    sas = entity("sensor.sas", "consumo istantaneo SAS", "900", "W", "power")
    private = entity("sensor.private", "consumo istantaneo privato", "500", "W", "power")

    reply = ConversationEngine().ask("Quanto consuma SAS?", [private, sas], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.evidence[0].entity_id == "sensor.sas"


def test_spelled_acronym_disambiguates_sas_site() -> None:
    sas = entity("sensor.sas", "consumo istantaneo SAS", "900", "W", "power")
    private = entity("sensor.private", "consumo istantaneo privato", "500", "W", "power")

    reply = ConversationEngine().ask("Quanto consuma s. a. s.?", [private, sas], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.evidence[0].entity_id == "sensor.sas"


def test_answers_grid_power_for_named_site() -> None:
    sas = entity("sensor.grid_sas", "potenza rete SAS", "120", "W", "power")
    private = entity("sensor.grid_private", "potenza rete privato", "75", "W", "power")

    reply = ConversationEngine().ask("Potenza rete SAS?", [private, sas], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.intent == "grid_power"
    assert reply.speech == "La potenza di rete SAS in questo momento è 120 W."


def test_exact_exported_energy_name_is_not_mistaken_for_pv_power() -> None:
    exported = entity(
        "sensor.export_sas",
        "energia oggi fotovoltaico SAS esportata",
        "4.2",
        "kWh",
        "energy",
    )

    reply = ConversationEngine().ask("Energia oggi fotovoltaico SAS esportata", [exported], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.intent == "exported_energy_today"
    assert reply.speech == "Oggi l'impianto SAS ha esportato 4,2 chilowattora."


def test_small_energy_value_is_spoken_in_watt_hours() -> None:
    exported = entity(
        "sensor.export_sas",
        "energia oggi fotovoltaico SAS esportata",
        "0.08",
        "kWh",
        "energy",
    )

    reply = ConversationEngine().ask("Energia oggi fotovoltaico SAS esportata", [exported], now=NOW)

    assert reply.speech == "Oggi l'impianto SAS ha esportato 80 wattora."


def test_exact_imported_energy_name_selects_private_site() -> None:
    imported = entity(
        "sensor.import_private",
        "energia oggi fotovoltaico privato importata",
        "1.6",
        "kWh",
        "energy",
    )

    reply = ConversationEngine().ask(
        "Energia oggi fotovoltaico privato importata", [imported], now=NOW
    )

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.intent == "imported_energy_today"
    assert reply.speech == "Oggi l'impianto privato ha importato 1,6 chilowattora."


def test_unavailable_value_is_never_presented_as_measurement() -> None:
    battery = entity("sensor.battery", "Batteria casa", "unknown", "%", "battery")

    reply = ConversationEngine().ask("A quanto è la batteria?", [battery], now=NOW)

    assert reply.status is ReplyStatus.UNAVAILABLE
    assert "unknown" not in reply.speech


def test_battery_reply_does_not_repeat_name_or_space_percent() -> None:
    battery = entity("sensor.battery", "batteria SAS", "13", "%", "battery")

    reply = ConversationEngine().ask("A quanto è la batteria SAS?", [battery], now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert reply.speech == "La batteria SAS è al 13%."


def test_battery_reply_infers_percent_when_unit_is_missing() -> None:
    battery = entity("sensor.battery", "batteria SAS", "100", None, "battery")

    reply = ConversationEngine().ask("A quanto è la batteria SAS?", [battery], now=NOW)

    assert reply.speech == "La batteria SAS è al 100%."


def test_non_numeric_sensor_state_is_not_spoken_as_measurement() -> None:
    battery = entity("sensor.battery", "Batteria casa", "charging", "%", "battery")

    reply = ConversationEngine().ask("A quanto è la batteria?", [battery], now=NOW)

    assert reply.status is ReplyStatus.UNAVAILABLE
    assert "charging" not in reply.speech


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
    energy = entity("sensor.pv_energy_today", "Produzione fotovoltaico oggi", "18", "kWh", "energy")

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


def test_category_routed_light_summary_reports_only_lights_on() -> None:
    lights = (
        EntitySnapshot("light.scrivania", "Scrivania", "light", "on", category="luci_primo"),
        EntitySnapshot("light.corridoio", "Corridoio", "light", "off", category="luci_primo"),
    )

    reply = ConversationEngine().ask(
        "tutti i valori luci primo piano", lights, now=NOW, category_routed=True
    )

    assert reply.status is ReplyStatus.ANSWERED
    assert "Scrivania" in reply.speech
    assert "Corridoio" not in reply.speech


def test_light_summary_is_strictly_scoped_to_requested_area() -> None:
    lights = (
        EntitySnapshot("light.cucina", "Tavolo", "light", "on", area="Cucina"),
        EntitySnapshot("light.sala", "Lampadario", "light", "on", area="Sala"),
        EntitySnapshot("light.piano", "Scala", "light", "off", area="Cucina"),
    )

    reply = ConversationEngine().ask("quali luci sono accese in cucina", lights, now=NOW)

    assert reply.status is ReplyStatus.ANSWERED
    assert {item.entity_id for item in reply.evidence} == {"light.cucina", "light.piano"}
    assert "Lampadario" not in reply.speech


def test_temperature_summary_is_strictly_scoped_to_requested_area() -> None:
    temperatures = (
        entity(
            "sensor.cucina",
            "Temperatura cucina",
            "22",
            "°C",
            "temperature",
            area="Cucina",
        ),
        entity(
            "sensor.sala",
            "Temperatura sala",
            "20",
            "°C",
            "temperature",
            area="Sala",
        ),
    )

    reply = ConversationEngine().ask(
        "dimmi tutte le temperature in cucina", temperatures, now=NOW
    )

    assert reply.status is ReplyStatus.ANSWERED
    assert {item.entity_id for item in reply.evidence} == {"sensor.cucina"}
