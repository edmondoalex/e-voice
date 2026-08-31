# Ekonex AI conversation demo

Status: product and interaction design demo, not an implemented feature.

This document illustrates how one central Ekonex conversational service could serve Wear OS first
and later Alexa Multi-Capability, mobile, web, Google, and Siri channels. Examples are in Italian
because the initial target locale is `it-IT`.

## Product outcome

The assistant accepts natural language, resolves only entities authorized for the current Ekonex
installation, reads synchronized state, and proposes or executes a bounded operation. The language
model never sends arbitrary Home Assistant service calls. It produces a candidate structured
request that the deterministic Ekonex policy and command layers validate.

```text
voice
  -> channel speech recognition
  -> Ekonex conversational service
  -> entity and context resolution
  -> authorization and safety policy
  -> read-only answer OR validated EVCP command
  -> Home Assistant Connector
  -> result, spoken/text reply, and audit event
```

## Channel behavior

### Wear OS

Wear OS is the preferred first channel. The user opens the app or Tile, taps the microphone, and
speaks naturally. Ekonex receives recognized text, so it can manage the complete conversation.

### Alexa Smart Home

Normal Smart Home utterances are interpreted by Amazon before Ekonex is called. Ekonex cannot use
AI when Amazon emits no directive. A future Alexa Multi-Capability model could provide explicit
conversations such as `Alexa, chiedi a Ekonex...`. This must be developed as a later version and
must not alter the skill currently under certification.

## Read-only sensor questions

Yes, the assistant can be designed to answer questions about selected Home Assistant sensors,
including photovoltaic production, energy, hot-water temperature, batteries, air quality, weather,
consumption, and connectivity. Only entities deliberately exposed to the Ekonex Connector are
eligible.

### Photovoltaic production

**User:** Quanta energia sta producendo il fotovoltaico?

**Ekonex:** In questo momento il fotovoltaico sta producendo 3,8 kW.

**User:** E quanto stiamo consumando?

**Ekonex:** La casa sta consumando 2,1 kW. Stai immettendo circa 1,7 kW in rete.

**User:** Quanto ha prodotto oggi?

**Ekonex:** Oggi l'impianto ha prodotto 18,6 kWh.

**User:** È una giornata migliore di ieri?

**Ekonex:** Alle 14:30 oggi hai prodotto 2,4 kWh in più rispetto alla stessa ora di ieri.

The last example requires retained history or statistics; a current-value sensor alone is not
enough.

### Storage battery

**User:** A quanto è la batteria di casa?

**Ekonex:** La batteria di accumulo è al 72% e sta caricando a 1,2 kW.

**User:** Quanto può durare?

**Ekonex:** Con il consumo attuale, la stima è di circa 4 ore. È una stima e può cambiare con i
carichi della casa.

**User:** Avvisami quando scende sotto il 20%.

**Ekonex:** Posso creare un avviso per questa installazione. Confermi la soglia del 20%?

The duration answer requires capacity and power sensors plus a transparent estimation formula. The
alert requires a separate notification/automation feature; it must not be promised from one sensor
value alone.

### Domestic hot water (ACS)

**User:** Qual è la temperatura dell'acqua calda?

**Ekonex:** La temperatura ACS è 54 °C.

**User:** È sufficiente per fare la doccia?

**Ekonex:** La temperatura è sopra la soglia informativa di 45 °C che hai configurato. Non posso
garantire la quantità di acqua disponibile senza un sensore o un modello dell'accumulo.

**User:** Porta l'acqua calda a 58 gradi.

**Ekonex:** Il sensore ACS è di sola lettura. Non trovo un controllo autorizzato per modificare il
setpoint.

If a separate authorized `number`, `water_heater`, or other control entity exists, Ekonex may offer
that command after validation. A sensor must never be treated as an actuator.

### Device batteries

**User:** Ci sono batterie quasi scariche?

**Ekonex:** Sì. Il sensore della porta garage è al 12% e il termostato della camera è al 18%.

