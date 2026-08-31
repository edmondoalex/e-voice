# Demo conversazionale Ekonex AI

Stato: documento di progettazione. Le funzioni descritte non sono ancora tutte implementate.

Ekonex deve diventare il maggiordomo digitale della casa: comprende il linguaggio naturale, legge sensori autorizzati, esegue soltanto comandi convalidati e spiega sempre che cosa ha osservato davvero. Il primo canale consigliato è Wear OS; in seguito la stessa intelligenza potrà servire Alexa, applicazioni mobili, web, Google e Siri.

## Architettura

```text
voce o evento Home Assistant
  -> riconoscimento vocale oppure Connector
  -> servizio Ekonex
  -> risoluzione di entità e contesto
  -> autorizzazioni e regole di sicurezza
  -> lettura oppure comando EVCP convalidato
  -> Home Assistant
  -> esito, risposta vocale/testuale e registro
```

Il modello linguistico non invia servizi arbitrari a Home Assistant. Produce una richiesta strutturata; codice deterministico controlla cliente, installazione, entità, parametri, conferme e sicurezza.

## Wear OS e Alexa

Su Wear OS l'utente apre Ekonex, tocca il microfono e parla naturalmente. Ekonex riceve il testo riconosciuto, può fare domande di chiarimento e mostrare le fonti della risposta.

Nella modalità Alexa Smart Home, Amazon interpreta la frase prima di chiamare Ekonex. Se Amazon non genera una direttiva, Ekonex non riceve la richiesta. Una futura esperienza conversazionale potrà gestire frasi come «Alexa, chiedi a Ekonex...», ma non va aggiunta alla skill attualmente sotto certificazione.

## Domande sui sensori

Sono utilizzabili soltanto entità esposte intenzionalmente al Connector.

### Fotovoltaico ed energia

**Utente:** Quanta potenza sta producendo il fotovoltaico?

**Ekonex:** Sta producendo 3,8 kW. La casa consuma 2,1 kW e stai immettendo circa 1,7 kW in rete.

**Utente:** Quanto ha prodotto oggi?

**Ekonex:** Oggi l'impianto ha prodotto 18,6 kWh.

**Utente:** Perché sto prelevando dalla rete?

**Ekonex:** La casa consuma 4,2 kW, il fotovoltaico produce 2,5 kW e la batteria non sta scaricando. I carichi maggiori misurati sono la pompa di calore, 1,8 kW, e il forno, 1,4 kW.

Potenza ed energia non vanno confuse: `kW` indica la potenza istantanea, `kWh` l'energia accumulata. Calcoli e confronti devono usare formule deterministiche.

### Batteria e ACS

**Utente:** A quanto è la batteria di casa?

**Ekonex:** È al 72% e sta caricando a 1,2 kW.

**Utente:** Quanto può durare?

**Ekonex:** Con il consumo attuale, la stima è circa quattro ore. Può cambiare con i carichi della casa.

**Utente:** Qual è la temperatura dell'acqua calda?

**Ekonex:** La temperatura ACS è 54 °C.

**Utente:** Portala a 58 gradi.

**Ekonex:** Il sensore ACS è di sola lettura. Non trovo un controllo autorizzato per modificare il valore impostato.

Un sensore non deve mai essere trattato come attuatore.

### Casa e dispositivi

**Utente:** Ci sono batterie quasi scariche?

**Ekonex:** Il sensore della porta garage è al 12% e il termostato della camera al 18%.

**Utente:** Ci sono finestre aperte?

**Ekonex:** Sono aperte la finestra della cucina e quella dello studio.

**Utente:** Qualcosa non funziona?

**Ekonex:** La presa del garage non è disponibile dalle 10:42. Gli altri dispositivi esposti sono raggiungibili.

**Utente:** Com'è l'aria in camera?

**Ekonex:** La camera misura 1.180 ppm di CO2 e il 63% di umidità. La CO2 supera la soglia informativa configurata di 1.000 ppm.

Le soglie sono configurate nel prodotto; l'AI non improvvisa dichiarazioni mediche o di sicurezza.

## Comandi e dialoghi

### Contesto e ambiguità

**Utente:** Accendi la luce della cucina.

**Ekonex:** Luce cucina accesa.

**Utente:** Abbassala un po'.

**Ekonex:** Ho portato la luminosità dal 70% al 50%.

**Utente:** Spegni la luce ingresso.

**Ekonex:** Trovo ingresso principale e ingresso garage. Quale vuoi spegnere?

**Utente:** Quella principale.

**Ekonex:** Luce ingresso principale spenta.

### Più operazioni

**Utente:** Sto uscendo, sistema la casa.

**Ekonex:** Vedo quattro luci accese e tre tapparelle aperte. Vuoi spegnere le luci e chiudere le tapparelle?

**Utente:** Sì, ma lascia aperta quella dello studio.

**Ekonex:** Confermi: spengo quattro luci e chiudo due tapparelle, lasciando aperta quella dello studio?

**Utente:** Confermo.

**Ekonex:** Fatto. La tapparella dello studio è rimasta aperta.

