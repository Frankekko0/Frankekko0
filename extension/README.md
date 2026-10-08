# FlipFinder for Vinted (estensione browser) · v1.1

Mentre navighi su Vinted l'estensione valuta gli annunci che vedi:
- **badge** sulle schede con score, margine netto e "già tracciato";
- un **riquadro** sull'annuncio aperto;
- un **pannello laterale live** con la classifica dei migliori articoli della ricerca che stai scorrendo.

Tutto viene salvato in FlipFinder, con la data, la modalità di acquisizione e la versione dell'algoritmo.

## Cosa fa (e cosa non fa)

- **Legge solo le pagine di Vinted che apri e scorri tu.**
  - Una scheda viene valutata quando almeno metà di essa entra a schermo.
  - L'annuncio aperto viene letto tutto: campi, foto e stato.
- **Di base non naviga, non scorre e non apre annunci da sola.** Le letture automatiche sono tutte facoltative e spente di default (analisi dei candidati, aggiornamento dei tracciati, **scanner delle ricerche**, vedi sotto).
  - L'**analisi approfondita** di una scheda parte su tuo clic e legge solo quella pagina, senza cookie.
  - Le letture automatiche dei migliori candidati sono facoltative e spente di default:
    - al massimo una ogni 30 secondi e 20 all'ora;
    - solo mentre hai una scheda di Vinted aperta;
    - al primo rifiuto di Vinted si fermano per 6 ore, senza tentativi di aggiramento.
- **Sul tuo account Vinted solo ciò che chiedi tu, un clic per un'azione.**
  - **Preferiti** (pagina Analisi di FlipFinder): un clic apre l'annuncio in una scheda in background, preme una volta il cuore di Vinted, attende la conferma e chiude la scheda. Ogni volta che apri un annuncio, lo stato del cuore visto sulla pagina riallinea FlipFinder.
    - Se non riesce a leggere se l'annuncio è già nei preferiti (né dal pulsante né dai dati della pagina), non clicca: un clic alla cieca potrebbe toglierlo.
    - Se dopo il clic il pulsante non mostra il nuovo stato, ricarica la pagina e legge lo stato vero. Non clicca mai una seconda volta.
  - **Acquista** (pagina Analisi, pagina di tracking, pannello): un clic apre l'annuncio e verifica disponibilità e prezzo. Se è venduto, riservato o il prezzo è cambiato lo vedi prima. Solo un secondo clic preme "Acquista" di Vinted e apre il suo checkout: il pagamento lo confermi tu su Vinted. FlipFinder registra "checkout aperto" e, sulla pagina di conferma della stessa scheda, "acquistato" con il totale pagato.
    - Preme solo il vero "Acquista" (il suo identificativo, o un pulsante il cui testo è esattamente "Acquista"), mai pulsanti simili come "Protezione acquisti" o "Fai un'offerta".
    - "Checkout aperto" viene registrato solo quando la pagina del checkout compare davvero; altrimenti te lo dice.
  - Le richieste dalla pagina di FlipFinder sono accettate solo subito dopo un tuo clic reale, e solo per queste azioni. Niente offerte, messaggi o acquisti automatici.
  - Le azioni rapide ("Traccia", "Analisi approfondita", "Apri nella pagina di tracking") agiscono solo su FlipFinder.
- **Nessun cookie, token o credenziale di Vinted** viene letto o inviato.
  - L'estensione si autentica a FlipFinder con una **chiave dell'estensione** (`ff_ext_…`), creata e revocabile da FlipFinder.
  - Del venditore arrivano a FlipFinder solo valutazione, numero di recensioni e un'impronta non reversibile dell'ID: mai il nome utente.
- **Permessi minimi:** `storage`, `alarms`, `sidePanel`, `scripting` (solo per collegare i tasti Preferiti/Acquista alla pagina di FlipFinder, all'indirizzo che autorizzi tu), `offscreen` (una pagina nascosta che legge l'HTML delle ricerche dello scanner: il service worker non ha un DOM) e `notifications` (gli avvisi dello scanner).
  - Gira solo sui domini `www.vinted.*`.
  - L'accesso all'indirizzo di FlipFinder viene chiesto al momento dell'associazione, non all'installazione.
  - L'accesso a un sito di Vinted per lo scanner viene chiesto con un clic, quando aggiungi una ricerca.

## Scanner automatico delle ricerche (facoltativo, spento di default)

Rilegge da solo le ricerche che salvi, finché **il browser è aperto** (non serve una scheda di Vinted aperta), e salva in FlipFinder gli annunci nuovi. Quelli nuovi che superano le tue soglie (score e margine minimo) ti arrivano come notifica del browser; un clic sulla notifica apre l'annuncio su Vinted. Acquisto, offerte e messaggi restano una tua decisione.

**Come si usa**
1. Apri una ricerca su Vinted, imposta filtri e prezzo massimo.
2. Dal popup dell'estensione: **Aggiungi questa ricerca allo scanner**. Il browser chiede il permesso per quel sito di Vinted. In alternativa incolla l'indirizzo nelle opzioni.
3. Nelle opzioni: **Scanner automatico → Attiva lo scanner**, e scegli ogni quanti minuti (10–240, 15 di default).

