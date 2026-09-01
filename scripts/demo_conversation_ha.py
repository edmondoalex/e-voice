"""Demo locale in sola lettura sulle entità reali di Home Assistant."""

from __future__ import annotations

import getpass
from datetime import datetime
from typing import Any

import httpx

from apps.cloud_api.app.conversation import ConversationSession, EntitySnapshot


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def snapshots_from_states(states: list[dict[str, Any]]) -> tuple[EntitySnapshot, ...]:
    """Converte la risposta REST di HA senza conservare attributi non necessari."""
    snapshots: list[EntitySnapshot] = []
    for item in states:
        entity_id = item.get("entity_id")
        if not isinstance(entity_id, str) or "." not in entity_id:
            continue
        domain = entity_id.split(".", 1)[0]
        if domain != "sensor":
            continue
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        state = item.get("state")
        snapshots.append(
            EntitySnapshot(
                entity_id=entity_id,
                name=str(attributes.get("friendly_name") or entity_id),
                domain=domain,
                state=state if isinstance(state, str) else None,
                unit=(
                    str(attributes["unit_of_measurement"])
                    if isinstance(attributes.get("unit_of_measurement"), str)
                    else None
                ),
                device_class=(
                    str(attributes["device_class"])
                    if isinstance(attributes.get("device_class"), str)
                    else None
                ),
                available=state not in {"unknown", "unavailable", None},
                observed_at=_timestamp(item.get("last_updated")),
            )
        )
    return tuple(snapshots)


def main() -> None:
    print("Demo Ekonex su Home Assistant reale, esclusivamente in lettura.")
    base_url = input("Indirizzo HA [http://homeassistant.local:8123]: ").strip()
    base_url = (base_url or "http://homeassistant.local:8123").rstrip("/")
    token = getpass.getpass("Token HA (non verrà mostrato né salvato): ").strip()
    if not token:
        print("Token mancante. Prova terminata.")
        return

    try:
        response = httpx.get(
            f"{base_url}/api/states",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        print(f"Connessione non riuscita: {type(error).__name__}. Il token non è stato salvato.")
        return
    finally:
        token = ""

    if not isinstance(payload, list):
        print("Home Assistant ha restituito un formato inatteso.")
        return
    entities = snapshots_from_states(payload)
    print(f"Caricati {len(entities)} sensori reali. Nessun comando verrà inviato.")
    session = ConversationSession()
    while True:
        utterance = input("Tu: ").strip()
        if utterance.casefold() in {"esci", "quit", "exit"}:
            return
        reply = session.ask(utterance, entities)
        print(f"Ekonex: {reply.speech}")
        if reply.evidence:
            source = reply.evidence[0]
            print(f"  fonte: {source.entity_id}, dato: {source.value} {source.unit or ''}".rstrip())


if __name__ == "__main__":
    main()
