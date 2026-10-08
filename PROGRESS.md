# PROGRESS

Stato del lavoro "massimo livello" (analisi, autenticità, estensione veloce, live, Preferiti/Acquista).
Aggiornato a ogni passo: **fatto**, **in corso**, **ipotesi**.

## Fatto
- **Obiettivo 7 – prezzi dalle vendite concluse e da altri mercati** (verificato: 469 test backend, benchmark sintetico).
  - Nuove tabelle (migrazione `0009`): `sold_sales` (vendite concluse: articolo, modello, taglia, condizione, prezzo, data, giorni per vendere, fonte), `external_prices` (prezzi trovati fuori da Vinted: tipo nuovo/richiesto/venduto, fonte, data, valuta, condizione, link), `external_searches` (cache per modello), `model_price_stats` (statistiche pre-calcolate per brand, categoria, modello, taglia e condizione: mediana, fascia bassa/alta, campioni per fonte, giorni per vendere, quota venduta, prezzo da nuovo). Indici nuovi per i comparabili per modello.
  - Fonti delle vendite concluse, in ordine di affidabilità: tue vendite (prezzo incassato) > tuoi acquisti (prezzo pagato) > articoli Vinted visti passare a "venduto" (ultimo prezzo rilevato, giorni dalla pubblicazione) > vendite pubblicate da altri mercati. Un articolo che hai comprato conta una volta sola. Registrazione immediata dopo ogni cattura, controllo o email che vede la vendita, e dopo ogni acquisto o vendita inseriti; sincronizzazione completa ogni notte, incrementale ogni 30 minuti (`app/market/*`).
  - Sconto da trattativa: mediana di 1 − pagato/richiesto sui tuoi acquisti (almeno 3), applicato solo agli "ultimi prezzi rilevati" di Vinted; finché non è misurabile non si applica e lo si dice.
  - Pesi nelle stime: tue vendite 5, tuoi acquisti e venduti Vinted 1,5, venduti di altri mercati 1, prezzi richiesti Vinted 1 × fattore ridotto, richiesti di altri mercati 0,5 × fattore ridotto. I tuoi acquisti non pesano come le tue vendite: compri sotto mercato, quindi il prezzo pagato è esatto ma più basso del valore di rivendita. Con almeno 5 vendite concluse (da qualunque fonte) i prezzi richiesti sono solo riferimento. Il prezzo da nuovo non è mai un comparabile.
  - Controllo automatico (`app/analytics/evidence.py`, ogni giorno e con `python -m app.tools.price_eval`): le stesse vendite stimate con soli annunci, + tuoi dati, + altri mercati, ognuna solo con i dati precedenti (un prezzo esterno vale dalla sua data). Una fonte resta accesa solo se non aumenta l'errore medio sulla metà più recente delle vendite; con meno di 30 casi "non ancora misurabile" e peso ridotto.
  - Ogni analisi salva da dove arriva ogni numero (`provenance`): prezzo probabile, minimo–massimo, giorni per vendere, probabilità di vendita, prezzo da nuovo, quante vendite reali ci sono dietro (tue / Vinted / altri mercati), riferimenti esterni usati e no. Pagina Analisi: "Basato su N vendite reali" e la sezione "Where the numbers come from".
  - Ricerca esterna (`app/external/*`, `docs/EXTERNAL_PRICES.md`): API ufficiale Serper.dev (risultati Google), mai lettura di Google. Per modello: Google Shopping "brand modello" (prezzi da nuovo e usati) + ricerca web "brand modello usato prezzo" sui mercati dell'usato (eBay, Vestiaire, Depop, Grailed, Subito, Wallapop; Vinted escluso) + eventuale terza ricerca dei venduti. Abbinamento rigoroso al modello esatto con ogni scarto contato: altri modelli del brand (Air Max 95 per Air Max 90), taglie bambino, repliche, lotti, accessori, prezzi implausibili. Cache per modello rinnovata ogni 30 giorni solo se il modello è stato rivisto; budget giornaliero e mensile contati prima di ogni chiamata; mai una ricerca durante l'analisi. Spenta finché non imposti la chiave.
  - Impostazioni → "Price data": vendite concluse per fonte, modelli con almeno 5 vendite reali, sconto da trattativa, stato e consumo della ricerca esterna, errore con e senza dati esterni, "Refresh now".
  - Misura (BENCHMARK SINTETICO, mercato simulato, 460 vendite più recenti stimate solo con dati precedenti): errore medio €10,33 (prima: solo annunci) → €10,12 con i tuoi dati → **€8,93** con i dati di altri mercati (MAPE 17,2% → 13,9%). Con dati esterni volutamente distorti (+75%) il controllo li esclude: €10,12, sempre meglio di prima. Vendite concluse nel database 855 → 1.724; modelli con almeno 5 vendite reali 13 → 14.