**Come funziona**
- Legge solo la **prima pagina** di ogni ricerca, dal più recente (aggiunge `order=newest_first` se manca; toglie `page`, `time` e simili). Al massimo 10 ricerche.
- La **prima lettura** di una ricerca costruisce la base: tutto quello che c'è è già "visto" e non genera notifiche. Dalla seconda in poi, gli annunci non ancora visti sono "nuovi". Tutto viene comunque salvato nell'archivio, con modalità `extension_scan`.
- La pagina viene scaricata **senza cookie** (l'account Vinted non c'entra) e letta in una pagina nascosta (`offscreen`) senza eseguire script né caricare immagini, con lo stesso lettore di schede e la stessa configurazione del parser delle pagine che apri tu.

**Ritmo prudente (fisso nel codice)**
- Una sola lettura per volta, **almeno 1 minuto** tra due letture, **30 all'ora** e **300 al giorno**. L'intervallo scelto viene allungato da solo se con più ricerche supererebbe questi tetti (per esempio 4 ricerche: ogni 20 minuti).
- Al **primo rifiuto o CAPTCHA** (403, 429, pagina anti-bot) tutte le letture automatiche (scanner, analisi dei candidati, aggiornamento dei tracciati) si fermano per **6 ore**, senza ritentare e senza aggirare nulla; ti arriva una notifica.
- Una ricerca che non mostra schede leggibili per 3 volte di fila viene sospesa e va riattivata dalle opzioni (può voler dire che la configurazione del parser va aggiornata).
- Se il permesso per il sito è stato tolto, non legge nulla.
- Le condizioni di Vinted vietano la raccolta automatica: attivarlo è una tua scelta.

## Installazione e associazione (Chrome, Edge, Brave)

1. Apri `chrome://extensions` (in Edge `edge://extensions`), attiva **Modalità sviluppatore**, premi **Carica estensione non pacchettizzata** e scegli questa cartella `extension/`.
2. In FlipFinder: **Impostazioni → Browser extension → New key**, poi copia la chiave (viene mostrata una volta sola).
3. Nelle opzioni dell'estensione (si aprono da sole alla prima installazione):
   1. inserisci l'indirizzo di FlipFinder, per esempio `http://localhost:3000`;
   2. incolla la chiave e premi **Associa**;
   3. il browser chiede il permesso per quell'indirizzo: concedilo.
4. Apri una ricerca su Vinted e scorri. Il pannello live si apre dal popup ("Apri il pannello live") o con **Alt+Shift+F**.

Hai aggiornato l'estensione? Premi **Aggiorna** in `chrome://extensions` e ricarica le schede di Vinted.

Senza associazione resta il pulsante del popup **"Apri questa pagina in FlipFinder"**: manda i dati dell'annuncio o della ricerca nel frammento dell'URL, che il browser non invia a nessun server.

## Cosa vedi

- **Badge sulle schede.** Il badge mostra:
  - score e margine netto, in verde quando l'articolo supera le tue soglie;
  - "dati insufficienti" quando i comparabili sono troppo pochi per una stima: nessun numero inventato;
  - venduto, riservato o rimosso, quando lo stato è noto;
  - un occhio sugli articoli che tracci.

  Al clic il badge mostra costo totale, rivendita, margine, confidenza e il motivo, oltre alle tre azioni rapide.
- **Riquadro sull'annuncio:** analisi completa con costo totale, rivendita stimata, margine netto, ROI e confidenza, più "Traccia", la pagina di tracking e l'analisi completa.
- **Pannello live** (pannello laterale di Chrome):
  - **Classifica:** il migliore più i quattro successivi, con foto, prezzo, costo totale, rivendita, margine netto, score, confidenza e motivo in una riga. Il migliore è evidenziato anche nella pagina; un clic su una voce porta alla scheda.
  - **Filtri:** budget, margine minimo, brand, taglie, esclusione del rischio contraffazione.
  - **Avvisi** visivi e sonori, disattivabili.
  - **Contatori:** visti, salvati, miglior margine.
  - **Classifica per ricerca:** si azzera quando cambi ricerca; puoi **bloccarla** ed **esportarla in CSV**.
  - **Vista dettaglio** dell'annuncio aperto: tutte le foto, comparabili, segnali di rischio, "Traccia", "Acquista su Vinted" (verifica, poi "Apri il checkout").
  - **Interruttore Live** per sospendere la valutazione.

  È un pannello laterale e non un riquadro flottante: non copre la griglia di Vinted, resta aperto mentre navighi e Chrome ne ricorda il lato e la larghezza.
- **Popup:** stato della sincronizzazione e coda.

  L'icona dell'estensione mostra:
  - il numero di catture in attesa;
  - `!` se FlipFinder non è raggiungibile (le catture restano in coda e partono appena torna);
  - `off` se l'estensione non è associata.