**User:** E la batteria del cancello?

**Ekonex:** Non trovo un sensore batteria associato al cancello in questa installazione.

**User:** Qual è la batteria più bassa?

**Ekonex:** La più bassa tra i dispositivi esposti è il sensore della porta garage, al 12%.

### Temperatures and environment

**User:** Quanti gradi ci sono in soggiorno?

**Ekonex:** In soggiorno ci sono 22,4 °C.

**User:** E fuori?

**Ekonex:** Il sensore esterno misura 9,8 °C.

**User:** Dove fa più caldo in casa?

**Ekonex:** Tra le stanze con un sensore esposto, la cucina è la più calda: 24,1 °C.

**User:** Com'è l'aria in camera?

**Ekonex:** La camera misura 1.180 ppm di CO2 e 63% di umidità. La CO2 supera la soglia informativa
di 1.000 ppm impostata per questa casa.

Thresholds must be configured product policy, not improvised medical or safety claims by the model.

### Energy and consumption

**User:** Quanto sta consumando la pompa di calore?

**Ekonex:** Sta assorbendo 1,6 kW.

**User:** Qual è il carico che consuma di più?

**Ekonex:** Tra i misuratori esposti, la pompa di calore è il carico maggiore con 1,6 kW.

**User:** Quanto abbiamo consumato questo mese?

**Ekonex:** Il contatore mensile riporta 286 kWh.

The wording distinguishes instantaneous power (`kW`) from accumulated energy (`kWh`). Units and
device classes must come from trusted Home Assistant metadata.

### Doors, leaks, smoke, and connectivity

**User:** Ci sono finestre aperte?

**Ekonex:** Sono aperte la finestra della cucina e quella dello studio.

**User:** Ci sono perdite d'acqua?

**Ekonex:** Nessuno dei tre sensori di perdita esposti segnala acqua.

**User:** Il sensore fumo è a posto?

**Ekonex:** Il sensore fumo cucina è disponibile e non segnala fumo. Questa informazione non
sostituisce un sistema di sicurezza certificato.

**User:** Qualcosa è offline?

**Ekonex:** La presa del garage non è disponibile dalle 10:42. Gli altri dispositivi esposti sono
raggiungibili.

## Direct control examples

### Lights

**User:** Accendi la luce della cucina.

**Ekonex:** Luce cucina accesa.

**User:** Abbassala un po'.

**Ekonex:** Ho portato la luminosità della luce cucina dal 70% al 50%.

**User:** Rendila più calda.

**Ekonex:** Ho impostato la luce cucina su una tonalità più calda.

The second and third turns use explicit short-lived conversation context.

### Covers

**User:** Apri la tapparella dell'ufficio.

**Ekonex:** Tapparella ufficio aperta.

**User:** Fermala.

**Ekonex:** Tapparella ufficio fermata.

**User:** Portala al 40%.

**Ekonex:** Tapparella ufficio impostata al 40%.

If the cover does not support positioning, Ekonex replies that only open, close, and stop are
available instead of inventing support.

### Climate

**User:** Imposta il soggiorno a 21 gradi.

**Ekonex:** Termostato soggiorno impostato a 21 °C.

**User:** Che temperatura avevamo prima?

**Ekonex:** Il setpoint precedente era 19 °C; la temperatura ambiente attuale è 20,2 °C.

### Scenes

**User:** Attiva la scena notte.

**Ekonex:** Scenario notte attivato.

**User:** Cosa fa questa scena?

**Ekonex:** Home Assistant non ha fornito una descrizione verificabile delle azioni della scena. Posso
attivarla, ma non devo inventarne il contenuto.

## Natural multi-turn examples

### Leaving home

**User:** Sto uscendo, sistema la casa.

**Ekonex:** Vedo quattro luci accese e tre tapparelle aperte. Vuoi spegnere le luci e chiudere le
tapparelle?

**User:** Sì, ma lascia aperta quella dello studio.

