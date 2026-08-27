"""Public legal information required by the Alexa skill listing."""

# ruff: noqa: E501

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal"])

_STYLE = """
body{margin:0;background:#f5f7fa;color:#172033;font:16px/1.6 system-ui,-apple-system,sans-serif}
main{max-width:820px;margin:0 auto;padding:48px 24px 72px}article{background:#fff;padding:36px;
border-radius:16px;box-shadow:0 8px 30px #17203314}h1{margin-top:0;color:#16283d}h2{margin-top:2em}
a{color:#0866c6}footer{margin-top:32px;color:#526070;font-size:.9rem}li{margin:.4em 0}
"""


def _page(title: str, content: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='it'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title} | Ekonex Voice</title><style>{_STYLE}</style></head>"
        f"<body><main><article>{content}<footer>Ultimo aggiornamento: 27 agosto 2026 · "
        "<a href='/privacy'>Privacy</a> · <a href='/terms'>Termini di utilizzo</a>"
        "</footer></article></main></body></html>"
    )


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_policy() -> HTMLResponse:
    """Publish the Italian privacy policy without requiring authentication."""

    return _page(
        "Informativa sulla privacy",
        """
        <h1>Informativa sulla privacy di Ekonex Voice</h1>
        <p>Ekonex, contattabile all’indirizzo <a href="mailto:info@ekonex.it">info@ekonex.it</a>,
        tratta i dati necessari a fornire Ekonex Voice e a collegare gli impianti autorizzati con
        Amazon Alexa.</p>
        <h2>Dati trattati</h2>
        <ul>
          <li>dati dell’account, come indirizzo e-mail, identificativi utente e autorizzazioni;</li>
          <li>identificativi e configurazione dell’impianto e del Connector Home Assistant;</li>
          <li>nomi, tipi, funzionalità e stati dei dispositivi che l’utente sceglie di sincronizzare;</li>
          <li>comandi, risultati, eventi tecnici e dati diagnostici necessari a sicurezza e assistenza;</li>
          <li>token OAuth e credenziali tecniche, conservati in forma cifrata o non reversibile ove
          applicabile.</li>
        </ul>
        <p>Ekonex Voice non riceve da Alexa la registrazione audio né, normalmente, la trascrizione
        completa della frase pronunciata. Riceve le direttive Smart Home generate da Amazon.</p>
        <h2>Finalità e base del trattamento</h2>
        <p>I dati sono usati per autenticare l’utente, sincronizzare i dispositivi, eseguire i comandi,
        mostrare attività e diagnostica, prevenire abusi e mantenere il servizio affidabile. Il
        trattamento è necessario all’esecuzione del servizio richiesto e, per sicurezza e
        miglioramento tecnico, al legittimo interesse del fornitore.</p>
        <h2>Amazon Alexa e altri fornitori</h2>
        <p>Quando l’utente collega Alexa, le informazioni strettamente necessarie su dispositivi,
        capacità e stato sono comunicate ad Amazon. Amazon tratta tali dati secondo la propria
        informativa. Possono inoltre essere impiegati fornitori di infrastruttura e hosting vincolati
        alla riservatezza e alle istruzioni del titolare.</p>
        <h2>Conservazione e sicurezza</h2>
        <p>I dati sono conservati per il tempo necessario a fornire il servizio, rispettare obblighi
        applicabili e gestire sicurezza, audit e contestazioni. Ekonex adotta isolamento tra clienti,
        controllo degli accessi, cifratura delle credenziali e registrazione degli eventi rilevanti.</p>
        <h2>Scelte e diritti dell’utente</h2>
        <p>L’utente può revocare l’accesso disabilitando la skill Ekonex Voice nell’app Alexa. Può
        richiedere accesso, rettifica, cancellazione, limitazione o portabilità dei propri dati e
        opporsi al trattamento scrivendo a <a href="mailto:info@ekonex.it">info@ekonex.it</a>.
        L’interessato può inoltre rivolgersi all’autorità di controllo competente.</p>
        <h2>Modifiche</h2>
        <p>Questa informativa può essere aggiornata per riflettere evoluzioni del servizio o della
        normativa. La versione corrente è sempre pubblicata a questo indirizzo.</p>
        """,
    )


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms_of_use() -> HTMLResponse:
    """Publish the Italian terms of use without requiring authentication."""

    return _page(
        "Termini di utilizzo",
        """
        <h1>Termini di utilizzo di Ekonex Voice</h1>
        <p>I presenti termini disciplinano l’uso di Ekonex Voice e della relativa integrazione Amazon
        Alexa. Utilizzando il servizio, l’utente conferma di essere autorizzato a controllare gli
        impianti e i dispositivi collegati al proprio account.</p>
        <h2>Requisiti del servizio</h2>
        <p>Sono necessari un account Ekonex Voice attivo, un impianto correttamente configurato,
        componenti compatibili e connettività Internet. Le funzioni disponibili dipendono dalle
        capacità effettivamente dichiarate dal dispositivo e dai servizi Amazon Alexa.</p>
        <h2>Uso consentito</h2>
        <p>L’utente deve proteggere le proprie credenziali, assegnare nomi vocali non ambigui e usare il
        servizio nel rispetto della legge e dei diritti altrui. È vietato tentare accessi non
        autorizzati, interferire con il servizio o utilizzarlo per finalità illecite.</p>
        <h2>Funzionamento e disponibilità</h2>
        <p>I comandi vocali dipendono anche da Amazon Alexa, dalla rete, da Home Assistant, dal
        Connector e dai dispositivi fisici. Ekonex non garantisce disponibilità ininterrotta né che
        ogni formulazione vocale sia interpretata allo stesso modo. Il servizio non deve essere usato
        come unico sistema per funzioni di sicurezza, emergenza o protezione della vita.</p>
        <h2>Modifiche e sospensione</h2>
        <p>Ekonex può aggiornare il servizio per sicurezza, compatibilità o miglioramenti. L’accesso può
        essere limitato o sospeso in caso di abuso, rischio per la sicurezza, cessazione del rapporto
        o manutenzione necessaria.</p>
        <h2>Responsabilità</h2>
        <p>Nei limiti consentiti dalla legge, Ekonex non risponde di indisponibilità o comportamenti
        causati da servizi terzi, configurazioni dell’utente, connettività o dispositivi non sotto il
        proprio controllo. Restano impregiudicati i diritti inderogabili del consumatore.</p>
        <h2>Disattivazione e assistenza</h2>
        <p>L’utente può interrompere il collegamento disabilitando la skill nell’app Alexa. Per
        assistenza, richieste sui dati o chiusura dell’account può scrivere a
        <a href="mailto:info@ekonex.it">info@ekonex.it</a>.</p>
        <h2>Legge applicabile</h2>
        <p>Si applica la legge italiana, fatti salvi i diritti e i fori inderogabili previsti per i
        consumatori.</p>
        """,
    )
