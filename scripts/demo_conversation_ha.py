"""Demo locale in sola lettura sulle entità reali di Home Assistant."""

from __future__ import annotations

import getpass
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from apps.cloud_api.app.conversation import ConversationReply, ConversationSession, EntitySnapshot

ALLOWLIST_PATH = Path(__file__).resolve().parents[1] / "config" / "assistant_entities.local.json"


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def snapshots_from_states(
    states: list[dict[str, Any]],
    mappings: dict[str, dict[str, Any]] | None = None,
) -> tuple[EntitySnapshot, ...]:
    """Converte la risposta REST di HA senza conservare attributi non necessari."""
    snapshots: list[EntitySnapshot] = []
    for item in states:
        entity_id = item.get("entity_id")
        if not isinstance(entity_id, str) or "." not in entity_id:
            continue
        domain = entity_id.split(".", 1)[0]
        if domain != "sensor":
            continue
        if mappings is not None and entity_id not in mappings:
            continue
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        state = item.get("state")
        mapping = mappings.get(entity_id, {}) if mappings is not None else {}
        mapped_name = mapping.get("name")
        raw_aliases = mapping.get("aliases", [])
        aliases = (
            tuple(alias for alias in raw_aliases if isinstance(alias, str))
            if isinstance(raw_aliases, list)
            else ()
        )
        snapshots.append(
            EntitySnapshot(
                entity_id=entity_id,
                name=(
                    mapped_name
                    if isinstance(mapped_name, str) and mapped_name.strip()
                    else str(attributes.get("friendly_name") or entity_id)
                ),
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
                aliases=aliases,
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
        mappings = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"Lista autorizzata mancante o non valida: {ALLOWLIST_PATH}")
        return
    if not isinstance(mappings, dict) or not mappings:
        print("La lista autorizzata è vuota. Prova terminata.")
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
    entities = snapshots_from_states(payload, mappings)
    print(
        f"Caricati {len(entities)} sensori autorizzati su {len(mappings)} configurati. "
        "Nessun altro sensore verrà usato e nessun comando verrà inviato."
    )
    session = ConversationSession()
    last_reply: ConversationReply | None = None
    print("Scrivi 'dettagli' per vedere la fonte dell'ultima risposta o 'esci' per terminare.")
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
        reply = session.ask(utterance, entities)
        last_reply = reply
        print(f"Ekonex: {reply.speech}")


if __name__ == "__main__":
    main()