- **Obiettivo 8 – analisi della pagina più veloce** (verificato su vinted.it reale: 20 caricamenti prima, 10 dopo; mercato sintetico nel database di misura; `extension/e2e/real-speed.e2e.cjs`, `docs/SPEED_MEASURE.md`).
  - Dove andava il tempo (mediane, prima): pagina Vinted primo byte 1.036 ms, pronta 1.606 ms (HTML 3,7 MB); lettura delle 96 schede 31 ms; migliore evidenziata 1.699 ms dall'apertura (77 ms dopo la pagina pronta); verdetto rapido su tutte 1.780 ms; 9 richieste a FlipFinder per pagina, 3.437 ms di rete in tutto; database 750 ms per pagina; analisi server 2.488 ms; tutti i verdetti completi 5.890 ms; foto dei 5 migliori: nessuna analizzata entro 20 s.
  - Cosa cambia: le schede si leggono mentre la pagina arriva (prima che sia pronta); verdetto istantaneo anche per modello; una sola richiesta di statistiche pre-calcolate per pagina (`POST /extension/page-stats`, una query); catture al server partendo dalle migliori (primo blocco di 12); schede già analizzate e invariate non vengono rianalizzate; comparabili condivisi per 60 s tra richieste; foto analizzate in background partendo dai migliori candidati, mai prima della risposta; `Server-Timing` (db, analisi, totale) su ogni richiesta dell'estensione.
  - Dopo (mediane): migliore evidenziata **1.133 ms** dall'apertura (275 ms *prima* che la pagina sia pronta; il primo byte di Vinted da solo è 930 ms); verdetto rapido su tutte e 96 **1.493 ms**; primo verdetto completo 1.393 ms; tutti i verdetti completi **1.930 ms** (era 5.890); rete delle catture 855 ms (era 3.437); database 306 ms per pagina (era 750); analisi server 223 ms (era 2.488); `POST /capture/cards` 157 ms (era 480); statistiche della pagina 39 ms; foto dei 5 migliori 4/5 in 3,6 s in un giro (0/5 nell'altro, worker occupato); nessun task lungo dell'estensione oltre 92 ms.

- **Pagine articolo di Vinted nel formato attuale** (verificato su pagine reali salvate e anonimizzate: un articolo in vendita e uno venduto, con e senza script; 485 test backend, 39 test estensione).
  - Vinted ha cambiato la pagina articolo: preferiti, recensioni del venditore, protezione acquisti, "riservato" e stato di vendita stanno ora in sezioni separate della pagina. Prima nessuno dei due parser li leggeva più, e un articolo venduto veniva riconosciuto solo dal testo.
  - Ora server ed estensione li leggono di nuovo, sempre e solo dalle sezioni dell'articolo stesso (mai dall'utente collegato o dagli articoli suggeriti). "Venduto" viene dalla sezione di stato dell'articolo, dai dati schema.org o dall'etichetta sopra il riepilogo, mai dal solo "non acquistabile" (vale anche per i tuoi articoli o se non sei collegato): una vendita falsa sporcherebbe le vendite concluse.

- **Controllo incrociato del codice nuovo** (un revisore indipendente per il backend, uno per estensione e web app). Corretti nel backend: tetto "prezzo da nuovo" annullato dalla calibrazione ma ancora dichiarato; budget delle ricerche esterne superabile lanciando a mano una ricerca durante quella oraria; prezzo pagato usato come comparabile dello stesso articolo; articolo comprato contato due volte fino alla notte e poi sparito; due sincronizzazioni complete al giorno; cache dei comparabili che poteva tenere per 60 s una copia senza la vendita appena vista; scarti mancanti ("Ragazzo/a", "Air Max 90/1", "tre paia"). Corretti in estensione e web app: dopo un aggiornamento dell'estensione, su una pagina di FlipFinder già aperta un solo clic su Acquista poteva avviare anche la vecchia procedura in due passi (e un secondo clic su Acquista); "Open checkout at €X" non ricontrollava il prezzo; nessun nuovo controllo di articolo/prezzo subito prima del clic; un gesto valeva più azioni per qualche secondo; schede e statistiche perse se un messaggio non arrivava; due acquisti contemporanei potevano sovrascriversi; una provenienza incompleta poteva bloccare la pagina Analisi.

