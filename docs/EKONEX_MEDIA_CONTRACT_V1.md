# Contratto interno Ekonex Media v1

Stato: **PRONTO PER REVISIONE FINALE**
Flusso: **Componente Home Assistant → Backend Ekonex Media → e‑Face X4**

Questo è un contratto interno. Il componente fornisce dati e comandi affidabili; il backend applica sicurezza, stato e orchestrazione; e‑Face realizza esclusivamente l’interfaccia.

## 1. Regole fondamentali

- Un player è identificato da `installation_id + registry_id`.
- `entity_id` è informativo e può cambiare.
- Il tenant deriva sempre dall’utente autenticato e non dal payload.
- Il backend verifica impianto, player e tutti i membri del gruppo a ogni richiesta.
- Sono accettate solo le operazioni definite qui; nessun servizio Home Assistant arbitrario.
- Massimo 64 membri per gruppo.
- Nessun coordinatore viene dedotto dal primo `group_members`.
- In assenza di un coordinatore affidabile si mostra `Player selezionato` o `Gruppo multimediale`.
- Gli aggiornamenti del componente sono raggruppati in una finestra nominale di 250 ms.
- e‑Face non riceve token Home Assistant, URL firmati, `access_token` o copertine Base64.
- I player senza `area_id` restano nel backend ma non sono restituiti a e‑Face.

## 2. Revisioni

Sono separate due revisioni:

- `installation_revision`: revisione globale monotona dell’impianto. Aumenta per ogni insieme di cambiamenti applicativi pubblicato.
- `resource_revision`: revisione monotona del singolo player o gruppo. Aumenta solo quando cambia quella risorsa.

Un evento applicativo può aggiornare più risorse con una sola `installation_revision`; ciascuna risorsa modificata incrementa la propria `resource_revision`.

L’heartbeat non modifica nessuna revisione.

I comandi contengono:

```json
{
  "request_id": "uuid",
  "operation": "media_pause",
  "expected_resource_revision": null,
  "arguments": {}
}
```

`expected_resource_revision` è opzionale. È obbligatorio soltanto per:

- `media_join`;
- `media_unjoin`;
- `set_group_volume`;
- altri comandi futuri che dipendano dalla composizione del gruppo.

Una revisione non corrente produce `conflict` senza eseguire il comando.

## 3. Player

```json
{
  "installation_id": "uuid",
  "registry_id": "abc123",
  "entity_id": "media_player.ufficio_alex",
  "name": "Ufficio Alex",
  "area": {"id": "ufficio", "name": "Ufficio"},
  "experiences": ["listen"],
  "state": "playing",
  "availability": "available",
  "connection_status": "online",
  "media": {
    "title": "Titolo",
    "artist": "Artista",
    "album": "Album",
    "duration_seconds": 245,
    "content_fingerprint": "sha256:..."
  },
  "source": "Spotify Connect",
  "source_list": ["Spotify Connect", "TuneIn"],
  "volume_percent": 46,
  "muted": false,
  "group": {
    "group_id": "grp_v1_...",
    "member_registry_ids": ["abc123", "def456"],
    "coordinator_registry_id": null,
    "completeness": "complete"
  },
  "capabilities": {
    "play": true,
    "pause": true,
    "stop": true,
    "next": true,
    "previous": true,
    "set_volume": true,
    "mute": true,
    "select_source": true,
    "grouping": true,
    "artwork": true
  },
  "supported_features_raw": 0,
  "last_changed_at": "2026-09-11T10:00:00Z",
  "last_updated_at": "2026-09-11T10:00:02Z",
  "observed_at": "2026-09-11T10:00:02.250Z",
  "resource_revision": 18
}
```

`experiences` contiene esclusivamente `watch`, `listen` oppure entrambi. Il componente HA
propone una classificazione iniziale prudente; la scelta manuale salvata dall'utente ha sempre
priorità. Un player non viene classificato automaticamente in entrambe le esperienze senza un
segnale affidabile.

Snapshot e lista player includono inoltre:

```json
{
  "media_rooms": {
    "watch": [{"area_id": "sala", "name": "Sala"}],
    "listen": [{"area_id": "ufficio", "name": "Ufficio"}]
  }
}
```

Una stanza compare soltanto se contiene almeno un player esposto con quell'esperienza.

Condizioni distinte:

- `availability`: `available`, `unavailable`, `unknown`;
- `connection_status`: `online`, `degraded`, `offline`.

## 4. Gruppi

### 4.1 Identità

Il backend:

1. verifica tutti i `registry_id` nello stesso impianto;
2. elimina duplicati;
3. ordina i membri lessicograficamente;
4. include `installation_id`;
5. genera un hash SHA-256 stabile.

