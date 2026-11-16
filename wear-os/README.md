# Ekonex Wear OS - CHANGE081

Client ufficiale per dispositivo utente associato a e-Control. Non contiene
tenant, impianto o token condivisi. L'installatore genera l'offerta da
e-Control; sull'orologio si inserisce il codice monouso. La credenziale
esclusiva viene cifrata con Android Keystore, il backup e disabilitato e ogni
richiesta usa esclusivamente `https://voice.e-control.tech/api/wear/v1`.

La build debug serve alla qualifica tecnica. La distribuzione cliente richiede
la chiave aziendale stabile e il collaudo fisico indicato in CHANGE081.