- **Obiettivo 9 – Preferiti e Acquista al primo clic** (verificato: `extension/e2e/actions.e2e.cjs` con estensione vera e Vinted finto in HTTPS, test della web app).
  - Acquista: un clic apre l'articolo, verifica che sia in vendita allo stesso prezzo dell'analisi e preme "Acquista"; se il prezzo è cambiato o l'articolo è venduto/riservato non clicca nulla e la web app propone il checkout al nuovo prezzo. Il pagamento lo confermi sempre tu su Vinted.
  - Cause di fallimento al primo clic trovate e corrette: estensione "addormentata" quando la pagina di FlipFinder si apriva (il ponte chiedeva una volta sola e restava "non collegato"), ponte di una versione precedente rimasto nella pagina, pagine aperte prima dell'associazione, scheda Vinted in background rallentata, pulsante cliccato prima che Vinted lo rendesse attivo, banner dei cookie. La web app richiede lo stato al momento del clic prima di dire "non collegato".
  - Test e2e con i pulsanti veri della pagina Analisi (3 esecuzioni consecutive superate): Acquista al primo clic, checkout aperto in **0,96 s** senza finestre intermedie; nessuna scheda Vinted aperta 1,2 s; service worker fermato subito prima del clic 1,1 s (Preferiti 1,7 s); browser nuovo 1,3 s (Preferiti 1,5 s); prezzo cambiato 30 → 27 € segnalato in 0,6 s senza clic, poi "Open checkout at €27" apre il checkout solo a quel prezzo; riservato e venduto spiegati senza clic; tasto simile accanto ad Acquista ignorato; checkout abbandonato non registrato.
  - Un clic = una azione: una seconda richiesta dopo lo stesso clic viene rifiutata, anche se il browser considera ancora attivo il gesto.

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

