# Prova locale dell'IA Ekonex

Questa prima prova non utilizza Alexa, non modifica la produzione e non invia dati a servizi esterni.
Serve a verificare il confine di sicurezza tra il linguaggio naturale e i dati autorizzati di Home
Assistant.

## Avvio della demo

Da PowerShell, nella cartella principale del progetto:

```powershell
.\.venv\Scripts\python.exe scripts\demo_conversation.py
```

Provare, per esempio:

```text
Quanto produce il fotovoltaico?
Qual è la temperatura dell'acqua calda?
A quanto è la batteria di casa?
Quanti gradi ci sono in soggiorno?
Disattiva l'allarme e apri il cancello
```

La demo mostra anche l'entità usata come fonte. L'ultima frase deve essere rifiutata perché questa
versione è esclusivamente informativa.

Per terminare, scrivere `esci`.

## Prove automatiche

Su Windows il componente di test Home Assistant richiede `fcntl`, disponibile soltanto su sistemi
Unix. I test puri del motore possono essere eseguiti senza caricare quel componente:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -m pytest --confcutdir=apps/cloud_api/tests apps/cloud_api/tests/test_conversation.py -q
Remove-Item Env:PYTEST_DISABLE_PLUGIN_AUTOLOAD
```

I test verificano:

- risposta fotovoltaica accompagnata dalla fonte;
- riconoscimento degli alias ACS;
- chiarimento quando due sensori sono equivalenti;
- scelta della temperatura tramite area;
- rifiuto di valori `unknown`;
- segnalazione di un dato vecchio;
- distinzione tra potenza `kW` ed energia `kWh`;
- impossibilità di trasformare una richiesta non supportata in comando.

## Passaggio successivo: dati reali in sola lettura

Il passo successivo collegherà il motore alle entità già sincronizzate nel cloud, con questi vincoli:

1. il cliente e l'installazione vengono determinati dall'autenticazione;
2. vengono caricate soltanto entità autorizzate e non eliminate;
3. la risposta include valore, unità, disponibilità e orario dell'ultima osservazione;
4. nessun comando viene inviato al Connector;
5. Alexa e la skill in certificazione restano completamente escluse dalla prova.

Solo dopo questa verifica si aggiungeranno il modello linguistico e, separatamente, comandi con
regole deterministiche e conferma esplicita.
