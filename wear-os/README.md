# Ekonex Wear OS - CHANGE081

Client ufficiale per dispositivo utente associato a e-Control. Non contiene
tenant, impianto o token condivisi. L'installatore genera l'offerta da
e-Control; sull'orologio si inserisce il codice monouso. La credenziale
esclusiva viene cifrata con Android Keystore, il backup e disabilitato e ogni
richiesta usa esclusivamente `https://voice.e-control.tech/api/wear/v1`.

La build corrente e versionata come `0.1.0`. Su Windows `./build.ps1` crea
`build/ekonex-wear-0.1.0-production.apk`; la distribuzione cliente definitiva
richiede comunque la chiave aziendale stabile e il canale Google Play Wear OS.
