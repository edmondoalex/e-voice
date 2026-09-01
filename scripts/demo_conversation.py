"""Esegue la demo conversazionale locale senza Alexa né Home Assistant reale."""

from datetime import UTC, datetime

from apps.cloud_api.app.conversation import ConversationSession, EntitySnapshot

DEMO_ENTITIES = (
    EntitySnapshot(
        "sensor.pv_power",
        "Produzione fotovoltaico",
        "sensor",
        "3.8",
        "kW",
        "power",
        aliases=("fotovoltaico", "pannelli solari"),
        observed_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
    ),
    EntitySnapshot(
        "sensor.acs_temperature",
        "Temperatura ACS",
        "sensor",
        "54",
        "°C",
        "temperature",
        aliases=("acqua calda", "bollitore"),
        observed_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
    ),
    EntitySnapshot(
        "sensor.storage_battery",
        "Accumulo fotovoltaico",
        "sensor",
        "72",
        "%",
        "battery",
        aliases=("batteria casa", "accumulo"),
        observed_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
    ),
    EntitySnapshot(
        "sensor.living_temperature",
        "Temperatura soggiorno",
        "sensor",
        "22.4",
        "°C",
        "temperature",
        area="soggiorno",
        observed_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
    ),
)


def main() -> None:
    session = ConversationSession()
    print("Demo Ekonex IA locale. Scrivi 'esci' per terminare.")
    while True:
        utterance = input("Tu: ").strip()
        if utterance.casefold() in {"esci", "quit", "exit"}:
            return
        reply = session.ask(
            utterance, DEMO_ENTITIES, now=datetime(2026, 9, 1, 8, 5, tzinfo=UTC)
        )
        print(f"Ekonex: {reply.speech}")
        if reply.evidence:
            source = reply.evidence[0]
            print(f"  fonte: {source.entity_id}, dato: {source.value} {source.unit or ''}".rstrip())


if __name__ == "__main__":
    main()
