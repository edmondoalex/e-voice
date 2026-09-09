# Current work status

## Ripresa — 9 settembre 2026

- Implementata localmente la prima versione end-to-end delle fonti Media Player: sincronizzazione
  `source`/`source_list`, configurazione persistente di inclusione, nome e alias, selezione dal
  portale, comando EVCP chiuso e mapping Home Assistant.
- Predisposta l'esposizione Alexa `InputController` usando soltanto le fonti abilitate e
  riconducendo nomi e alias al valore tecnico esatto di Home Assistant.
- Aggiunta la migrazione `20260909_0019`; produzione e Laboratorio distribuito non sono stati
  modificati.
- Prossimo gate: completare la validazione Linux/HAOS, creare una nuova beta del Connector e
  distribuire il backend esclusivamente nel Laboratorio prima della prova su `TV Sala`.

## Checkpoint pre-blackout — 7 settembre 2026

Questo checkpoint prevale sulle sezioni storiche sottostanti quando descrivono funzioni ormai
completate. È stato preparato prima dell'interruzione di corrente prevista per la mattina dell'8
settembre 2026.

### Versioni e distribuzioni attive

- Branch di sviluppo: `agent/conversation-core-demo`.
- Ultimo commit: `78687d2` (`feat: expose media players to Alexa laboratory`).
- Backend Laboratorio attivo: immagine Docker `e-voice-lab:78687d2`, container
  `e-voice-lab-api`, porta VPS `127.0.0.1:8001`.
- Componente Home Assistant pubblicato su HACS: `0.1.8-beta.21`.
- La produzione e la Smart Home Skill in certificazione non sono state modificate.

### Funzioni completate e verificate

- Inventario portale senza il precedente limite nascosto di 50 entità.
- `TV Sala` visibile nella sezione Media Player anche senza ricerca.
- Controlli Media Player dal portale: accensione/spegnimento, volume, muto, play, pausa, stop e
  traccia precedente/successiva, mostrati soltanto quando supportati dall'entità Home Assistant.
- Catena Portale Lab → VPS → Connector Home Assistant verificata con più comandi HTTP `200 OK`.
- Routine vocali con Echo singolo, gruppi configurabili e gruppo automatico Ovunque.
- Annunci e modalità parla, volume percentuale con ripristino ritardato, condizioni, messaggi con
  variabili Home Assistant, storico e destinatario ultimo Echo utilizzato.
- Categorie vocali dinamiche, distinzione temperature ambiente/centrale termica e generazione,
  validazione, pubblicazione e Build del modello Custom Alexa dal portale.

### Diagnosi Alexa Media Player

- Il codice Lab è pronto a pubblicare `media_player` nella Discovery Smart Home con categoria TV,
  PowerController, Speaker e PlaybackController in base alle capacità reali.
- I 105 test Alexa del modulo `test_alexa.py` passano.
- `TV Sala` non compare ancora nell'app Alexa perché la Smart Home Skill attuale usa la Lambda
  `ekonex-voice`, che inoltra le Discovery alla produzione (`voice.e-control.tech`).
- La prova del 7 settembre è arrivata nei log del container produzione `e-voice-api-1`, non nel
  container Laboratorio. Non cambiare temporaneamente quella Lambda: è usata dalla Skill in
  certificazione.
- Prossimo requisito: Smart Home Skill privata di laboratorio e Lambda separata
  `ekonex-voice-lab`, indirizzata a `https://voice-lab.e-control.tech`.

### Priorità concordate

1. Aggiornare e mantenere allineata la documentazione (questo checkpoint).
2. Fonti audio/video personalizzabili per singolo Media Player e quindi stanza per stanza:
   leggere `source_list` da Home Assistant, consentire selezione/esclusione, nome vocale e alias
   per ogni fonte, comando portale e successiva esposizione Alexa InputController.
