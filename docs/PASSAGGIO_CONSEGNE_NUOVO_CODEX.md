# Passaggio di consegne — Ekonex Voice / Laboratorio Alexa

> Documento operativo per una nuova sessione Codex. Leggere interamente prima di modificare codice, VPS, AWS o Alexa.
>
> Aggiornato: 3 settembre 2026, fuso orario Europe/Rome.

## 1. Obiettivo del progetto

Ekonex Voice collega Home Assistant/e-Control a servizi vocali. L'obiettivo non è un semplice elenco di dispositivi: deve diventare il “maggiordomo” della casa o dell'impianto, capace di:

- rispondere sullo stato dei soli sensori scelti nel portale Ekonex;
- usare nomi vocali e alias configurati dal cliente;
- interrogare produzione fotovoltaica, consumo, batterie, rete, energia giornaliera, temperature, allarme, serrature e aperture;
- rispondere a richieste singole e riepiloghi di gruppo;
- comprendere formulazioni naturali mediante IA, senza affidare all'IA valori o autorizzazioni;
- in futuro emettere annunci proattivi/TTS generati da trigger Home Assistant;
- in futuro servire anche Wear OS e altri assistenti, mantenendo il portale Ekonex come fonte unica della configurazione.

Principio fondamentale: l'IA interpreta la frase, ma dati, categorie, entità autorizzate e valori devono provenire dal backend Ekonex/Home Assistant. L'IA non deve inventare stati o controllare entità non autorizzate.

## 2. Regola assoluta: produzione sotto certificazione

La skill Smart Home di produzione **Ekonex Voice** è sotto certificazione Amazon.

- Stato visto nel portale: `In Review`.
- Invio: 27 agosto 2026, ore 06:10.
- Il portale indicava risultati attesi entro 17 settembre 2026, ore 06:10.
- Caso Amazon aperto: `21738300901`.

Amazon Developer Advocate (Jayanth A.) ha confermato per iscritto:

- non distribuire la rimozione di `Alexa.PlaybackController`;
- non attendere la certificazione per quel test, perché la modifica non risolve il problema;
- Discovery payload, semantics, instance name, display category e Lambda di produzione sono corretti;
- il difetto italiano per il comando “apri” è sotto indagine interna Amazon;
- mantenere il caso aperto e attendere aggiornamenti concreti.

### Vincolo operativo

**Non modificare né distribuire la skill, la Lambda o il backend di produzione per fare prove Alexa durante la certificazione.** Tutto lo sviluppo conversazionale descritto qui va nel Laboratorio separato. Una futura promozione in produzione dovrà essere esplicita, controllata e successiva alla certificazione.

## 3. Ambienti e architettura

### Repository locale

- Percorso Windows: `C:\Users\NUC Alex\OneDrive\EA SAS\0000000033-TOOL\HASSIO ADDON\e-Voice`
- Branch attuale: `agent/conversation-core-demo`
- Ultimo rilascio stabile al momento della prima stesura: `a15284a feat: route Alexa requests by voice category`.
- Correzioni successive: `896d0cb` ripristina la pagina categorie; il commit successivo deve gestire `temperature_ambiente` e leggere `current_temperature` dalle entità `climate`.

### VPS

- Host: `169.58.200.54`
- Provider/display name: Ekonex Voice
- Accesso SSH già predisposto con chiave locale: `C:\Users\NUC Alex\.ssh\codex_ekonex_vps`
- Esempio: `ssh -i "$env:USERPROFILE\.ssh\codex_ekonex_vps" root@169.58.200.54`
- Produzione: `/opt/ekonex/e-voice`
- Laboratorio: `/opt/ekonex/e-voice-lab`
- Caddy: `/etc/caddy/Caddyfile`
- Dominio produzione: `https://voice.e-control.tech`
- Dominio laboratorio: `https://voice-lab.e-control.tech`

Non riportare in chat password, chiavi API, token backend o contenuto dei file `.env`.

### Container di produzione osservati

- `e-voice-api-1`
- `e-voice-maintenance-1`
- `e-voice-postgres-1`
- `e-voice-redis-1`

Il backend produzione è pubblicato solo su `127.0.0.1:8000` e Caddy fa reverse proxy. Il `docker-compose.yml` della VPS produzione risultava modificato rispetto a Git: non sovrascriverlo senza prima analizzare il diff.

