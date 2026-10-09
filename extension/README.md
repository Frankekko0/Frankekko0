# FlipFinder for Vinted (estensione browser) · v1.2

Mentre navighi su Vinted l'estensione valuta gli annunci che vedi:
- **badge** sulle schede con score, margine netto e "già tracciato";
- un **riquadro** sull'annuncio aperto;
- un **pannello laterale live** con la classifica dei migliori articoli della ricerca che stai scorrendo.

Tutto viene salvato in FlipFinder, con la data, la modalità di acquisizione e la versione dell'algoritmo.

## Cosa fa (e cosa non fa)

- **Legge solo le pagine di Vinted che apri e scorri tu.**
  - Una scheda viene valutata quando almeno metà di essa entra a schermo.
  - L'annuncio aperto viene letto tutto: campi, foto e stato.
- **Non fa nient'altro da sola.** Non naviga, non scorre, non apre annunci, non rilegge ricerche salvate e non aggiorna in background gli articoli tracciati. Non esistono scanner, letture automatiche né analisi "approfondite" che scarichino pagine che non stai guardando.
  - Le condizioni di Vinted (in vigore dall'8/10/2026) vietano gli strumenti software esterni per la raccolta automatica e per azioni come i preferiti. Per questo, per scelta dell'utente (decisione Q1 del piano), queste funzioni sono state **rimosse**, non solo spente.
  - Un articolo tracciato si aggiorna quando lo riapri tu su Vinted, oppure con le e-mail di notifica di Vinted se le hai collegate.
- **Nessuna azione sul tuo account Vinted.** Niente clic su "Preferiti" o "Acquista", niente offerte né messaggi: l'estensione mette a disposizione solo **"Apri su Vinted"** (un normale link che si apre in una nuova scheda) e tutto ciò che fai lì è tuo.
- **Nessun cookie, token o credenziale di Vinted** viene letto o inviato.
  - L'estensione si autentica a FlipFinder con una **chiave dell'estensione** (`ff_ext_…`), creata e revocabile da FlipFinder.
  - Del venditore arrivano a FlipFinder solo valutazione, numero di recensioni e un'impronta non reversibile dell'ID: mai il nome utente.
- **Permessi minimi:** `storage`, `alarms` (invio e aggiornamento della configurazione) e `sidePanel`. Nessun `scripting`, `offscreen`, `notifications`, `tabs`, `cookies`.
  - Gira solo sui domini `www.vinted.*`.
  - L'unica richiesta di rete è verso l'indirizzo di FlipFinder; il suo accesso viene chiesto al momento dell'associazione, non all'installazione.
  - Un test (`tests/no-automation.test.mjs`) legge i sorgenti e fallisce se tornano permessi, messaggi o chiamate di rete di queste funzioni.

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

  Al clic il badge mostra costo totale, rivendita, margine, confidenza e il motivo, oltre alle azioni rapide: "Traccia", "Apri su Vinted", "Apri nella pagina di tracking".
- **Riquadro sull'annuncio:** analisi completa con costo totale, rivendita stimata, margine netto, ROI e confidenza, più "Traccia", la pagina di tracking e l'analisi completa.
- **Pannello live** (pannello laterale di Chrome):
  - **Classifica:** il migliore più i quattro successivi, con foto, prezzo, costo totale, rivendita, margine netto, score, confidenza e motivo in una riga. Il migliore è evidenziato anche nella pagina; un clic su una voce porta alla scheda.
  - **Filtri:** budget, margine minimo, brand, taglie, esclusione del rischio contraffazione.
  - **Avvisi** visivi e sonori, disattivabili.
  - **Contatori:** visti, salvati, miglior margine.
  - **Classifica per ricerca:** si azzera quando cambi ricerca; puoi **bloccarla** ed **esportarla in CSV**.
  - **Vista dettaglio** dell'annuncio aperto: tutte le foto, comparabili, segnali di rischio, "Traccia", "Apri su Vinted".
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
| `manifest.json` | Manifest V3: content script solo su `www.vinted.*`; permessi `storage`, `alarms`, `sidePanel`; l'indirizzo di FlipFinder in `optional_host_permissions`. |
| `src/parser-config.js` | Copia di `backend/app/acquisition/vinted_parser.json`, la configurazione del parser condivisa con il server. L'estensione scarica le versioni nuove da FlipFinder (`/extension/parser-config`), quindi una correzione si fa in un punto solo, senza ripubblicare l'estensione. |
| `src/parse.js` | Parser puro guidato dalla configurazione: annuncio (JSON-LD, script della pagina, meta, etichette in 15 lingue), schede, prezzi e valute, stato, record da inviare entro i limiti del server. |
| `src/core.js` | Logica pura: opzioni, coda (un record per ID Vinted, nuovi tentativi con attesa crescente), classifica live, export CSV. |
| `src/cards.js` | Lettura delle schede dalla pagina che hai aperto. |
| `src/content.js` | Sulla pagina: `MutationObserver` con debounce per le schede nuove, `IntersectionObserver` per valutarle quando entrano a schermo, `requestIdleCallback` per il parsing, badge (Shadow DOM), riquadro dell'annuncio. |
| `src/background.js` | Service worker: coda persistente e sincronizzazione, client di FlipFinder, aggiornamento della configurazione, sessioni per scheda del pannello live. |
| `src/panel.*`, `src/options.*`, `src/popup.*`, `src/ui.css` | Pannello live, opzioni, popup. |

Quando l'annuncio è aperto dopo una navigazione interna di Vinted, gli script della pagina descrivono ancora il primo annuncio. In quel caso vengono ignorati e si usa solo ciò che è visibile.

## Test

```bash
node --test extension/tests/*.test.mjs        # parser (con le stesse fixture del server), coda, classifica, assenza di automazione
node extension/tools/sync-parser-config.mjs   # dopo aver cambiato backend/app/acquisition/vinted_parser.json
```

Test end-to-end con l'estensione vera in Chromium, su pagine Vinted finte (nessuna richiesta reale a Vinted). Serve un FlipFinder di prova in esecuzione che accetti nuove registrazioni (ogni test crea un proprio account):

```bash
APP_URL=http://localhost:3000 CHROME_PATH=/percorso/chrome node extension/e2e/live.e2e.cjs /tmp/screenshots
# velocità del verdetto rapido su una ricerca da 96 articoli (tempo dalla lettura della pagina, CPU 4x, offline, scorrimento)
APP_URL=http://localhost:3000 CHROME_PATH=/percorso/chrome node extension/e2e/speed.e2e.cjs
```

### Verdetto rapido

All'apertura di una ricerca l'estensione valuta subito tutte le schede in locale dal riepilogo di mercato scaricato da FlipFinder (prezzi dei venduti per brand e categoria, quota venduta in 30 giorni, i tuoi costi, regole antifalso; aggiornato ogni 3 ore, usato anche offline) e evidenzia la migliore: profitto corretto per il rischio = margine × probabilità di vendita × probabilità di autenticità. L'analisi completa del server arriva subito dopo e sostituisce le stime (badge "≈" = stima rapida).

### In produzione

Nelle opzioni imposta l'indirizzo HTTPS della tua installazione (es. `https://flip.tuodominio.it`) e incolla la chiave creata in FlipFinder → Impostazioni → Browser extension: il browser chiede il permesso per quell'indirizzo una sola volta.
