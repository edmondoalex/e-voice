"""Esegue la demo conversazionale locale senza Alexa né Home Assistant reale."""

from datetime import UTC, datetime

from apps.cloud_api.app.conversation import ConversationReply, ConversationSession, EntitySnapshot

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
    last_reply: ConversationReply | None = None
    print("Demo Ekonex IA locale. Scrivi 'dettagli' per la fonte o 'esci' per terminare.")
    while True:
        utterance = input("Tu: ").strip()
        if utterance.casefold() in {"esci", "quit", "exit"}:
            return
        if utterance.casefold() == "dettagli":
            if last_reply is None or not last_reply.evidence:
                print("Ekonex: Non ci sono dettagli disponibili per l'ultima risposta.")
            else:
                source = last_reply.evidence[0]
                print(
                    f"Ekonex: Fonte {source.entity_id}, dato {source.value} "
                    f"{source.unit or ''}.".replace(" %.", "%.")
                )
            continue
        reply = session.ask(
            utterance, DEMO_ENTITIES, now=datetime(2026, 9, 1, 8, 5, tzinfo=UTC)
        )
        last_reply = reply
        print(f"Ekonex: {reply.speech}")


if __name__ == "__main__":
    main()