### Laboratorio

- Container: `e-voice-lab-api`
- Porta locale VPS: `127.0.0.1:8001 -> 8000`
- Rete Docker condivisa: `e-voice_default`
- File variabili: `/opt/ekonex/e-voice-lab/.env` con permessi `600`
- Endpoint interno: `/alexa/laboratory`
- Caddy espone solo l'endpoint laboratorio previsto e restituisce `404` sugli altri percorsi.
- Health interno noto: `http://127.0.0.1:8001/health`
- Immagine distribuita più recente nota: `e-voice-lab:a15284a`

Il laboratorio legge configurazione e metadati dal proprio contesto, ma sovrappone gli stati vivi provenienti dal database di produzione tramite un utente PostgreSQL di sola lettura. Non deve mai scrivere nel database produzione e non deve inviare comandi Home Assistant.

### Skill Alexa Custom di laboratorio

- Nome: Ekonex Laboratorio R&D
- Invocation name attualmente usato: `casa econex` (Alexa spesso comprende meglio “Econex” pronunciato così)
- Skill ID: `amzn1.ask.skill.6f4ff736-deee-43b8-bf09-6399d0f0a4a2`
- Tipo: Custom Skill, italiano, hosting proprio tramite AWS Lambda.
- Lambda in regione Europa/Irlanda: `EkonexLaboratorio`
- La Lambda inoltra le richieste HTTPS al backend laboratorio usando URL e token nelle proprie variabili d'ambiente.

Non confondere questa skill con la Smart Home di produzione. Al termine dei test la skill laboratorio può rimanere privata o essere dismessa; le funzionalità validate dovranno essere promosse deliberatamente in produzione.

## 4. Stato Git da preservare

Al momento della redazione il worktree non è pulito. Modifiche locali non committate:

- `custom_components/ekonex_voice/manifest.json`
- `docs/EKONEX_AI_CONVERSATION_DEMO.md`
- `tests/test_ha_inventory.py`

Sono presenti molti ZIP non tracciati `ekonex-lab-backend-*.zip`, tre ZIP Lambda e `ekonex_voice-beta16.zip`.

Regole per il nuovo Codex:

- non cancellare gli ZIP senza autorizzazione;
- non fare `git reset --hard`, `git checkout --` o pulizie distruttive;
- non includere automaticamente le tre modifiche locali in commit non correlati;
- prima di ogni commit usare `git status --short` e aggiungere solo file intenzionali;
- prima di distribuire verificare commit e immagine esatti.

## 5. Funzioni già implementate

### Portale e nomi vocali

Ogni entità può avere:

- nome e-Control sincronizzato e non modificabile;
- nome visualizzato;
- nome vocale;
- fino a 20 alias vocali, uno per riga;
- categoria vocale;
- tipo dispositivo Alexa.

Il nome vocale configurato nel portale deve essere la fonte principale. Il sistema non deve richiedere modifiche manuali nel codice ogni volta che viene aggiunto un sensore.

### Categorie vocali

Pagina di menu: **Categorie sensori**. Nonostante il nome storico, le categorie devono funzionare anche per entità non `sensor`, per esempio `lock` e `binary_sensor`.

Categorie standard osservate/aggiunte:

- `consumption_power` — Consumo istantaneo
- `consumed_energy_today` — Energia consumata oggi
- `exported_energy_today` — Energia esportata oggi
- `imported_energy_today` — Energia importata oggi
- `produced_energy_today` — Energia prodotta oggi
- `battery_level` — Livello batteria
- `grid_power` — Potenza di rete
- `photovoltaic_power` — Potenza fotovoltaica
- `alarm_status` — Stato allarme
- `opening_status` — Stato apertura
- `lock_status` — Stato serratura
- `temperature` — Temperatura ambiente/generica
- `thermal_temperature` — Temperatura centrale termica
- `temperature_ambiente` — categoria personalizzata realmente assegnata al Termostato Ufficio nel laboratorio
- categoria personalizzata osservata: Sensori allarme

Il dettaglio installazione mostra ora una colonna **Categoria vocale** tra Entità e Dominio/area.

### Raggruppamento e riepiloghi

Le interrogazioni di gruppo devono usare la categoria, non euristiche sui nomi. Esempi:

- tutte le batterie;
- tutti i fotovoltaici;
- energia importata/esportata/prodotta/consumata per tutti gli impianti;
- tutte le serrature;
- tutte le temperature ambiente;
- tutte le temperature della centrale termica.

Per le richieste su più impianti, l'utente preferisce sentire i singoli valori SAS e Privato, **non una somma matematica**.

### Formattazione valori

- Temperature centrali termiche: interi, senza decimali.
- Temperature ambiente/termostati: una cifra decimale.
- Batterie: percentuale intera.
- Potenza: unità comprensibile, tipicamente watt o kilowatt.
- Energia: conversione leggibile; esempio `0,08 kWh` deve essere pronunciato “80 wattora”. Non pronunciare “wh” come lettere.
- Le risposte Alexa non devono includere righe tecniche come `fonte: sensor...`.
- Per stato allarme, l'utente ha chiesto in un passaggio di pronunciare esattamente il valore del sensore, non una parafrasi arbitraria.

### Apprendimento IA

Pagina di menu: **Apprendimento IA**; il laboratorio mostra chiaramente il banner rosso `LABORATORIO — NON È PRODUZIONE` e usa il logo laboratorio fornito dall'utente.

Funzioni presenti:

- raccolta della frase pronunciata dopo una risposta valida;
- interpretazione/intent/impianto/riutilizzi/stato;
- Approva;
- Elimina accanto ad Approva o nella riga;
- filtro della tabella;
- download `Scarica JSON Alexa aggiornato`;
- dati persistenti nel database, quindi non dovrebbero sparire al reboot.

L'idea approvata dall'utente è: l'IA interpreta una nuova formulazione la prima volta; dopo approvazione, la frase confluisce nel modello Alexa e le richieste successive usano il modello deterministico. In laboratorio il passaggio resta manuale: scaricare JSON, incollarlo in Alexa e fare Build. La futura automazione Approva -> aggiornamento modello -> Build non è ancora da considerare completata e in produzione richiederebbe controlli e versionamento.

### OpenAI

- È stato acquistato credito API separato da ChatGPT Plus; Plus non include l'API.
- Auto-reload risultava disattivato.
- È stata creata una chiave con permesso minimo per `Responses` e lettura modelli.
- La chiave non deve essere stampata o copiata in documentazione.
- L'IA va chiamata solo come fallback per frasi nuove/ambigue, non per ogni richiesta già nota, per contenere costi e latenza.
- L'utente vuole poter capire dove vengono consumati i token.

Poiché alcuni segreti sono comparsi visivamente durante le sessioni precedenti, valutare una rotazione programmata della chiave OpenAI e del token laboratorio, senza interrompere il servizio e senza riportarli in chat.

## 6. Ultimi commit funzionali importanti

- `a15284a` — routing richieste Alexa tramite categoria vocale dinamica
- `9ae8ea1` — distingue temperature ambiente e centrale termica
- `3b862de` — ripristina query degli stati vivi delle entità nel laboratorio
- `0dd364d` — replica stato online/offline dell'installazione nel laboratorio
- `0fe0c12` — sovrappone stati vivi nel laboratorio
- `9a31a33` — riepilogo degli stati testuali delle serrature
- `7d97a25` — classificazione automatica delle entità senza categoria
- `7ba9a1c` — usa con priorità le categorie vocali nelle richieste di gruppo

Consultare `git show <commit>` prima di modificare queste aree.

## 7. Ultima modifica: categorie dinamiche nel modello Alexa

Problema precedente: Alexa aveva un `TemperatureSummaryIntent` senza slot. Pronunciando “temperature ambiente” o “temperature della centrale termica”, Alexa inviava al backend solo il concetto generico “tutte le temperature”, perdendo la categoria.

Soluzione distribuita in `a15284a`:

- nuovo `CategorySummaryIntent`;
- slot `category` di tipo `EKONEX_CATEGORY`;
- durante il download JSON, il portale legge tutte le `VoiceCategory` del tenant e popola dinamicamente lo slot con nome e slug;
- il backend riceve lo slug della categoria;
- il servizio conversazionale filtra rigidamente le entità per `voice_category.slug`.

Sono rimasti anche intent espliciti per ambienti e centrale termica, compatibili con il nuovo routing.

### Verifica backend già eseguita

Richiesta con slug `thermal_temperature`:

`Acqua Calda: 66°C; Puffer Alto: 65°C; Volano: 52°C.`

Richiesta con slug `temperature`:

`Non trovo un sensore autorizzato adatto a questa domanda.`

Questo secondo risultato è corretto se nessun sensore ambiente autorizzato è stato assegnato alla categoria `temperature`. È la prova che il sistema non mescola più la centrale termica nelle temperature ambiente.

### Passaggio manuale ancora necessario in Alexa

Dopo ogni modifica a categorie/frasi che cambia il modello:

1. Portale Laboratorio -> **Apprendimento IA**.
2. Premere **Scarica JSON Alexa aggiornato**.
3. Alexa Developer Console -> skill laboratorio -> Build -> JSON Editor.
4. Sostituire l'intero JSON con quello scaricato.
5. Save Model.
6. Build Model.
7. Attendere build riuscita prima del test Echo.

Se il backend è aggiornato ma Alexa non lo è, Alexa può instradare la frase all'intent sbagliato o rispondere “non so come aiutarti”.

## 8. Test vocali prioritari

Usare prima il simulatore Alexa, poi un Echo reale. Sul dispositivo, scandire bene `casa Econex`.

### Categorie temperatura

- `Alexa, chiedi a casa Econex tutte le temperature della centrale termica`
  - atteso: Acqua Calda, Puffer Alto e Volano, tutti e tre;
- `Alexa, chiedi a casa Econex tutte le temperature ambiente`
  - atteso: solo entità in categoria temperatura ambiente; nessun valore della centrale termica.

### Batterie

- `Alexa, chiedi a casa Econex la percentuale di tutte le batterie`
- `Alexa, chiedi a casa Econex come sono messe tutte le batterie`
  - atteso: SAS e Privato con valori vivi separati.

### Energia e fotovoltaico

- produzione di tutti i fotovoltaici;
- energia oggi esportata da tutti gli impianti;
- energia oggi importata da tutti gli impianti;
- energia prodotta oggi da tutti gli impianti;
- energia consumata oggi da tutti gli impianti.

Atteso: due valori separati SAS e Privato. Non sommare.

### Serrature e aperture

- `stato di tutte le serrature` funzionava meglio di `stato delle serrature`, che in precedenza veniva confuso con lo stato allarme;
- verificare dopo il nuovo JSON che le categorie evitino la collisione;
- distinguere `lock` (bloccata/sbloccata) da `binary_sensor` (aperta/chiusa).

### Stato allarme

- verificare che una richiesta sullo stato allarme restituisca il valore corrente autorizzato e non intercetti richieste su porte o serrature.

## 9. Problemi noti e diagnosi

### Alexa comprende male abbreviazioni e termini

- `SAS` può essere trascritto come `s. a. s.` o lettere separate.
- `ACS` può essere interpretato male.
- `puffer` può essere pronunciato/trascritto “paffer”.
- Il nome invocazione deve avere almeno due parole, quindi `ekonex` da solo non era ammesso; `casa econex` è stato usato per i test.

Nomi vocali e alias del portale aiutano il matching backend, ma non correggono ciò che Alexa elimina prima di inviare l'intent. Per questo servono slot e categorie nel modello Alexa.

### Richiesta singola vs gruppo

In passato alcune frasi combinate producevano un solo impianto perché l'intent singolo vinceva su quello riepilogativo. Le categorie devono ridurre questo problema, ma vanno verificati tutti i tipi, non soltanto temperatura.

### Online/offline nel portale laboratorio

Il laboratorio appariva offline e mostrava stati vecchi perché lavorava su una copia/DB laboratorio. È stato implementato l'overlay di stato vivo dalla produzione in sola lettura. Se ricompare:

1. verificare `e-voice-lab-api` e health;
2. verificare che la variabile `EKONEX_LABORATORY_LIVE_DATABASE_URL` esista nel `.env` senza stamparne il valore;
3. verificare accesso read-only al DB produzione;
4. controllare log container e timestamp dell'installazione;
5. non risolvere puntando il laboratorio in scrittura alla produzione.

### Home Assistant che si riavviava

Nei log era presente un errore della custom integration `e_dry`:

`async_write_ha_state` chiamato da un thread diverso dall'event loop in `custom_components/e_dry/sensor.py`, linea 428.

