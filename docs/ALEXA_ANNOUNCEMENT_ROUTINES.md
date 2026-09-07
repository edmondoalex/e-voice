# Routine vocali Alexa

Stato: implementazione locale predisposta per il laboratorio. Non distribuire sul backend o sulla
skill Alexa di produzione durante la certificazione Amazon.

## Responsabilità

- Alexa Devices fornisce a Home Assistant le entità `notify.*_announce` e `notify.*_speak`.
- Ekonex Voice sincronizza soltanto le entità esplicitamente esposte dall'installatore.
- Il portale definisce nome, modalità e destinatari della routine.
- Home Assistant rileva il trigger e risolve eventuali template usando gli stati locali.
- La VPS autorizza la routine e instrada comandi astratti verso l'installazione già autenticata.
- Il connettore consente esclusivamente `notify.send_message` verso entità Alexa Devices esposte.

La VPS non riceve credenziali Amazon e non usa Alexa Media Player. Il testo non viene conservato
nei log di comando: diagnostica e audit registrano soltanto operazione e lunghezza.

## Configurazione

1. Installare e configurare l'integrazione ufficiale Alexa Devices in Home Assistant.
2. Aggiungere una seconda istanza Ekonex Voice scegliendo **Laboratorio**. La configurazione
   esistente di produzione resta collegata al suo endpoint.
3. Aprire il link di associazione mostrato da Home Assistant: per questa istanza deve iniziare con
   `https://voice-lab.e-control.tech/pair`.
4. Nelle opzioni dell'istanza laboratorio esporre i dispositivi Echo oppure le singole entità
   `notify.*_announce`/`notify.*_annuncio`, `notify.*_speak`/`notify.*_parla` e i relativi
   `media_player` desiderati.
5. Attendere la sincronizzazione dell'inventario.
6. Nel portale laboratorio aprire **Routine vocali**.
7. Creare tutti i gruppi Ekonex desiderati scegliendo per ciascuno i relativi Echo.
8. Creare una routine indicando destinatario, modalità, volume facoltativo e, se utile, un testo
   predefinito.
9. Copiare lo slug mostrato dal portale nell'automazione Home Assistant.

`Ovunque` usa tutte le entità Announce esposte. Per evitare ripetizioni, non esporre
contemporaneamente gli Echo singoli e un gruppo Alexa che contiene gli stessi Echo; in alternativa
creare una routine diretta al solo gruppo Alexa `Ovunque`. L'integrazione Alexa Devices consiglia
i gruppi Alexa quando molti dispositivi potrebbero subire il rate limiting Amazon.

## Messaggio scritto nel portale

Se la routine contiene un messaggio predefinito, Home Assistant può richiamarla senza `message`:

```yaml
actions:
  - action: ekonex_voice.run_voice_routine
    data:
      routine: avviso_cancello
```

## Messaggio con valore di una variabile

Home Assistant elabora il template localmente prima dell'invio:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.temperatura_puffer
    above: 75
actions:
  - action: ekonex_voice.run_voice_routine
    data:
      routine: avviso_centrale_termica
      message: >-
        Attenzione. Il puffer ha raggiunto
        {{ states('sensor.temperatura_puffer') | round(0) }} gradi.
```

## Messaggio proveniente dall'evento del trigger

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.porta_garage
    to: "on"
actions:
  - action: ekonex_voice.run_voice_routine
    data:
      routine: sicurezza_piano_terra
      message: >-
        La porta del garage è stata aperta alle
        {{ now().strftime('%H:%M') }}.
```

Quando convivono una connessione Produzione e una Laboratorio, la routine seleziona
automaticamente l'unica connessione Laboratorio. `installation_id` serve soltanto se sono presenti
più connessioni dello stesso ambiente.

## Limiti e sicurezza

- Testo semplice, massimo 500 caratteri; markup e caratteri di controllo vengono rifiutati.
- Routine, gruppi ed entità sono sempre confinati a tenant e installazione.
- Un'entità rimossa, disabilitata, non esposta o non disponibile non viene comandata.
- La modalità `announce` riproduce prima il segnale di notifica; `speak` pronuncia direttamente.
- Il volume della routine è facoltativo (0–100%). Se assente, e-Voice non modifica il volume;
  se presente, usa esclusivamente il `media_player` Alexa Devices associato allo stesso dispositivo.
- Il volume impostato resta attivo dopo l'annuncio: un ripristino immediato potrebbe interrompere o
  alterare la riproduzione, la cui durata non viene comunicata da Amazon.
- Il servizio diretto `ekonex_voice.announce` resta disponibile per automazioni interamente locali.
- Un trigger di routine richiede la credenziale Connector già memorizzata nella ConfigEntry.

## Gate di rilascio

Per una prova end-to-end servono nello stesso ambiente: migrazioni `0015` e `0016`, backend
laboratorio aggiornato, componente beta aggiornato, entità Alexa Devices esposte e una sessione
Connector collegata al backend laboratorio. La promozione in produzione richiede autorizzazione
esplicita dopo la certificazione Amazon.