3. Rafforzare la comprensione di richieste per piano, stanza, categoria e singola entità.
4. Creare la Smart Home Skill/Lambda Lab separata e collaudare TV e fonti con la voce.
5. Funzioni evolute, iniziando da riepilogo intelligente della casa, Energy Copilot e diagnostica
   installatore; seguono anomalie, notifiche multicanale, modalità casa, conferme vocali, scene,
   diario evoluto e Wear OS.

### Ripartenza dopo interruzione di corrente

1. Verificare Home Assistant e attendere che tutte le integrazioni siano caricate.
2. Controllare in HACS/manifest che Ekonex Voice sia `0.1.8-beta.21` e che la ConfigEntry Lab sia
   connessa.
3. Sulla VPS controllare `docker ps`, `docker inspect e-voice-lab-api` e
   `curl http://127.0.0.1:8001/health`; l'immagine attesa è `e-voice-lab:78687d2`.
4. Nel portale Lab verificare che l'impianto sia online e provare un comando non critico su
   `TV Sala`.
5. Controllare che la produzione sia ancora sull'immagine precedente e non distribuire in
   produzione alcuna modifica Lab.
6. Riprendere dalla progettazione/persistenza delle fonti personalizzate per Media Player.

### Stato locale da preservare

Il worktree contiene modifiche e archivi non correlati ancora non tracciati o non committati. Non
usare pulizie distruttive, non cancellare gli ZIP e aggiungere ai commit soltanto i file pertinenti.
Le modifiche conversazionali locali devono essere riesaminate prima di includerle in altri commit.

Last updated: 2026-08-31 (Europe/Rome)

## Conversational AI local test (2026-09-01)

- Development continues on `agent/conversation-core-demo`; the withdrawn PlaybackController
  removal is reverted on this branch and must not be deployed.
- A channel-independent, read-only conversation core now answers a bounded Italian demo for
  photovoltaic power, ACS temperature, batteries, and room temperature.
- The core returns evidence, detects ambiguity/unavailable/stale data, and cannot execute commands.
- `scripts/demo_conversation.py` provides an offline interactive test; the focused suite contains
  eight passing tests. Alexa production and the skill under certification are untouched.
- The read-only application service can now build conversational snapshots from persisted,
  installation-scoped entities. It rejects cross-tenant installation IDs and omits deleted
  entities. Sensor synchronization is prepared to include bounded `unit_of_measurement` and
  `state_class` metadata. This work remains local and undeployed.
- The installation portal now groups entities into collapsed domain panels (sensors, lights,
  covers, climate, and other types) with per-group counts. Opening a panel reveals its existing
  entity table and controls; all groups start compact. The complete 34-test admin-console suite
  passes. This user-interface change is local and does not alter Alexa Discovery.

## Alexa certification and support case

- The Ekonex Voice Smart Home skill is under Amazon certification.
- Amazon Developer Support case: `21738300901`.
- Amazon traced the Italian utterance tested at 2026-08-27 16:22 UTC. The endpoint was
  resolved correctly, but interpretation selected a value-setting path and emitted no directive.
- Amazon withdrew its earlier claims about whether Discovery semantics were present in or read by
  its device registry.
- The Developer Advocate requested removal of `Alexa.PlaybackController` from blind endpoints,
  followed by rediscovery with only one blind enabled and this exact utterance:
  `Alexa, apri le tapparelle` (no device name).
- The test result must include the exact UTC timestamp, Alexa's spoken response, and whether a
  directive reached the backend.
- We asked Amazon whether the capability change may be deployed while certification is in progress
  or must wait until review completes. Amazon acknowledged receipt; no substantive reply has
  arrived yet.
- Do not deploy, merge to `main`, or resynchronize the production Alexa endpoints until Amazon
  answers. Production and the certification target remain unchanged.

### Prepared correction

- Branch: `agent/remove-cover-playback-controller`
- Commit: `1c275c1` (`fix(alexa): remove playback controller from covers`)
- The branch removes `Alexa.PlaybackController` from cover Discovery while retaining legacy
  directive handling during propagation.