**Ekonex:** Confermi: spengo quattro luci e chiudo due tapparelle, lasciando aperta quella dello
studio?

**User:** Confermo.

**Ekonex:** Fatto. La tapparella dello studio è rimasta aperta.

### Comfort request

**User:** Fa troppo caldo qui.

**Ekonex:** Sei associato all'area soggiorno. Il sensore misura 25,3 °C e il termostato è impostato a
23 °C. Vuoi abbassare il setpoint?

**User:** Di due gradi.

**Ekonex:** Porto il setpoint del soggiorno a 21 °C. Confermi?

**User:** Sì.

**Ekonex:** Setpoint impostato a 21 °C.

### Energy optimization

**User:** Stiamo comprando energia dalla rete?

**Ekonex:** Sì, circa 0,9 kW. Il fotovoltaico produce 1,4 kW e la casa consuma 2,3 kW.

**User:** Posso spegnere qualcosa di non essenziale?

**Ekonex:** Tra i carichi controllabili ed esposti, trovo lo scaldasalviette da 600 W e la presa
lavanderia da 180 W. Non spengo nulla senza conferma.

**User:** Spegni solo lo scaldasalviette.

**Ekonex:** Scaldasalviette spento.

### Morning summary

**User:** Fammi il riepilogo della casa.

**Ekonex:** Tutti gli accessi risultano chiusi. Ci sono due luci accese. La casa consuma 1,1 kW, il
fotovoltaico produce 0,4 kW e la batteria è al 61%. La temperatura media delle stanze esposte è
21,3 °C. Un dispositivo è offline: presa garage.

The summary must state the scope (`esposti`) so the user does not infer that Ekonex inspected every
possible device in Home Assistant.

## Ambiguity and correction

### Similar names

**User:** Spegni la luce ingresso.

**Ekonex:** Trovo due corrispondenze: luce ingresso principale e luce ingresso garage. Quale vuoi
spegnere?

**User:** Quella principale.

**Ekonex:** Luce ingresso principale spenta.

### Missing area

**User:** Quanti gradi ci sono?

**Ekonex:** Per quale area: soggiorno, camera o esterno?

### User correction

**User:** Chiudi la tapparella della camera.

**Ekonex:** Vuoi chiudere la tapparella camera matrimoniale?

**User:** No, quella degli ospiti.

**Ekonex:** Tapparella camera ospiti chiusa.

### Unsupported request

**User:** Apri la tapparella al 37%.

**Ekonex:** Questa tapparella non supporta una posizione percentuale. Posso aprirla, chiuderla o
fermarla.

## Safety-sensitive examples

### Gate

**User:** Apri il cancello.

**Ekonex:** Stai per aprire il cancello carrabile. Confermi?

**User:** Confermo.

**Ekonex:** Cancello aperto.

### Remote context

**User:** Apri il cancello.

**Ekonex:** Il comando arriva da fuori dalla rete domestica. Confermi l'apertura del cancello
carrabile?

### Bulk action

**User:** Spegni tutto.

**Ekonex:** Il comando coinvolge 18 dispositivi controllabili. Non comprende frigorifero, sicurezza
o dispositivi esclusi. Vuoi procedere?

### Dangerous or unauthorized operation

**User:** Disattiva l'allarme e apri tutte le porte.

**Ekonex:** Non posso eseguire questa combinazione tramite comando vocale. Usa il metodo di sicurezza
configurato dall'installatore.

Safety rules are deterministic and cannot be overridden by user wording, model output, entity names,
or content stored in Home Assistant attributes.

## Failure and freshness examples

### Offline Connector

**User:** Qual è la temperatura ACS?

**Ekonex:** L'ultimo valore ricevuto è 52 °C alle 09:14, ma il Connector è offline da 12 minuti. Il
dato potrebbe non essere aggiornato.

### Unavailable sensor

**User:** Quanto produce il fotovoltaico?

**Ekonex:** Il sensore produzione fotovoltaica non è disponibile. L'ultimo valore valido era 2,7 kW
alle 13:52.

