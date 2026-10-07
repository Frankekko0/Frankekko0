# PROGRESS

Stato del lavoro "massimo livello" (analisi, autenticità, estensione veloce, live, Preferiti/Acquista).
Aggiornato a ogni passo: **fatto**, **in corso**, **ipotesi**.

## Fatto
- **Bug foto profilo nelle analisi** (verificato: test server + estensione + integrazione).
  - Causa: i parser (server ed estensione) prendevano ogni `full_size_url` e ogni `favourite_count`/`feedback_*` negli script della pagina: lì ci sono anche l'utente collegato (la tua foto profilo e le tue 999 recensioni), l'avatar del venditore e gli articoli suggeriti. Con la versione vecchia, sulla pagina di prova: prima foto = tua foto profilo, preferiti/visualizzazioni di un suggerito, venditore = tu.
  - Correzione per costruzione: si individua l'oggetto JSON dell'articolo con il suo ID (`app/acquisition/embedded.py`, stesso algoritmo in `extension/src/parse.js`) e si leggono solo i suoi campi: foto della galleria, contatori, venditore (`user` dell'articolo). Le foto profilo trovate nella pagina vengono comunque escluse. Nel DOM solo il contenitore della galleria; sulle schede l'immagine dell'articolo, mai l'avatar. Configurazione in `vinted_parser.json` (`item_json`, `gallery_container`, `avatar_images`, `card_*`); rimossi i vecchi pattern globali.
  - Una cattura dalla galleria (`images_source=item_json|gallery_dom`) sostituisce sempre le foto salvate, anche se sono meno.
  - Pulizia dei dati salvati: `app/media/cleanup.py` (job `clean_foreign_data_task` all'avvio del worker e ogni giorno; `python -m app.tools.clean_images [--dry-run]`): rimuove immagini condivise da annunci diversi, azzera i profili venditore copiati, toglie l'analisi foto e rianalizza.
  - Test: `tests/fixtures/vinted/item_with_avatars.html` (pagina salvata da collegato con tua foto, avatar, suggeriti, logo, banner; dati divisi in due blocchi), test parser Python e JS, `tests/integration/test_foreign_images.py`.

- **Obiettivo 1 – analisi prodotto** (verificato: 256 test backend, 23 estensione, 16 frontend, tsc/eslint/ruff).
  - Profitto atteso corretto per il rischio = margine netto × P(vendita entro 30 gg) × P(autentico): colonna `risk_adjusted_profit` (migrazione 0006), ordinamento predefinito del feed (`sort=expected`, sui costi dell'utente), classifica dell'estensione.
  - P(vendita): quota pesata di annunci simili venduti entro 30 giorni (rimossi = non venduti, attivi da meno di 30 gg ignorati); con meno di 6 esiti si usa la quota del segmento dichiarandolo, altrimenti "dati insufficienti".
  - P(autentico) e verdetto (`app/authenticity/assess.py`, regole per brand in `authenticity_rules.json`): 4 esiti, mai sopra 97%, nuovo venditore da solo mai "falso", senza analisi foto = "non verificabile", foto da chiedere + messaggio pronto.
  - Prezzo: con almeno 5 venduti simili il prezzo lo fanno solo le vendite reali (gli annunci in vendita restano visibili come "solo riferimento"). Regola "solo stesso modello" provata e scartata: peggiorava l'errore.
  - Calibrazione (`app/analytics/calibration.py`, `accuracy.py`, job giornaliero + all'avvio): minimo/massimo dagli errori misurati; lo spostamento del prezzo probabile si applica solo se riduce l'errore sulle vendite più recenti. Endpoint `/analytics/accuracy`, scheda "Estimate accuracy".
  - Misura (mercato simulato, 399 vendite più recenti stimate solo con dati precedenti): errore medio €18,13 → €17,66; errore mediano 22,0% → 20,7%; sovrastima media €9,33 → €8,72; vendite dentro min–max 44% → 79%.
  - Pagina Analisi: sezione Decisione (motivo in 3 righe, scomposizione margine/domanda/sicurezza/venditore, min/probabile/max, margine, tempo, P(vendita), prezzo massimo, offerta, ognuno con confidenza), Autenticità (prove foto per foto con riquadro sul dettaglio), Domanda/Venditore/Prodotto (preferiti/giorno, ribassi, quota venduti per taglia e colore, stagionalità solo con 12 mesi, tracking, abitudine del venditore a ribassare, brand scritto male e categoria sbagliata come occasioni nascoste, condizione dichiarata vs foto).
  - Condizione effettiva: se le foto mostrano difetti certi/probabili peggiori del dichiarato, il prezzo si stima sulla condizione peggiore.

- **Obiettivo 2 – autenticità da ogni foto** (verificato: 265 test backend; set etichettato).
  - Ogni foto (fino a 20, URL a piena risoluzione) scaricata e controllata in locale: dimensioni, nitidezza (rapporto dettaglio piena risoluzione/miniatura, indipendente dal contenuto), luce, forma da screenshot. Le foto inutilizzabili rendono i dettagli "non verificabili", mai "coerenti".
  - Hash percettivo di ogni foto salvato e confrontato con le foto di tutti gli altri annunci (distanza ≤ 4 bit su 64): presente presso 1–2 altri venditori = foto riciclata, presso 3+ = foto di catalogo/stock; i ripubblicati dallo stesso venditore non contano (`app/vision/provenance.py`).
  - Analisi AI foto per foto (se configurata): foto numerate, regole del brand nel prompt, per ogni dettaglio esito/certezza/riquadro [x,y,l,a]; screenshot, filigrane, foto ritoccate o generate.
  - Verdetto: "probabilmente autentico" solo con almeno 2 foto chiave lette con certezza e nessun segnale contrario; un difetto su un dettaglio chiave fa scattare "a rischio falso"; parole da replica pesano molto di più.
  - Venditore: stesso articolo nuovo in più taglie (o 3+ copie) = segnale debole, mai decisivo da solo.
  - Set di prova etichettato (`tests/fixtures/authenticity/labeled_cases.json`, 16 autentici + 16 falsi, 3 "difficili"; `python -m app.tools.auth_eval`): falsi negativi 0/16, falsi positivi 0/16, falsi segnalati "a rischio" 15/16; l'unico mancato (falso perfetto senza difetti visibili) resta "incerto", mai "autentico".

- **Obiettivo 3 – estensione veloce** (verificato: `extension/e2e/speed.e2e.cjs` in Chromium con l'estensione vera su pagina da 96 articoli e 1 MB; 28 test JS; e2e completo ancora verde).
  - Primo tempo, verdetto rapido: il server prepara un riepilogo di mercato (`/extension/market-cache`, 12 KB: prezzi dei venduti per brand×categoria, quota venduta in 30 gg, tempi, i tuoi costi, regole antifalso), il service worker lo scarica ogni 3 h e lo salva; la pagina lo legge dallo storage mentre si carica e valuta tutte le schede in locale (`src/quick.js`, stesse formule del server, verificate con valori del calcolatore Python). La migliore (profitto corretto per il rischio più alto e positivo) è evidenziata con bordo verde e un riquadro "Migliore della pagina" con profitto atteso e motivo; clic = scorre alla scheda.
  - Secondo tempo, in background: tutte le schede vanno al server a blocchi (analisi completa con i tuoi costi), che sostituisce le stime; foto in background per le opportunità.
  - Cache per ID: valutazioni del server conservate 12 h (anche dopo riavvio del browser), in memoria nel service worker; sessioni del pannello in memoria con salvataggio differito (prima ogni scheda riscriveva tutto e la pagina aspettava).
  - Misure: migliore evidenziata 17–53 ms dopo che il browser ha letto la pagina (78–93 ms con CPU 4× più lenta, 24–26 ms con FlipFinder spento); lavoro dell'estensione con badge a blocchi senza bloccare; nessun task lungo durante lo scorrimento. Dalla navigazione 0,56–1,4 s su questa macchina: è il caricamento della pagina stessa (server e browser sulla stessa CPU).
  - Offline: nessun blocco, verdetto dal riepilogo salvato; la coda si svuota quando FlipFinder torna raggiungibile. Selettori sempre nel modulo aggiornabile (`vinted_parser.json` → `parser-config.js`, aggiornato dal server).

- **Obiettivo 4 – via i demo, produzione** (verificato: 266 test backend, migrazione 0007 su DB usa-e-getta, stack di produzione avviato davvero con Docker).
  - Backup del DB di sviluppo prima di tutto: `pg_dump` (28 MB) nella cartella di lavoro della sessione.
  - Eliminati: provider simulato (`app/marketplace/mock`), immagini demo (`/api/v1/demo`), account e accesso demo, impostazioni `SEED_DEMO_USER`/`MOCK_*`, eccezioni per i dati simulati. Senza fonte configurata (`MARKETPLACE_PROVIDER=none`, predefinito) non c'è scanner: nessun ripiego su dati finti.
  - Migrazione `0007`: cancella annunci/venditori simulati (con foto, storico, analisi, avvisi, log), account demo, prodotti orfani, statistiche/calibrazione/apprendimento ricavati dal mercato simulato; i tuoi acquisti/vendite/catture restano.
  - Interfaccia: niente "Try the demo" né etichette demo; stati vuoti con indicazione concreta (estensione, import); indicatore "Your captures" e "Scan now" solo se esiste un feed.
  - Produzione (`docker-compose.prod.yml`, `deploy/`): Caddy con HTTPS automatico (o Tailscale), API e web app non esposte, registrazione chiusa dopo il primo account, cookie Secure, backup prima di ogni avvio e ogni giorno con rotazione + ripristino, registro errori su volume visibile in Impostazioni → Error log.
  - Verifiche sullo stack reale: HTTP→HTTPS 308, HSTS/CSP, owner 201 e secondo account 400, cookie Secure+HttpOnly, backup scritti, 16 pagine/schede senza tracce demo né errori, estensione collegata via HTTPS (sync ok, 24 annunci salvati). Trovati e corretti 3 difetti reali: segreto JWT non passato ai container, Caddy bloccato da e-mail vuota, cartella log non scrivibile.

- **Obiettivi 5-6: tasti Preferiti e Acquista** (pagina Analisi; Acquista anche in tracking e nel pannello)
  - Backend: tabella `marketplace_actions` (migrazione `0008`), `GET/POST /listings/{id}/vinted`, `POST /capture/vinted-actions` (chiave dell'estensione). "Acquistato" crea l'acquisto una sola volta con il prezzo dell'articolo e, dal totale pagato, la spedizione.
  - Estensione: ponte solo sull'indirizzo di FlipFinder autorizzato, attivo solo dopo un clic reale. Il cuore viene premuto una volta e poi confermato. Acquista verifica stato e prezzo, e solo un secondo clic apre il checkout. L'acquisto è attribuito solo alla scheda in cui è stato avviato il checkout, e un checkout abbandonato non viene registrato.
  - Verifica e2e (`extension/e2e/actions.e2e.cjs`, estensione vera, Vinted finto in HTTPS):
    - richiesta senza clic rifiutata (`no_click`), azione non prevista rifiutata;
    - aggiunta e rimozione dai preferiti allineate tra Vinted e FlipFinder;
    - cuore cambiato su Vinted → riallineato alla visita successiva;
    - utente non collegato a Vinted → avviso, nessun clic;
    - checkout aperto a 20 € → pagamento confermato → registrato 25,19 € pagati, nei Flip prezzo 20 € e costo totale 25,19 €;
    - prezzo cambiato (30 → 27 €) e articolo riservato segnalati prima del checkout;
    - pannello: prezzo cambiato 27 → 25 €, poi checkout aperto;
    - checkout abbandonato non registrato come acquisto.
  - Difetti reali trovati dal test e corretti:
    - schede dell'estensione non raggiungibili dal pannello aperto come scheda;
    - lettura del cuore inviata prima che l'annuncio fosse registrato (404);
    - totale pagato perso quando la conferma compare dopo il caricamento;
    - possibile attribuzione di un acquisto all'articolo sbagliato.

## Da fare da te
- Eliminare i dati demo dal DB di sviluppo di questa sessione: la cancellazione è stata bloccata dal controllo di sicurezza (backup già fatto). Sul tuo server la migrazione 0007 lo fa da sola all'avvio, dopo il backup automatico.

## In corso
- Nessuno.

## Ipotesi
- Le misure di errore sono sul mercato simulato (nel DB c'è 1 sola vendita reale): sulle tue vendite reali la calibrazione si attiva da sola dopo 30 vendite osservate; le tue rivendite pesano 5 volte.
- Il set di prova autenticità è fatto di scenari (esiti dell'analisi foto + prezzo + venditore), non di foto reali: misura le regole di decisione; la qualità della lettura delle foto dipende dal modello AI configurato.
- Soglie foto: nitidezza 0,30 tarata su immagini sintetiche (nitide ≥ 0,35, sfocate ≤ 0,28); hash ≤ 4 bit = stessa foto.
- Velocità su Vinted reale non misurabile da qui (nessun accesso): la pagina di prova riproduce struttura e peso di una ricerca da 96 articoli; il tempo dell'estensione dopo la lettura della pagina non dipende da Vinted.
- Verdetto rapido: categoria riconosciuta dal titolo con le parole chiave della tassonomia; se la categoria non ha abbastanza vendite si usa il brand intero (confidenza ridotta); senza dati "dati insuff.".
- Produzione verificata con dominio `localhost` (certificato locale di Caddy): con un dominio vero Caddy usa Let's Encrypt, non verificabile da qui.
- Tempo di risposta del venditore: Vinted non lo mostra nella pagina articolo, quindi è "non disponibile" (non stimato).
- Prezzo di listino e periodo di uscita: nessuna fonte pubblica affidabile; mostrato solo il prezzo originale dichiarato dal venditore nel testo.
- La pagina "salvata" del test è costruita sulla struttura nota di Vinted (non ho una tua pagina reale): se ne salvi una (Ctrl+S) in `backend/tests/fixtures/vinted/real/`, va aggiunta ai test.
- Foto di articoli suggeriti presenti una sola volta in analisi salvate con la versione vecchia non sono riconoscibili dall'URL: vengono sostituite alla prossima apertura dell'annuncio (cattura dalla galleria = autorevole).
- Preferiti/Acquista: i selettori del cuore, del tasto "Acquista" e della pagina di pagamento completato sono costruiti sulla struttura nota di Vinted, non verificati su Vinted reale (nessun accesso da qui). Se cambiano si correggono in `vinted_parser.json`, senza ripubblicare l'estensione. In caso di dubbio l'estensione si ferma e lo dice, non clicca a caso.
- Il test e2e dei tasti gira su un database separato e vuoto (`flipfinder_e2e`), perché il DB di sviluppo non può passare alla migrazione 0008 senza la 0007 bloccata. Il backend di sviluppo sulla porta 8000 è ancora la versione precedente.
- Permesso `scripting` aggiunto all'estensione, e aggiornato il test che fissa l'elenco dei permessi: è l'unico modo per agganciare la pagina di FlipFinder a un indirizzo scelto da te, e non dà accesso a nuovi siti.
- Acquisto registrato solo dalla pagina di conferma nella stessa scheda del checkout avviato da FlipFinder. Se paghi altrove usa "I bought it". Se il totale non è leggibile si registra il prezzo dell'articolo.
