# FlipFinder for Vinted (estensione browser)

Mentre navighi su Vinted, un clic su **Analizza con FlipFinder** apre in FlipFinder l'analisi
completa dell'annuncio che stai guardando: valore di mercato, scenari di rivendita, profitto netto
con i tuoi costi, ROI, Flip Score, rischio e prezzo massimo da pagare.

## Come funziona (e cosa non fa)

- Legge **solo la pagina che hai aperto** (dati strutturati JSON-LD, meta tag e le voci
  "Brand / Taglia / Condizioni / Colore"), quando premi il pulsante.
- Non fa richieste a Vinted, non naviga da sola, non compra, non invia offerte o messaggi.
- I dati arrivano a FlipFinder nel frammento dell'URL (`/analyze#import=…`), che il browser non
  invia a nessun server; FlipFinder li controlla, compila il modulo e avvia un *Quick check*.
  Puoi correggere i campi prima di salvare l'annuncio nel tuo feed.

## Installazione (Chrome, Edge, Brave)

1. Apri `chrome://extensions` (in Edge: `edge://extensions`) e attiva **Modalità sviluppatore**.
2. Premi **Carica estensione non pacchettizzata** e scegli questa cartella `extension/`.
3. Se FlipFinder non gira su `http://localhost:3000`, apri il popup dell'estensione →
   *Indirizzo di FlipFinder* e salva l'URL corretto.
4. Apri un annuncio su Vinted: il pulsante compare in basso a destra (oppure usa il popup
   dall'icona nella barra degli strumenti).

Se non sei ancora entrato in FlipFinder, ti verrà chiesto di accedere e poi tornerai
direttamente all'analisi.

## Struttura

| File | Ruolo |
|---|---|
| `manifest.json` | Manifest V3; content script solo sui domini `www.vinted.*`, permesso `storage` |
| `src/parse.js` | Parsing puro (nessun DOM, nessuna rete): JSON-LD, meta, etichette multilingua, prezzi, condizioni |
| `src/content.js` | Raccoglie i dati della pagina aperta e mostra il pulsante (Shadow DOM, segue la navigazione interna di Vinted) |
| `src/background.js` | Apre la scheda di FlipFinder (solo URL `http(s)://…/analyze`) |
| `src/popup.html`, `src/popup.js` | Pulsante "Analizza l'annuncio aperto" e impostazione dell'indirizzo dell'app |

## Test

```bash
node --test "extension/tests/*.test.mjs"
```

Se Vinted cambia l'impaginazione e qualche campo non viene letto, FlipFinder lo segnala e ti
lascia completarlo a mano: l'analisi funziona comunque.