## Come funziona

| File | Ruolo |
|---|---|
| `manifest.json` | Manifest V3: content script solo su `www.vinted.*`; permessi `storage`, `alarms`, `sidePanel`, `scripting`, `offscreen`, `notifications`; FlipFinder e i siti di Vinted dello scanner in `optional_host_permissions`. |
| `src/app-bridge.js` | Solo sull'indirizzo di FlipFinder autorizzato: inoltra al service worker Preferiti/Acquista, e solo subito dopo un clic reale. Non passa cookie, token o password. |
| `src/parser-config.js` | Copia di `backend/app/acquisition/vinted_parser.json`, la configurazione del parser condivisa con il server. L'estensione scarica le versioni nuove da FlipFinder (`/extension/parser-config`), quindi una correzione si fa in un punto solo, senza ripubblicare l'estensione. |
| `src/parse.js` | Parser puro guidato dalla configurazione: annuncio (JSON-LD, script della pagina, meta, etichette in 15 lingue), schede, prezzi e valute, stato, record da inviare entro i limiti del server. |
| `src/core.js` | Logica pura: opzioni, coda (un record per ID Vinted, nuovi tentativi con attesa crescente), ritmo delle letture, classifica live, export CSV, e per lo scanner: validazione degli indirizzi, ritmo (1 minuto, 30/h, 300/giorno), scelta della ricerca da leggere, riconoscimento dei nuovi annunci. |
| `src/cards.js` | Lettura delle schede da un documento (condivisa da `content.js` e dallo scanner). |
| `src/scan.html`, `src/scan.js` | Pagina nascosta (`offscreen`) dello scanner: scarica una ricerca senza cookie, la legge senza eseguirla, riconosce rifiuti e pagine anti-bot. |
| `src/content.js` | Sulla pagina: `MutationObserver` con debounce per le schede nuove, `IntersectionObserver` per valutarle quando entrano a schermo, `requestIdleCallback` per il parsing, badge (Shadow DOM), riquadro dell'annuncio, lettura su comando. |
| `src/background.js` | Service worker: coda persistente e sincronizzazione, client di FlipFinder, ritmo delle letture, aggiornamento della configurazione, sessioni per scheda del pannello live, pianificazione e notifiche dello scanner. |
| `src/panel.*`, `src/options.*`, `src/popup.*`, `src/ui.css` | Pannello live, opzioni, popup. |

Quando l'annuncio è aperto dopo una navigazione interna di Vinted, gli script della pagina descrivono ancora il primo annuncio. In quel caso vengono ignorati e si usa solo ciò che è visibile.

## Test

```bash
node --test extension/tests/*.test.mjs        # parser (con le stesse fixture del server), coda, ritmo, classifica
node extension/tools/sync-parser-config.mjs   # dopo aver cambiato backend/app/acquisition/vinted_parser.json
```

Test end-to-end con l'estensione vera in Chromium, su pagine Vinted finte (nessuna richiesta reale a Vinted). Serve un FlipFinder di prova in esecuzione che accetti nuove registrazioni (ogni test crea un proprio account):

```bash
APP_URL=http://localhost:3000 CHROME_PATH=/percorso/chrome node extension/e2e/live.e2e.cjs /tmp/screenshots
# velocità del verdetto rapido su una ricerca da 96 articoli (tempo dalla lettura della pagina, CPU 4x, offline, scorrimento)
APP_URL=http://localhost:3000 CHROME_PATH=/percorso/chrome node extension/e2e/speed.e2e.cjs
# Preferiti e Acquista dalla pagina Analisi, dal tracking e dal pannello (Vinted finto in HTTPS locale; serve openssl)
APP_URL=http://localhost:3000 CHROME_PATH=/percorso/chrome node extension/e2e/actions.e2e.cjs /tmp/screenshots
# scanner: spento di default, prima lettura come base, annuncio nuovo e conveniente = notifica, ritmo, pausa di 6 ore al primo 403
# (Vinted finto in HTTPS locale; basta l'API: APP_URL può essere http://localhost:8000)
APP_URL=http://localhost:8000 CHROME_PATH=/percorso/chrome node extension/e2e/scan.e2e.cjs
```

### Verdetto rapido

All'apertura di una ricerca l'estensione valuta subito tutte le schede in locale dal riepilogo di mercato scaricato da FlipFinder (prezzi dei venduti per brand e categoria, quota venduta in 30 giorni, i tuoi costi, regole antifalso; aggiornato ogni 3 ore, usato anche offline) e evidenzia la migliore: profitto corretto per il rischio = margine × probabilità di vendita × probabilità di autenticità. L'analisi completa del server arriva subito dopo e sostituisce le stime (badge "≈" = stima rapida).

### In produzione

Nelle opzioni imposta l'indirizzo HTTPS della tua installazione (es. `https://flip.tuodominio.it`) e incolla la chiave creata in FlipFinder → Impostazioni → Browser extension: il browser chiede il permesso per quell'indirizzo una sola volta.