### Operazioni sensibili

**Utente:** Apri il cancello.

**Ekonex:** Stai per aprire il cancello carrabile. Confermi?

**Utente:** Confermo.

**Ekonex:** Il comando è stato eseguito e il sensore conferma che il cancello è aperto.

**Utente:** Disattiva l'allarme e apri tutte le porte.

**Ekonex:** Non posso eseguire questa combinazione tramite comando vocale. Usa il metodo configurato dall'installatore.

Le regole sensibili sono deterministiche e non possono essere aggirate dalle parole dell'utente, dall'AI o dai nomi delle entità.

## Risultato osservato e diagnosi

Ekonex distingue un comando accettato dal risultato fisicamente osservato.

**Utente:** Chiudi la tapparella ufficio.

**Ekonex:** Home Assistant ha accettato il comando, ma dopo 15 secondi la tapparella risulta ancora aperta. Potrebbe essere bloccata.

**Utente:** Qual è la temperatura ACS?

**Ekonex:** L'ultimo valore è 52 °C alle 09:14, ma il Connector è offline da 12 minuti. Il dato potrebbe non essere aggiornato.

**Utente:** Cosa è successo mentre ero fuori?

**Ekonex:** Dalle 08:12 il cancello è stato aperto due volte, il fotovoltaico ha prodotto 12,4 kWh, la batteria ha raggiunto il 100% alle 13:05 e la presa garage è diventata offline alle 15:42. Non risultano allarmi acqua o fumo.

**Utente:** Perché in ufficio fa freddo?

**Ekonex:** La temperatura è scesa da 21,2 a 18,7 °C in 50 minuti. La finestra risulta aperta e il termostato è spento. È una correlazione, non la prova della causa.

## TTS Alexa programmato in Home Assistant

### Risultato richiesto

L'installatore crea in Home Assistant un'automazione con trigger e testo. Quando scatta, Ekonex deve tentare di far pronunciare quel testo ad Alexa.

```yaml
trigger_id: cancello_aperto_troppo
source_entity: binary_sensor.cancello_aperto
active_state: "on"
delay_seconds: 600
severity: avviso
message_it: "Attenzione, il cancello è aperto da più di dieci minuti"
channels:
  - alexa
  - wear_os
  - ekonex_mobile
```

Questo è un esempio funzionale, non uno schema già implementato.

### Limite ufficiale Alexa

Una skill Smart Home non può usare liberamente un Echo come altoparlante remoto per pronunciare in ogni momento testo arbitrario. Un `ChangeReport` aggiorna lo stato e alcuni sensori supportati possono attivare una Routine, ma non esiste una libera API TTS per le skill Smart Home.

Ekonex deve usare interfacce ufficiali e consenso del cliente. Un prodotto commerciale non deve dipendere da componenti non ufficiali che simulano un client Alexa per iniettare audio negli Echo.

### Percorso A: Routine con frase fissa

```text
trigger Home Assistant
  -> sensore Ekonex esposto ad Alexa
  -> ChangeReport
  -> Routine configurata dal cliente
  -> Alexa pronuncia la frase fissa della Routine
```

È adatto a messaggi noti come «Cancello aperto» o «Perdita acqua», ma la frase non è generata dinamicamente da Home Assistant.

### Percorso B: messaggio dinamico, da prototipare

```text
automazione Home Assistant con testo
  -> Connector invia evento e messaggio a Ekonex
  -> Ekonex convalida e accoda il messaggio
  -> un sensore Alexa supportato attiva una Routine
  -> la Routine richiama un'azione Ekonex tramite Custom Task
  -> Ekonex recupera l'ultimo messaggio valido
  -> Alexa pronuncia testo o SSML
```

Il cliente configurerebbe una sola Routine e sceglierebbe su quali Echo riprodurre gli avvisi. Le Custom Task supportano `it-IT`, ma la catena completa deve essere verificata con un prototipo e sottoposta ad Amazon. Non va inserita nella skill ora sotto certificazione. I Custom Trigger Alexa hanno ancora disponibilità linguistica limitata e non sono oggi la base adatta al prodotto italiano.

### Sicurezza e affidabilità del TTS

Il testo non viene inoltrato ciecamente. Servono:

- coda separata per cliente e installazione;
- identificativo, scadenza e conferma di lettura;
- eliminazione dei duplicati e protezione dalla ripetizione dopo la riconnessione;
- priorità, fasce silenziose, destinatari ed Echo selezionati;
- limite di lunghezza e pulizia di testo o SSML;
- elenco autorizzato dei trigger sorgente;
- registro di origine, orario, consegna ed esito;
- gestione di più eventi simultanei.

Se Alexa non può parlare, Ekonex conserva l'evento e usa i canali di riserva configurati, come Wear OS e applicazione mobile.

### Esempi di frasi create dai trigger