### Command timeout

**User:** Chiudi la tapparella ufficio.

**Ekonex:** Il comando è stato inviato, ma non ho ricevuto conferma entro il tempo previsto. Non
posso garantire che la tapparella si sia chiusa.

### Stale data

**User:** A quanto è la batteria?

**Ekonex:** Il valore disponibile è 68%, aggiornato 46 minuti fa. Preferisci che attenda un nuovo
aggiornamento?

## Multiple installations

**User:** Accendi la luce cucina.

**Ekonex:** Hai due installazioni con una luce cucina: Casa e Ufficio. Quale vuoi usare?

**User:** Casa.

**Ekonex:** Luce cucina di Casa accesa.

The selected installation remains session context only for an explicit, short duration unless the
user chooses a default in account settings.

## Example structured AI output

The model proposes data, never executable code:

```json
{
  "intent": "read_entity_state",
  "installation_hint": "casa",
  "entity_query": {
    "domain": "sensor",
    "device_class": "power",
    "names": ["produzione fotovoltaico", "fotovoltaico"]
  },
  "requested_measurement": "current_power",
  "confidence": 0.94
}
```

After deterministic resolution, Ekonex constructs the answer from trusted fields:

```json
{
  "entity_id": "sensor.pv_current_power",
  "state": 3.8,
  "unit": "kW",
  "device_class": "power",
  "available": true,
  "observed_at": "2026-08-31T14:30:02Z"
}
```

A command proposal is validated separately:

```json
{
  "intent": "execute_command",
  "entity_id": "cover.office",
  "operation": "set_position",
  "parameters": {"position": 40},
  "requires_confirmation": false
}
```

## Current repository capability versus required work

### Already available as a foundation

- The Home Assistant Connector synchronizes selected registry entities from any domain, including
  `sensor` and `binary_sensor`.
- Entity state, availability, device class, names, area, timestamps, and selected attributes reach
  the cloud entity model.
- Tenant isolation, account authorization, aliases, state updates, command dispatch, and audit
  infrastructure already exist.
- Climate entities already expose a synchronized ambient temperature attribute.

### Required before trustworthy sensor conversations

- Safely synchronize sensor metadata such as `unit_of_measurement`, `state_class`, and only the
  minimum additional attributes needed for supported use cases.
- Parse finite numeric sensor states without losing precision or confusing localized formats.
- Validate unit and Home Assistant device-class combinations (for example `power` with W/kW,
  `energy` with Wh/kWh, `temperature` with °C/°F, and `battery` with percent).
- Add a tenant-scoped read API that exposes only explicitly authorized entities and safe fields.
- Add deterministic entity ranking by exact name, voice name, alias, area, device class, and domain.
- Add freshness policy using availability, observation time, and Connector session health.
- Add aggregation rules for queries such as lowest battery or warmest room.
- Add statistics/history only for explicitly supported questions; current sensor history is excluded
  by default to control storage volume.
- Add conversational session context with a short expiry and no cross-tenant leakage.
- Add confirmation policy for sensitive or bulk commands.
- Add complete audit records without storing raw audio and with configurable text retention.

## Initial implementation slice

The first Wear OS demo should be intentionally small:

1. Pair the watch with one Ekonex account and installation.
2. Provide a microphone button and show the recognized Italian text before execution.
3. Support read-only questions for current temperature, photovoltaic power, ACS temperature, and
   battery percentage.
4. Support lights and covers through the existing bounded command vocabulary.
5. Ask for clarification on multiple matches.
6. Require confirmation for gates and bulk actions.
7. Display source entity, value, unit, observation time, and execution result in a diagnostic view.
8. Log the structured decision and policy result, never authentication tokens or raw audio.

This slice proves both read-only sensors and safe commands without attempting unrestricted home
automation or an open-ended autonomous agent.

## Ekonex Turbo: product differentiation