Home Assistant lo segnala come comportamento che può causare crash o corruzione. Anche il database SQLite risultava non chiuso correttamente, probabilmente conseguenza dei riavvii e non necessariamente causa primaria. Questo problema appartiene a un altro filone/altro Codex, ma va ricordato.

## 10. Comandi diagnostici sicuri

### Locale

```powershell
git status --short --branch
git log -8 --oneline
```

### VPS

```bash
docker ps --filter name=e-voice-lab-api
curl -fsS http://127.0.0.1:8001/health
docker logs --since 10m e-voice-lab-api
systemctl is-active caddy
caddy validate --config /etc/caddy/Caddyfile
```

Controllo produzione senza modificarla:

```bash
docker ps --filter name=e-voice-api
curl -fsS http://127.0.0.1:8000/health
```

Evitare di mostrare `docker inspect` completo o `cat .env`, perché possono esporre segreti.

## 11. Procedura di rilascio laboratorio già usata

Il flusso storico è:

1. modificare e testare localmente;
2. creare un archivio ZIP del repository senza segreti;
3. copiare lo ZIP in `/opt/ekonex/e-voice-lab/releases/<commit>/`;
4. estrarre e costruire `e-voice-lab:<commit>`;
5. fermare il vecchio container;
6. rinominarlo come rollback, non cancellarlo immediatamente;
7. avviare `e-voice-lab-api` con `.env`, rete condivisa, porta 8001, filesystem read-only e privilegi ridotti;
8. verificare health ed endpoint conversazionale;
9. mantenere produzione intatta.

Parametri di hardening usati:

```text
--read-only
--tmpfs /tmp:rw,noexec,nosuid,size=64m
--cap-drop ALL
--security-opt no-new-privileges
--restart unless-stopped
```

Prima di un nuovo rilascio verificare quale container rollback esiste già e non riutilizzare nomi in conflitto.

## 12. Prossimi lavori raccomandati

Ordine suggerito:

1. Far scaricare all'utente il nuovo JSON dinamico, incollarlo e fare Build nella skill laboratorio.
2. Testare le due categorie temperatura su simulatore ed Echo.
3. Assegnare almeno uno o più sensori ambiente autorizzati alla categoria corretta e ripetere il test.
4. Verificare il routing generico per tutte le categorie standard: batteria, FV, consumo, energia, rete, allarme, serrature e aperture.
5. Aggiungere test automatici parametrizzati: per ogni categoria, il riepilogo deve includere solo entità con lo stesso slug.
6. Migliorare la pagina categorie con conteggio entità, esempi vocali e avviso per categorie vuote.
7. Aggiungere un pannello consumo IA: richieste, token input/output, costo stimato e percentuale di richieste risolte senza IA.
8. Ridurre le chiamate OpenAI tramite cache e regole deterministiche.
9. Progettare gli annunci immediati da trigger HA senza Alexa Media Player; valutare Alexa proactive events/notifications o servizio equivalente, chiarendo i limiti Alexa prima di implementare.
10. Solo dopo certificazione e approvazione esplicita, preparare un piano di promozione laboratorio -> produzione con backup, migrazioni, test e rollback.

## 13. Decisioni UX dell'utente

- Il portale deve rimanere semplice anche con moltissime entità.
- Le entità devono essere raggruppate per tipo e i gruppi compatti/espandibili.
- Deve essere visibile direttamente nell'elenco se una categoria è assegnata e quale.
- Le categorie devono evitare configurazioni una per una nel codice.
- Non obbligare l'utente a ricordare formule vocali innaturali come “dimmi”.
- Le frasi nuove devono poter essere apprese e poi diventare deterministiche.
- Il laboratorio deve essere visivamente inequivocabile rispetto alla produzione.
- L'utente vuole suggerimenti proattivi sull'architettura: non limitarsi a correggere una frase alla volta quando esiste una soluzione generale.

## 14. Istruzione iniziale consigliata per la nuova chat

In una nuova sessione, scrivere soltanto:

> Leggi interamente `docs/PASSAGGIO_CONSEGNE_NUOVO_CODEX.md`. Continua dal punto 12 senza modificare la produzione, che è sotto certificazione. Prima controlla lo stato Git e dimmi in due righe cosa farai.

Questo documento sostituisce la necessità di reinviare l'intera chat precedente.