```text
canonical = "v1\n" + installation_id + "\n" + join("\n", sort(unique(registry_ids)))
group_id = "grp_v1_" + base64url(sha256(canonical))
```

L’ordine ricevuto non modifica `group_id`.

### 4.2 Gruppi incoerenti o incompleti

`completeness` assume:

- `complete`: tutti i membri esistono, sono esposti e concordano sulla composizione;
- `incomplete`: uno o più membri non sono ancora presenti nello snapshot;
- `inconsistent`: i player dichiarano composizioni differenti;
- `unavailable`: la composizione è nota ma almeno un membro non è disponibile.

Regole:

- il backend non inventa membri mancanti né coordinatori;
- un gruppo non `complete` resta visibile con avviso;
- `media_join`, `media_unjoin` e `set_group_volume` sono rifiutati se il gruppo non è `complete`;
- dopo 5 secondi di incoerenza persistente il backend richiede un nuovo snapshot;
- il ripristino avviene solo dopo uno snapshot coerente o eventi concordanti;
- e‑Face non modifica localmente la composizione prima del risultato definitivo.

## 5. API REST

Base path: `/api/media/v1`

| Metodo | Endpoint | Risultato |
|---|---|---|
| GET | `/installations/{installation_id}/snapshot` | Stato completo, revisioni, player e gruppi |
| GET | `/installations/{installation_id}/players` | Player esposti |
| GET | `/installations/{installation_id}/groups` | Gruppi correnti |
| POST | `/installations/{installation_id}/players/{registry_id}/commands` | Invia comando player |
| POST | `/installations/{installation_id}/groups/{group_id}/commands` | Invia comando gruppo |
| GET | `/installations/{installation_id}/commands/{request_id}` | Recupera risultato idempotente |
| GET | `/installations/{installation_id}/players/{registry_id}/artwork?fingerprint=...` | Copertina binaria |
| GET | `/installations/{installation_id}/favorites` | Preferiti già configurati |
| POST | `/installations/{installation_id}/favorites/{favorite_id}/play` | Riproduce preferito |
| GET | `/installations/{installation_id}/events` | Stream SSE |

La Media API pubblica v1 non espone CRUD dei preferiti. Creazione, modifica, IP, porta e mapping Control4 appartengono alle impostazioni amministrative del backend.

## 6. Comandi tipizzati

Ogni payload ammette solo i campi dichiarati.

### Senza argomenti

Operazioni: `media_play`, `media_pause`, `media_stop`, `media_next`, `media_previous`, `volume_mute`, `volume_unmute`, `media_unjoin`.

```json
{
  "request_id": "uuid",
  "operation": "media_pause",
  "arguments": {},
  "expected_resource_revision": null
}
```

Per `media_unjoin`, `expected_resource_revision` deve contenere la revisione del gruppo corrente.

### Volume player

```json
{
  "request_id": "uuid",
  "operation": "set_volume",
  "arguments": {"volume_percent": 52},
  "expected_resource_revision": null
}
```

`volume_percent`: intero 0–100.

### Sorgente

```json
{
  "request_id": "uuid",
  "operation": "select_source",
  "arguments": {"source": "Spotify Connect"},
  "expected_resource_revision": null
}
```

La sorgente deve essere presente nella `source_list` corrente.

### Join multiplo

Il player nel path è il player selezionato; non viene dichiarato coordinatore.

```json
{
  "request_id": "uuid",
  "operation": "media_join",
  "arguments": {
    "member_registry_ids": ["def456", "ghi789"]
  },
  "expected_resource_revision": 18
}
```

- Da 1 a 63 membri aggiuntivi, massimo 64 complessivi.
- Duplicati e player del path vengono rifiutati.
- Tutti i membri devono essere esposti, disponibili, nello stesso impianto e supportare grouping.
- Il risultato contiene un elemento per ogni membro richiesto.

### Volume gruppo relativo

```json
{
  "request_id": "uuid",
  "operation": "set_group_volume",
  "arguments": {"volume_percent": 52},
  "expected_resource_revision": 9
}
```

- Il volume generale corrente è la media aritmetica dei volumi noti.
- Lo stesso delta viene applicato a ogni membro con clamp 0–100.
- Il mute rimane invariato.
- Prima dell’esecuzione la richiesta è rifiutata se un membro è offline, indisponibile, ha volume ignoto o non supporta `set_volume`.
- Un errore sopraggiunto durante l’esecuzione produce `partial_failure`; non viene eseguito rollback automatico.

## 7. Risultati

### 7.1 Risultato player

```json
{
  "request_id": "uuid",
  "kind": "player_command_result",
  "status": "success",
  "operation": "media_pause",
  "installation_id": "uuid",
  "registry_id": "abc123",
  "accepted_at": "2026-09-11T10:00:03Z",
  "completed_at": "2026-09-11T10:00:03.200Z",
  "resulting_resource_revision": 19,
  "error": null
}
```

