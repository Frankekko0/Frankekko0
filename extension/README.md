# FlipFinder for Vinted (estensione browser) · v1.0

Mentre navighi su Vinted l'estensione valuta gli annunci che vedi:
- **badge** sulle schede con score, margine netto e "già tracciato";
- un **riquadro** sull'annuncio aperto;
- un **pannello laterale live** con la classifica dei migliori articoli della ricerca che stai scorrendo.

Tutto viene salvato in FlipFinder, con la data, la modalità di acquisizione e la versione dell'algoritmo.

## Cosa fa (e cosa non fa)

- **Legge solo le pagine di Vinted che apri e scorri tu.**
  - Una scheda viene valutata quando almeno metà di essa entra a schermo.
  - L'annuncio aperto viene letto tutto: campi, foto e stato.
- **Non naviga, non scorre e non apre annunci da sola.**
  - L'**analisi approfondita** di una scheda parte su tuo clic e legge solo quella pagina, senza cookie.
  - Le letture automatiche dei migliori candidati sono facoltative e spente di default:
    - al massimo una ogni 30 secondi e 20 all'ora;
    - solo mentre hai una scheda di Vinted aperta;
    - al primo rifiuto di Vinted si fermano per 6 ore, senza tentativi di aggiramento.
- **Nessuna azione sul tuo account Vinted:** niente acquisti, offerte, preferiti o messaggi. Le azioni rapide ("Traccia", "Analisi approfondita", "Apri nella pagina di tracking") partono solo su tuo clic e agiscono su FlipFinder.
- **Nessun cookie, token o credenziale di Vinted** viene letto o inviato.
  - L'estensione si autentica a FlipFinder con una **chiave dell'estensione** (`ff_ext_…`), creata e revocabile da FlipFinder.
  - Del venditore arrivano a FlipFinder solo valutazione, numero di recensioni e un'impronta non reversibile dell'ID: mai il nome utente.
- **Permessi minimi:** `storage`, `alarms` e `sidePanel`.
  - Gira solo sui domini `www.vinted.*`.
  - L'accesso all'indirizzo di FlipFinder viene chiesto al momento dell'associazione, non all'installazione.

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
  - **Vista dettaglio** dell'annuncio aperto: tutte le foto, comparabili, segnali di rischio, "Traccia".
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
| `manifest.json` | Manifest V3: content script solo su `www.vinted.*`; permessi `storage`, `alarms`, `sidePanel`; FlipFinder in `optional_host_permissions`. |
| `src/parser-config.js` | Copia di `backend/app/acquisition/vinted_parser.json`, la configurazione del parser condivisa con il server. L'estensione scarica le versioni nuove da FlipFinder (`/extension/parser-config`), quindi una correzione si fa in un punto solo, senza ripubblicare l'estensione. |
| `src/parse.js` | Parser puro guidato dalla configurazione: annuncio (JSON-LD, script della pagina, meta, etichette in 15 lingue), schede, prezzi e valute, stato, record da inviare entro i limiti del server. |
| `src/core.js` | Logica pura: opzioni, coda (un record per ID Vinted, nuovi tentativi con attesa crescente), ritmo delle letture, classifica live, export CSV. |
| `src/content.js` | Sulla pagina: `MutationObserver` con debounce per le schede nuove, `IntersectionObserver` per valutarle quando entrano a schermo, `requestIdleCallback` per il parsing, badge (Shadow DOM), riquadro dell'annuncio, lettura su comando. |
| `src/background.js` | Service worker: coda persistente e sincronizzazione, client di FlipFinder, ritmo delle letture, aggiornamento della configurazione, sessioni per scheda del pannello live. |
| `src/panel.*`, `src/options.*`, `src/popup.*`, `src/ui.css` | Pannello live, opzioni, popup. |

Quando l'annuncio è aperto dopo una navigazione interna di Vinted, gli script della pagina descrivono ancora il primo annuncio. In quel caso vengono ignorati e si usa solo ciò che è visibile.

## Test

```bash
node --test extension/tests/*.test.mjs        # parser (con le stesse fixture del server), coda, ritmo, classifica
node extension/tools/sync-parser-config.mjs   # dopo aver cambiato backend/app/acquisition/vinted_parser.json
```

Test end-to-end con l'estensione vera in Chromium, su pagine Vinted finte (nessuna richiesta reale a Vinted). Serve FlipFinder in esecuzione con l'account demo:

```bash
APP_URL=http://localhost:3000 CHROME_PATH=/percorso/chrome node extension/e2e/live.e2e.cjs /tmp/screenshots
```