- Cover stop remains available through `Alexa.ModeController` where supported.
- Four focused tests pass; Ruff lint and formatting checks pass.
- The complete local suite cannot run on Windows because the Home Assistant pytest plugin imports
  the Unix-only `fcntl` module. CI on Linux remains the full verification gate.

## Wear OS direction

- Alexa remains the home voice channel, but the smartwatch integration should be a direct Wear OS
  application so it does not depend on Alexa Smart Home interpretation or certification.
- Initial Ekonex Voice watch experience:
  - launch the app or a Tile;
  - tap a microphone button;
  - use Wear OS speech recognition;
  - send recognized text to the authenticated Ekonex Voice backend;
  - resolve and execute the command through the existing Home Assistant Connector/EVCP path;
  - show a result and provide haptic confirmation;
  - offer favorite-device and scene shortcuts.
- A normal Wear OS app cannot register a general system-wide `Hey Ekonex` wake phrase. The user
  launches the app, Tile, or shortcut before speaking.

### Test hardware decision

- Preferred economical development device: Xiaomi Watch 2, ASIN `B0CTMTXNF9`.
- Confirmed features relevant to the project: Google Wear OS, Google Play, microphone, Wi-Fi,
  Snapdragon W5+ Gen 1, 2 GB RAM, and 32 GB storage.
- The discussed Amazon offer was approximately EUR 99.99, new, sold and fulfilled by Amazon. Check
  condition, seller, color, and current price again before purchase.
- Xiaomi Watch 5 is the stronger but more expensive alternative, with longer battery life and newer
  gesture support.
- Avoid Redmi Watch and Xiaomi Watch S-series models for this project because they use Xiaomi's
  proprietary platform rather than standard Wear OS app distribution.

### Installation and distribution

- Development does not require publishing the app.
- Install development APKs directly with ADB over Wi-Fi from a PC, or manually from an Android
  phone using an ADB-wireless installer. Developer options must be enabled on the watch.
- A phone companion app cannot silently install the watch APK; Android prevents that for security.
- If later distributed through Google Play, the app can remain free to users. A Play Console
  developer account currently has a one-time USD 25 registration fee.
- EA SAS should use an Organization developer account, which requires organization verification
  and a D-U-N-S number.
- Play Store publication is deferred until distribution to customers and automatic updates become
  necessary.

## Next actions

1. Wait for Amazon's answer on case `21738300901` without changing production Alexa Discovery.
2. When Amazon replies, decide whether to deploy the prepared branch during or after certification.
3. After an authorized deploy, rediscover and run the exact requested Italian utterance test.
4. When the Xiaomi Watch 2 is available, enable wireless debugging and install the first private
   Ekonex Voice Wear OS prototype without using the Play Store.

## Conversational AI design

- Detailed product examples and the proposed differentiated `Ekonex Turbo` direction are recorded
  in `docs/EKONEX_AI_CONVERSATION_DEMO.md`.
- The first conversational implementation should target Wear OS, where Ekonex receives recognized
  text directly. Alexa conversational support should be a later Multi-Capability version and must
  not modify the skill currently under certification.
- Selected Home Assistant sensor entities already synchronize their state to the cloud, but sensor
  units and other safe measurement metadata must be added before trustworthy answers about PV
  production, ACS temperature, batteries, energy, and environment.
- Product direction: evidence-backed answers, energy copilot, observed command outcomes,
  installation diagnostics, explicit visibility scope, personal aliases, and installer-authorized
  support. AI explains and resolves language; deterministic code performs calculations, validates
  commands, and enforces safety.
- Long-term Alexa direction: map approved Home Assistant triggers and real state changes to official
  Alexa sensor/security interfaces and proactive `ChangeReport` events. Customer-configured Alexa
  Routines may announce selected events. Alexa must not be treated as an unrestricted text-to-speech
  endpoint. Gate/door feedback must distinguish command acceptance from a physically observed
  sensor result; alarms require deterministic source mappings and multiple delivery channels.