- **Migliorie dopo la consegna** (verificate)
  - Limite tentativi di accesso a finestra scorrevole. Prima, a cavallo del minuto passavano fino a 20 tentativi invece di 10, e il test `test_login_rate_limit` falliva ogni tanto.
    - Ora: al massimo 10 in qualsiasi intervallo di 60 s.
    - `Retry-After` calcolato, non più fisso a 30 s.
    - Le richieste rifiutate non allungano l'attesa.
    - Test nuovo `tests/integration/test_rate_limit.py`. Sullo stack di produzione: 10 tentativi rifiutati con 401, l'11° e il 12° con 429 e `Retry-After: 60`.
  - Tasti su Vinted robusti a pagine diverse da quelle previste, verificati con 4 varianti nel test e2e:
    - stato del cuore illeggibile → nessun clic;
    - cuore che cambia solo l'icona → un clic, poi conferma rileggendo la pagina (stato dai dati dell'articolo, mai da quelli dei suggeriti);
    - pulsante "Protezione acquisti" prima di "Acquista" → mai cliccato;
    - "Acquista" che non porta al checkout → segnalato, niente "checkout aperto".

    Prima, nel primo e nell'ultimo caso, sarebbero stati registrati stati falsi.
  - Etichette dei pulsanti in più lingue: preferiti in entrambe le direzioni, "Acquista ora". Configurazione `2026.10.7-3`, aggiornata dall'estensione senza ripubblicarla.
  - Il service worker usa la stessa configurazione aggiornata delle pagine, non solo quella interna all'estensione.
  - Test e2e dei tasti eseguito anche sullo stack di produzione (immagini ricostruite, HTTPS con Caddy, cookie Secure, limiti reali, migrazioni fino alla 0008 all'avvio dopo il backup): tutto superato.
  - Velocità rimisurata dopo le modifiche: migliore evidenziata 18–51 ms dopo il caricamento della pagina, 108 ms con CPU 4×.
  - Verificato che il pannello non ha testo tagliato a destra: era un artefatto dello screenshot. Il controllo è ora nel test.

## Da fare da te
- Chiave Serper per la ricerca esterna (gratis le prime 2.500 ricerche, senza carta): registrati su serper.dev, copia la chiave e mettila nel file `.env` del PC (`EXTERNAL_SEARCH_PROVIDER=serper`, `SERPER_API_KEY=...`), poi `docker compose up -d`. Senza chiave tutto il resto funziona, con le tue vendite e i venduti Vinted.
- Aggiornare FlipFinder sul PC (`git pull` + `docker compose up -d --build`) e ricaricare l'estensione da `chrome://extensions` (pulsante Aggiorna).
- Numeri reali dell'errore con e senza dati esterni: compaiono da soli in Impostazioni → Price data dopo il controllo notturno, oppure subito con `docker compose exec backend python -m app.tools.price_eval`.
- (Sessione precedente) Eliminare i dati demo dal DB di sviluppo di quella sessione: sul tuo server la migrazione 0007 lo fa da sola all'avvio, dopo il backup automatico.

## In corso
- Nessuno.

## Ipotesi
- Errore delle stime: i numeri riportati sono sul benchmark sintetico (il database di questa sessione non contiene vendite reali). Sulle tue vendite il controllo automatico tiene accesa una fonte solo se non peggiora l'errore, quindi "uguale o meglio di prima" vale per costruzione appena ci sono almeno 30 vendite di prova; prima è "non ancora misurabile" e le fonti nuove pesano poco.
- Formato delle risposte di Serper: costruito dalla documentazione e dai client pubblici, non da una risposta reale (nessuna chiave in questa sessione). Alla prima esecuzione conviene `python -m app.tools.external_prices --brand nike --model "Air Max 90" --dry-run` per vedere cosa viene tenuto e scartato.
- Vendite concluse su altri mercati: Google mostra poche vendite concluse (soprattutto eBay "Venduto"); la maggior parte dei riferimenti esterni saranno prezzi da nuovo e richiesti.
- Velocità "migliore entro 1 s": non raggiungibile sulle pagine reali misurate da qui, perché Vinted impiega da solo ~0,93 s a mandare il primo byte; l'estensione evidenzia la migliore ~0,2 s dopo, prima che la pagina sia pronta. Da casa tua il primo byte può essere più veloce o più lento.
- Le misure di velocità usano pagine Vinted reali ma un mercato sintetico nel database (serve solo a dare comparabili all'analisi).
- FlipFinder è pensato per un solo account (in produzione le registrazioni si chiudono dopo il primo): le "tue" vendite e lo sconto da trattativa usano gli acquisti e le vendite registrati nell'installazione.
- Tassi di cambio fissi (BCE del 7/10/2026) in `app/external/fx.py`: da aggiornare a mano se cambiano molto.
- Le misure di errore sono sul mercato simulato (nel DB c'è 1 sola vendita reale): sulle tue vendite reali la calibrazione si attiva da sola dopo 30 vendite osservate; le tue rivendite pesano 5 volte.
- Il set di prova autenticità è fatto di scenari (esiti dell'analisi foto + prezzo + venditore), non di foto reali: misura le regole di decisione; la qualità della lettura delle foto dipende dal modello AI configurato.
- Soglie foto: nitidezza 0,30 tarata su immagini sintetiche (nitide ≥ 0,35, sfocate ≤ 0,28); hash ≤ 4 bit = stessa foto.
- Verdetto rapido: categoria riconosciuta dal titolo con le parole chiave della tassonomia; se la categoria non ha abbastanza vendite si usa il brand intero (confidenza ridotta); senza dati "dati insuff.".
- Produzione verificata con dominio `localhost` (certificato locale di Caddy): con un dominio vero Caddy usa Let's Encrypt, non verificabile da qui.
- Tempo di risposta del venditore: Vinted non lo mostra nella pagina articolo, quindi è "non disponibile" (non stimato).
- Prezzo di listino e periodo di uscita: nessuna fonte pubblica affidabile; mostrato solo il prezzo originale dichiarato dal venditore nel testo.
- La pagina "salvata" del test è costruita sulla struttura nota di Vinted (non ho una tua pagina reale): se ne salvi una (Ctrl+S) in `backend/tests/fixtures/vinted/real/`, va aggiunta ai test.
- Foto di articoli suggeriti presenti una sola volta in analisi salvate con la versione vecchia non sono riconoscibili dall'URL: vengono sostituite alla prossima apertura dell'annuncio (cattura dalla galleria = autorevole).
- Preferiti/Acquista: i selettori del cuore, del tasto "Acquista" e della pagina di pagamento completato sono costruiti sulla struttura nota di Vinted, non verificati su Vinted reale (nessun accesso da qui). Se cambiano si correggono in `vinted_parser.json`, senza ripubblicare l'estensione. In caso di dubbio l'estensione si ferma e lo dice, non clicca a caso. Lo stato del cuore dai dati della pagina usa il campo `is_favourite` dell'articolo (nome noto dell'API di Vinted, da confermare su una pagina reale).
- Il test e2e dei tasti gira su un database separato e vuoto (`flipfinder_e2e`), perché il DB di sviluppo non può passare alla migrazione 0008 senza la 0007 bloccata. Il backend di sviluppo sulla porta 8000 è ancora la versione precedente.
- Permesso `scripting` aggiunto all'estensione, e aggiornato il test che fissa l'elenco dei permessi: è l'unico modo per agganciare la pagina di FlipFinder a un indirizzo scelto da te, e non dà accesso a nuovi siti.
- Acquisto registrato solo dalla pagina di conferma nella stessa scheda del checkout avviato da FlipFinder. Se paghi altrove usa "I bought it". Se il totale non è leggibile si registra il prezzo dell'articolo.