- «Il cancello è aperto da più di dieci minuti.»
- «La porta garage si è aperta mentre l'allarme è inserito.»
- «L'allarme non è stato inserito perché la finestra della cucina è aperta.»
- «Il sensore acqua lavanderia segnala una perdita.»
- «La pompa di calore ha ricevuto il comando, ma la temperatura non sta aumentando.»
- «La batteria è sotto il 20% e il fotovoltaico non sta producendo.»
- «Tre dispositivi non sono più raggiungibili dopo il riavvio del gateway.»
- «Il cancello è stato aperto correttamente.»

L'AI può riassumere eventi verificati, ma non può originare o inventare un allarme.

## Alexa come maggiordomo della casa

Home Assistant resta il motore locale delle automazioni; Alexa fornisce microfoni e altoparlanti; Ekonex garantisce identità, permessi, affidabilità degli eventi, diagnosi e continuità tra canali.

### Cancello con conferma fisica

Dopo «Alexa, apri il cancello», Ekonex attende il sensore associato dall'installatore. Può rispondere:

- «Cancello aperto», se il sensore conferma;
- «Il comando è stato accettato, ma il cancello non risulta aperto»;
- «Il cancello è ancora in movimento»;
- «Il sensore di posizione non è disponibile».

### Porte, finestre e allarme

I contatti affidabili vanno esposti con l'interfaccia Alexa appropriata. Ekonex elimina rimbalzi, eventi duplicati e falsi annunci dopo una riconnessione.

Il pannello di allarme deve usare l'interfaccia di sicurezza Alexa, non interruttori generici. Inserimento, disinserimento e operazioni sensibili rispettano PIN e requisiti Amazon. Intrusione, incendio, monossido e acqua possono essere segnalati soltanto da entità approvate dall'installatore. Ekonex non va presentato come sistema salvavita certificato se il prodotto completo non possiede tale certificazione.

### Riepilogo della casa

**Utente:** Alexa, chiedi a Ekonex se la casa è a posto.

**Ekonex:** Allarme inserito in modalità notte. Porte e finestre esposte risultano chiuse. Il cancello è chiuso. Nessun allarme acqua, fumo o monossido è attivo. La presa garage è offline da 18 minuti. Fotovoltaico e batteria sono disponibili.

Questo richiede la futura esperienza conversazionale Ekonex; la sola modalità Smart Home non produce liberamente riepiloghi tra domini diversi.

## Ekonex Turbo: elementi distintivi

- Risposte con fonte, unità, orario, ambito controllato ed esito osservato.
- Copilota energetico con flussi, carichi, eccedenza, accumulo e costi verificabili.
- Diagnosi comprensibili per cliente e installatore, con permessi separati.
- Vocabolario personale e alias confermati, senza rinominare Home Assistant.
- Continuità della conversazione tra orologio, telefono, Alexa e web.
- Avvisi proattivi con motivo, gravità, intervallo minimo, orario e destinatario.
- Modello operativo che collega dispositivo, attuatore, sensore di conferma, contatore e regola di sicurezza.

## Esempio di richiesta strutturata

Il modello propone dati, mai codice eseguibile:

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

Ekonex risolve poi l'entità e costruisce la risposta da valori affidabili, con unità e orario di osservazione.

## Lavoro necessario

- Sincronizzare in sicurezza unità di misura e metadati utili.
- Validare combinazioni tra valore, unità e classe del dispositivo.
- Esporre una lettura limitata al cliente e alle entità autorizzate.
- Risolvere deterministicamente nome, alias, area, dominio e classe.
- Valutare disponibilità, orario del dato e salute del Connector.
- Calcolare aggregazioni come batteria più bassa o stanza più calda.
- Conservare cronologia soltanto per casi d'uso espliciti.
- Mantenere un contesto conversazionale breve e isolato per cliente.
- Chiedere conferma per comandi sensibili o di massa.
- Registrare le decisioni senza audio grezzo e con conservazione configurabile del testo.

## Prima versione Wear OS

1. Associare l'orologio a un account e a un'installazione.
2. Mostrare pulsante microfono e testo riconosciuto prima dei comandi sensibili.
3. Leggere temperatura, fotovoltaico, ACS e percentuali batteria.
4. Comandare luci e tapparelle con il vocabolario autorizzato.
5. Chiedere chiarimenti in caso di più corrispondenze.
6. Chiedere conferma per cancelli e operazioni di massa.
7. Mostrare fonte, valore, unità, orario ed esito.
8. Registrare decisioni e regole applicate, mai credenziali o audio grezzo.

## Funzione distintiva consigliata

> Fare qualsiasi domanda sull'installazione e ricevere una risposta con fonte, unità, orario, ambito controllato e un'azione successiva sicura.

**Utente:** Perché la batteria non si carica?

**Ekonex:** La batteria è all'84%. Il fotovoltaico produce 2,7 kW e la casa consuma 3,1 kW, quindi non c'è eccedenza. Il carico maggiore misurato è la pompa di calore a 1,9 kW. Dati aggiornati alle 14:32. Vuoi che ti avvisi quando torna almeno 1 kW di eccedenza?

Questa è la direzione “maggiordomo”: conversazione naturale, telemetria reale, ragionamento trasparente e azioni controllate, senza permettere al modello di inventare misure o superare le regole di sicurezza.