### 7.2 Risultato gruppo

```json
{
  "request_id": "uuid",
  "kind": "group_command_result",
  "status": "partial_failure",
  "operation": "set_group_volume",
  "installation_id": "uuid",
  "group_id": "grp_v1_...",
  "requested_percent": 52,
  "accepted_at": "2026-09-11T10:00:03Z",
  "completed_at": "2026-09-11T10:00:04Z",
  "resulting_resource_revision": 10,
  "members": [
    {
      "registry_id": "abc123",
      "previous_percent": 40,
      "target_percent": 46,
      "status": "success",
      "error_code": null
    },
    {
      "registry_id": "def456",
      "previous_percent": 52,
      "target_percent": 58,
      "status": "failed",
      "error_code": "SERVICE_CALL_FAILED"
    }
  ]
}
```

Per `media_join`, ogni membro usa `status: success|failed|skipped`; il risultato include anche `resulting_group_id` e l’elenco finale dei membri osservati.

Stati ammessi:

- `accepted`;
- `success`;
- `partial_failure`;
- `rejected`;
- `timeout`;
- `offline`;
- `unsupported`;
- `conflict`.

### 7.3 Idempotenza

- e‑Face genera `request_id`; backend e componente lo propagano invariato.
- Stesso `request_id` e stesso payload: stesso risultato, senza nuova esecuzione.
- Stesso `request_id` con payload diverso: `409 IDEMPOTENCY_KEY_REUSED`.
- Il backend conserva il risultato per almeno 24 ore.
- `GET /commands/{request_id}` restituisce `202 accepted` se pendente e `200` quando definitivo.

## 8. Realtime

Trasporto principale: SSE. Polling leggero soltanto come recupero, mai uno per player.

### 8.1 Connessione sicura

- Autenticazione tramite cookie di sessione `HttpOnly`, `Secure`, `SameSite=Strict`, oppure header `Authorization` quando supportato dal client.
- Nessun token nella query string.
- Verifica `Origin` e autorizzazione tenant/impianto prima di aprire lo stream.
- `Last-Event-ID` è ammesso come header.

### 8.2 Avvio e recupero

1. e‑Face richiede lo snapshot e memorizza `installation_revision`.
2. Apre lo stream indicando `Last-Event-ID` o `after_revision` come header, non come token.
3. Applica solo revisioni consecutive.
4. In caso di salto chiede il recupero eventi.
5. Se il recupero non è disponibile, riceve `snapshot.required` e scarica un nuovo snapshot.
6. Con realtime interrotto può richiedere lo snapshot ogni 60 secondi.

### 8.3 Envelope comune

```json
{
  "event_id": "uuid",
  "installation_id": "uuid",
  "installation_revision": 125,
  "type": "player.updated",
  "occurred_at": "2026-09-11T10:00:02Z",
  "observed_at": "2026-09-11T10:00:02.250Z",
  "data": {}
}
```

### 8.4 Schemi evento

| `type` | Campi obbligatori in `data` |
|---|---|
| `snapshot.required` | `reason`, `current_installation_revision` |
| `installation.connection_changed` | `connection_status` |
| `player.created` | `player` completo |
| `player.updated` | `registry_id`, `resource_revision`, `changed_fields`, `player` completo |
| `player.removed` | `registry_id`, `resource_revision` |
| `group.created` | `group` completo |
| `group.updated` | `group_id`, `resource_revision`, `changed_fields`, `group` completo |
| `group.removed` | `group_id`, `resource_revision` |
| `command.accepted` | `request_id`, `kind`, `operation` |
| `command.completed` | `request_id`, `result` tipizzato player o gruppo |
| `favorite.updated` | `favorite` completo |
| `heartbeat` | `connection_status`, `server_time` |

Ogni tipo viene validato con schema specifico (`oneOf` discriminato da `type`); `data` generico non è accettato.

Heartbeat:

- ogni 15 secondi;
- non incrementa `installation_revision` né `resource_revision`;
- stream degradato dopo 45 secondi senza heartbeat;
- impianto offline dopo 90 secondi, salvo disconnessione esplicita precedente.

## 9. Copertine

Il componente può trasportare Base64 soltanto internamente su EVCP. Il backend lo decodifica in memoria e non lo salva in database, audit, diagnostica o log.

Validazione obbligatoria backend:

