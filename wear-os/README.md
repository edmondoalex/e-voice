# Ekonex Wear OS - CHANGE081

Client ufficiale per dispositivo utente associato a e-Control. Non contiene
tenant, impianto o token condivisi. L'installatore genera l'offerta da
e-Control; sull'orologio si inserisce il codice monouso. La credenziale
esclusiva viene cifrata con Android Keystore, il backup e disabilitato e ogni
richiesta usa esclusivamente `https://voice.e-control.tech/api/wear/v1`.

La build corrente e versionata come `0.1.2` (`versionCode 3`). Ogni modifica
deve incrementare entrambi i valori: una versione gia distribuita non viene
mai ricostruita o riutilizzata.

Su Windows la build di produzione richiede esplicitamente la chiave stabile:
`./build.ps1 -Environment Production -KeystorePath <percorso-chiave>`. Il
processo rifiuta una firma diversa dal certificato originale Ekonex, cosi gli
aggiornamenti conservano associazione e preferenze. La `0.1.1`/code 2 e
ritirata per firma incompatibile e non deve essere distribuita.

## Note release 0.1.2

- ripristino automatico del servizio notifiche dopo riavvio o aggiornamento;
- aggiornamento gestito da e-Control senza cancellare dati e associazione;
- firma compatibile con la `0.1.0` gia installata sui dispositivi di prova.