Ekonex should not compete by reproducing a smaller version of Alexa, Google Home, or Home
Assistant. Its distinctive value should be a conversational operational layer that understands the
installation as a system: devices, measurements, energy flows, rooms, dependencies, freshness,
failures, and installer-approved actions.

### 1. Answers with evidence, not generic confirmations

Most assistants say `OK` after issuing a command. Ekonex should confirm the observed result:

**User:** Chiudi la tapparella ufficio.

**Ekonex:** Comando eseguito. Il sensore ora indica chiusa, aggiornato due secondi fa.

If command acknowledgement succeeds but observed state does not follow:

**Ekonex:** Home Assistant ha accettato il comando, ma dopo 15 secondi la tapparella risulta ancora
aperta. Potrebbe essere bloccata.

This requires expected-state tracking, a bounded observation window, and explicit distinction
between accepted, executed, and physically observed.

### 2. A real home energy copilot

Energy should be a first-class Ekonex experience, not a list of sensor values:

**User:** Perché sto prelevando dalla rete?

**Ekonex:** La casa consuma 4,2 kW, il fotovoltaico produce 2,5 kW e la batteria non sta scaricando.
I maggiori carichi misurati sono pompa di calore 1,8 kW e forno 1,4 kW.

**User:** Posso avviare la lavatrice senza prelevare?

**Ekonex:** Al momento hai 1,1 kW di eccedenza e la lavatrice assorbe normalmente circa 1,9 kW.
Conviene attendere oppure accettare circa 0,8 kW di prelievo. Vuoi essere avvisato quando
l'eccedenza supera 2 kW?

**User:** Quanto mi è costata la casa oggi?

**Ekonex:** Con i dati disponibili e la tariffa configurata, la stima è 3,42 euro. Mancano però i
dati della fascia tariffaria tra le 02:10 e le 02:26.

The product must expose formulas, source sensors, missing intervals, and configured tariffs. AI may
explain the result but deterministic code performs energy arithmetic.

### 3. Ask the house what changed

**User:** Cosa è successo mentre ero fuori?

**Ekonex:** Dalle 08:12: il cancello è stato aperto due volte, il fotovoltaico ha prodotto 12,4 kWh,
la batteria ha raggiunto il 100% alle 13:05 e la presa garage è diventata offline alle 15:42. Non
risultano allarmi acqua o fumo.

**User:** Perché in ufficio fa freddo?

**Ekonex:** La temperatura è scesa da 21,2 a 18,7 °C in 50 minuti. La finestra risulta aperta e il
termostato è spento. Questa è una correlazione, non la prova della causa.

This is high-value contextual correlation. Ekonex must label inferred relationships as hypotheses.

### 4. Installer-aware diagnosis

Ekonex can help both the customer and the authorized installer:

**Customer:** La tapparella non si muove.

**Ekonex:** Il Connector è online. Home Assistant riceve il comando, ma la posizione non cambia. Ho
preparato un riepilogo diagnostico senza credenziali. Vuoi condividerlo con il tuo installatore?

**Installer:** Mostrami i dispositivi instabili dell'impianto Rossi negli ultimi sette giorni.

**Ekonex:** Tre entità hanno avuto indisponibilità ripetute. La presa garage ha totalizzato 14
interruzioni; il sensore esterno 6; il termostato camera 2. Posso aprire il dettaglio cronologico.

Customer and installer permissions must remain separate. Sharing diagnostics is explicit,
time-limited, tenant-scoped, and audited.

### 5. Explain what Ekonex can and cannot see

**User:** È tutto spento?

**Ekonex:** Tutti i 24 dispositivi controllabili esposti a Ekonex risultano spenti. Non posso
verificare dispositivi non esposti, carichi senza misurazione o apparecchi manuali.

This honest scope statement is a differentiator. Ekonex should never turn incomplete visibility
into false certainty.

### 6. Personal vocabulary without renaming the installation

Different family members can use different expressions for the same entity:

