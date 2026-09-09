# Tag operativo — prove dopo la certificazione Alexa

Tag di lavoro: `POST-ALEXA-CERTIFICATION`

Stato: **BLOCCATO FINO ALLA CONCLUSIONE DELLA CERTIFICAZIONE AMAZON**

Questa checklist raccoglie le verifiche da eseguire dopo la certificazione. Non autorizza
modifiche immediate alla skill, alla Lambda o al backend di produzione.

## 1. Condizioni per iniziare

- [ ] Conservare l'esito completo della certificazione e le osservazioni Amazon.
- [ ] Aggiornare il caso Amazon `21738300901` con l'esito definitivo.
- [ ] Verificare che non siano presenti altre revisioni Amazon in corso.
- [ ] Ottenere approvazione esplicita prima di modificare la produzione.
- [ ] Annotare versioni, immagini Docker e stato dei servizi correnti.
- [ ] Preparare backup di database, Lambda, modello Alexa e configurazione Caddy.
- [ ] Definire commit, immagine e procedura di rollback esatti.

## 2. Gate tecnico

- [ ] Produzione e Laboratorio rispondono ai rispettivi endpoint `/health`.
- [ ] Home Assistant è online e il Connector di produzione è connesso.
- [ ] Migrazioni provate prima su database di test e Laboratorio.
- [ ] Pytest, Ruff, mypy, Hassfest e validazione HACS superati su Linux.
- [ ] Nessun segreto o dato personale presente in log, archivi o commit.
- [ ] Rilascio composto soltanto da modifiche deliberate e validate nel Lab.

## 3. Regressione Smart Home

- [ ] Discovery delle sole entità autorizzate.
- [ ] Luci, switch, prese e ventole: accensione, spegnimento, luminosità e percentuale.
- [ ] Scene e script esclusivamente se autorizzati.
- [ ] Clima: temperatura corrente distinta dal setpoint e sole modalità HVAC valide.
- [ ] Unità, arrotondamenti, indisponibilità e dati obsoleti.
- [ ] Pronunciare la frase richiesta da Amazon: `Alexa, apri le tapparelle`.
- [ ] Registrare ora UTC, risposta Alexa e presenza della direttiva nel backend.
- [ ] Tapparelle: apertura, chiusura, stop e posizione secondo la modalità configurata.
- [ ] Verificare che `PlaybackController` non sia pubblicato dove Amazon lo considera errato.
- [ ] Distinguere comando accettato e stato fisico realmente osservato.

## 4. Media Player e sorgenti vocali

- [ ] Aggiornare i Connector alla versione approvata.
- [ ] Verificare `source`, `source_list`, volume, muto e capability sincronizzate.
- [ ] Configurare inclusione, nome vocale e alias per ogni sorgente.
- [ ] Escludere sorgenti sconosciute, tecniche o duplicate.
- [ ] Provare ogni sorgente dal portale per ciascun Media Player.
- [ ] Pubblicare `Alexa.InputController` soltanto con sorgenti abilitate.
- [ ] Eseguire una nuova Discovery Alexa dopo le modifiche.
- [ ] Provare `Alexa, imposta TV Sala su Sky`.
- [ ] Provare `Alexa, cambia ingresso di TV Sala su PlayStation`.
- [ ] Provare `Alexa, seleziona Decoder su TV Sala`.
- [ ] Verificare che nomi e alias richiamino la sorgente Home Assistant esatta.
- [ ] Verificare che una sorgente esclusa non sia pubblicata né comandabile.
- [ ] Verificare lo stato della sorgente attiva.

## 5. Interrogazioni conversazionali

- [ ] Fotovoltaico SAS, Privato e tutti i fotovoltaici con valori separati.
- [ ] Consumo istantaneo e potenza di rete.
- [ ] Energia prodotta, consumata, importata ed esportata oggi.
- [ ] Tutte le batterie e una singola batteria.
- [ ] Temperature ambiente separate da quelle della centrale termica.
- [ ] Allarme, serrature, porte e aperture senza collisioni di intent.
- [ ] Singola entità tramite nome vocale e alias.
- [ ] Filtri rigidi per stanza, piano e categoria vocale.
- [ ] Riepilogo casa con massimo tre segnalazioni prioritarie.
- [ ] Ambiguità, indisponibilità e dati obsoleti gestiti esplicitamente.
- [ ] Nessun `entity_id` tecnico pronunciato all'utente.

## 6. Apprendimento IA

- [ ] Le frasi note non invocano OpenAI.
- [ ] Il fallback IA interviene soltanto per frasi nuove o ambigue.
- [ ] Approvazione, eliminazione e persistenza delle frasi apprese.
- [ ] Generazione, validazione e Build del modello Alexa.
- [ ] Conteggio token, costo stimato e percentuale risolta senza IA.
- [ ] L'IA non può autorizzare entità, inventare valori o creare comandi arbitrari.

## 7. Routine vocali e annunci

- [ ] Echo singolo, gruppo personalizzato e `Ovunque`.
- [ ] Modalità `announce` e `speak`.
- [ ] Messaggio fisso, template Home Assistant e valore proveniente dal trigger.
- [ ] Volume facoltativo con ripristino ritardato.
- [ ] Fascia notturna, priorità, condizioni, ripetizioni e silenziamento.
- [ ] Destinatario “ultima Alexa utilizzata” con scadenza.
- [ ] Nessun annuncio duplicato tra gruppi Alexa ed Echo singoli.
- [ ] Nessun testo pronunciato conservato nei log dei comandi.

## 8. Sicurezza e isolamento

- [ ] Nessuna entità visibile o comandabile tra tenant differenti.
- [ ] Entità rimosse, disabilitate o non esposte falliscono in modo chiuso.
- [ ] Comandi tipizzati e allowlistati; nessun servizio HA arbitrario.
- [ ] Pairing monouso, scadenza, limiti brute-force, revoca e rotazione.
- [ ] Token, password, codici OAuth e variabili d'ambiente redatti.
- [ ] Il Laboratorio non può scrivere nel database di produzione.

## 9. Promozione controllata

- [ ] Promuovere soltanto commit e immagine che hanno superato tutti i gate.
- [ ] Applicare migrazioni con backup e ripristino verificato.
- [ ] Aggiornare il Connector prima delle capability che ne dipendono.
- [ ] Aggiornare Lambda e Discovery Alexa in una finestra controllata.
- [ ] Iniziare con una sola installazione e un solo dispositivo.
- [ ] Estendere gradualmente la Discovery agli altri impianti.
- [ ] Monitorare errori, latenza, reconnect EVCP, direttive ed eventi proattivi.
- [ ] Conservare container, immagine e pacchetto Lambda precedenti per il rollback.

## 10. Scheda evidenze

| Campo | Valore |
|---|---|
| Data e ora Europe/Rome | |
| Ora UTC esatta | |
| Ambiente e installazione | |
| Versioni Connector/backend/Lambda | |
| Frase pronunciata | |
| Trascrizione Alexa | |
| Risposta pronunciata da Alexa | |
| Direttiva ricevuta | sì / no |
| Risultato backend | |
| Stato finale Home Assistant | |
| Stato fisico finale, se applicabile | |
| Esito | superato / fallito / da ripetere |
| Note e correlation ID | |

## Criterio di chiusura

Il tag `POST-ALEXA-CERTIFICATION` può essere chiuso soltanto quando tutte le prove applicabili
hanno evidenze, non rimangono regressioni critiche, il rollback è stato verificato e la promozione
in produzione ha ricevuto approvazione esplicita.
