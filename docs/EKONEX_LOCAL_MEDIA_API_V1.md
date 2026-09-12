# Ekonex Voice Local Media API v1

API interna per il collegamento `Home Assistant -> e-Voice -> e-Face X4`.
Espone esclusivamente i `media_player` selezionati nella configurazione Ekonex
Voice. Non sostituisce e non modifica l'API Media cloud esistente.

## Accesso dall'add-on

e-Face usa il proxy Supervisor verso Home Assistant Core:

```text
http://supervisor/core/api/evoice/media
Authorization: Bearer ${SUPERVISOR_TOKEN}
```

Il token resta nell'add-on e non deve essere restituito alla GUI o scritto nei
log. Le viste richiedono l'autenticazione nativa di Home Assistant.

## Endpoint

```text
GET  /snapshot
POST /players/{registry_id}/commands
GET  /players/{registry_id}/artwork
GET  /events
```

Lo snapshot contiene una voce per ogni player esposto, identificata dal
`registry_id` stabile. `entity_id` è solo informativo. Nome e stanza derivano
dal nome originale del player; eventuali personalizzazioni restano locali a
e-Face.

Il comando richiede un `request_id` UUID:

```json
{
  "request_id": "8cc03848-6143-4ea2-b2c6-7955325e9992",
  "operation": "media_play",
  "arguments": {}
}
```

Per gli Echo sono disponibili, se le rispettive entità correlate sono esposte:

```json
{"request_id":"UUID","operation":"tts","arguments":{"text":"Messaggio"}}
```

```json
{"request_id":"UUID","operation":"set_dnd","arguments":{"enabled":true}}
```

Il testo TTS deve contenere da 1 a 500 caratteri. Un comando non supportato
restituisce un errore JSON esplicito.

`/events` è uno stream SSE autenticato. Gli aggiornamenti sono aggregati in una
finestra nominale di 250 ms; un heartbeat viene inviato ogni 15 secondi senza
alterare lo stato.