- limite iniziale 700 KB dopo decodifica;
- MIME ammessi: `image/jpeg`, `image/png`, `image/webp`, `image/gif`;
- SVG vietato;
- verifica magic bytes coerenti con il MIME;
- rifiuto di contenuto troncato o non decodificabile;
- timeout esplicito;
- fingerprint calcolato da ID contenuto stabile oppure titolo, artista, album e durata normalizzati;
- fingerprint senza token o URL firmati;
- nuova verifica del fingerprint dopo il caricamento.

Risposte:

- `200`: binario con `ETag` e `Cache-Control: private, max-age=300`;
- `304`: ETag invariato;
- `404`: copertina assente;
- `409 ARTWORK_STALE`: brano cambiato;
- `413`: contenuto troppo grande;
- `415`: MIME o firma non ammessi;
- `504`: timeout.

## 10. Preferiti

- I preferiti sono creati e modificati esclusivamente nelle impostazioni del backend.
- La Media API v1 permette soltanto elenco e riproduzione.
- `favorite_id` è stabile e generato dal backend.
- Origine ammessa: `control4_http`, `home_assistant`, `backend`.
- Il backend conserva mapping autorizzato, stanza, preferito, IP e porta; e‑Face non invia URL arbitrari.
- Il target può essere un player o gruppo dello stesso impianto.
- Preferito non più disponibile: resta visibile con `available=false`; play restituisce `FAVORITE_UNAVAILABLE`.

## 11. Sicurezza e limiti

- Autorizzazione su ogni risorsa, non soltanto sull’URL dell’impianto.
- Doppia verifica backend e componente per membri e player esposti.
- Risorse di altri tenant possono rispondere 404 per evitare enumerazione.
- Massimo 64 membri e stringhe/payload limitati.
- Rate limit iniziale: 10 comandi/s per impianto, burst 20; artwork 5 richieste/s per utente.
- Nessun retry di comandi mutanti senza lo stesso `request_id`.
- Audit: tenant, impianto, request ID, operazione, target, esito e tempi.
- Audit escluso per token, URL firmati, Base64 e byte delle immagini.

## 12. Proprietà dei campi

| Campo | Componente HA | Backend Ekonex Media | e‑Face X4 |
|---|---|---|---|
| `installation_id` | produce dalla sessione | verifica | consuma |
| `tenant_id` | non riceve | produce dall’autenticazione | non sceglie |
| `registry_id` | produce | verifica e conserva | usa come target |
| `experiences` | propone e salva la scelta manuale | conserva e restituisce | filtra Watch/Listen |
| `media_rooms` | alimenta tramite area ed esperienze | calcola | consuma |
| `entity_id` | produce, informativo | conserva | visualizza soltanto |
| stato, availability, media | produce | conserva | visualizza |
| `connection_status` | segnala connessione | normalizza | visualizza |
| volume, mute, source | produce | conserva e valida | visualizza/comanda |
| `group_members` | produce senza leader inventato | verifica | visualizza |
| `group_id`, completeness | non produce | calcola | consuma |
| coordinatore | produce solo se affidabile | non deduce | neutro se assente |
| capability player | produce da HA | normalizza | abilita/disabilita controlli |
| capability connettore | produce | conserva | usa per compatibilità |
| `last_changed_at`, `last_updated_at` | produce | conserva | opzionale |
| `observed_at` | non autorevole | produce | consuma |
| `installation_revision` | alimenta il flusso | assegna/autorevole | controlla sequenza |
| `resource_revision` | alimenta il flusso | assegna/autorevole | usa per conflitti |
| `request_id` | propaga/deduplica | conserva/deduplica | genera |
| risultato fisico | produce | normalizza | consuma |
| artwork Base64 | produce temporaneamente | decodifica senza persistere | non riceve |
| artwork binario/ETag | non produce | produce | consuma |
| definizione preferito | eventuale origine | autorevole | sola lettura/play |

## 13. Codici errore minimi

- `INVALID_ARGUMENT`
- `PLAYER_NOT_EXPOSED`
- `MEMBER_NOT_FOUND`
- `GROUP_INCOMPLETE`
- `GROUP_INCONSISTENT`
- `GROUP_CHANGED`
- `OPERATION_NOT_SUPPORTED`
- `INSTALLATION_OFFLINE`
- `PLAYER_UNAVAILABLE`
- `REVISION_CONFLICT`
- `IDEMPOTENCY_KEY_REUSED`
- `SERVICE_CALL_FAILED`
- `FAVORITE_UNAVAILABLE`
- `ARTWORK_STALE`
- `ARTWORK_TOO_LARGE`
- `ARTWORK_INVALID_TYPE`
- `CONNECTOR_TIMEOUT`

## 14. Condizione di avvio implementazione

L’implementazione inizierà solo dopo approvazione finale di questo contratto e definizione dell’ordine di lavoro fra componente Home Assistant, backend Ekonex Media ed e‑Face X4.