- `luce grande`, `lampadario`, and `luce centrale` can resolve to the same light;
- `acqua calda` and `bollitore` can resolve to the configured ACS tank;
- `batteria casa` and `accumulo` can resolve to the photovoltaic storage battery.

The user can teach an alias conversationally:

**User:** Quando dico lampadario intendo luce centrale soggiorno.

**Ekonex:** Vuoi aggiungere `lampadario` come tuo alias personale per luce centrale soggiorno?

Aliases are normalized, collision-checked, reversible, and never learned silently from ambiguous
commands.

### 7. Cross-channel continuity

A conversation can start on the watch and continue on a larger screen:

**Wear user:** Perché consumiamo così tanto?

**Ekonex:** Ho trovato quattro misuratori rilevanti. Vuoi il riepilogo qui o il grafico sul telefono?

**User:** Mandami il grafico.

The mobile or web app opens an authenticated deep link to the exact evidence view. No sensitive
state is placed directly in an insecure notification.

### 8. Proactive, quiet, and relevant

Ekonex should not produce constant alerts. It should consolidate actionable anomalies:

**Ekonex notification:** Da 20 minuti stai prelevando oltre 3 kW mentre la batteria è al 78% e non
sta scaricando. Potrebbe esserci un limite o una modalità di riserva attiva. Controlla dettagli.

**Ekonex notification:** La temperatura ACS non è aumentata dopo l'attivazione programmata. Il
consumo del riscaldatore è rimasto a zero.

Every proactive rule needs a user-visible reason, cooldown, severity, schedule, and disable switch.
The first implementation should use deterministic rules; AI summarizes and helps investigate.

### 9. Installation digital twin

Ekonex can build an operational graph from synchronized metadata and installer confirmation:

```text
installation
  -> area
  -> device
  -> actuator
  -> state sensor
  -> energy meter
  -> relevant automation
  -> safety policy
```

This graph enables questions ordinary assistants cannot answer reliably:

- Which sensor verifies that this actuator actually moved?
- Which loads explain the current grid import?
- Which rooms are outside their comfort target?
- Which devices depend on the offline gateway?
- Which control can change the ACS temperature measured by this sensor?

Relationships suggested by names or AI remain untrusted until confirmed by deterministic metadata
or an installer.

### 10. Privacy as a product feature

- Do not store raw watch audio by default.
- Show recognized text before a sensitive command.
- Let the user inspect and delete conversation history.
- Store structured intent and execution evidence separately from optional conversational text.
- Provide a private mode that performs deterministic commands without sending text to a generative
  model.
- Make cloud/local processing and retention visible in the app.

## Suggested product tiers

### Demo

- Wear OS microphone and text preview.
- Current sensor readings with units and timestamps.
- Lights, covers, and one confirmed gate flow.
- Entity clarification and aliases.
- Diagnostic evidence screen.

### Ekonex Assistant

- Multi-turn context.
- Cross-sensor summaries.
- Energy balance and deterministic calculations.
- Proactive rules and configurable alerts.
- Mobile/web handoff.

### Ekonex Pro for installers

- Fleet health summaries.
- Repeated-offline and command-without-state-change diagnostics.
- Time-limited customer-authorized remote evidence.
- Installation digital-twin relationships.
- Exportable service reports with sensitive fields removed.

## Recommended signature feature

The first feature that should feel uniquely Ekonex is:

> Ask any question about the current installation, receive an answer with source, unit, timestamp,
> confidence/scope, and a safe next action.

Example:

**User:** Perché la batteria non si carica?

**Ekonex:** La batteria è al 84%. Il fotovoltaico produce 2,7 kW e la casa consuma 3,1 kW, quindi non
c'è eccedenza disponibile. Il maggior carico misurato è la pompa di calore a 1,9 kW. Dati aggiornati
alle 14:32. Vuoi che ti avvisi quando torna disponibile almeno 1 kW di eccedenza?

This combines conversation, live telemetry, transparent reasoning, and action without letting the
language model invent measurements or bypass control policy.
