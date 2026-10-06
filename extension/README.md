# FlipFinder for Vinted (estensione browser)

Due modi di usarla, con lo stesso pulsante in basso a destra:

- **Su un annuncio**: *Analizza con FlipFinder* apre l'analisi completa di quell'annuncio
  (valore di mercato, scenari di rivendita, profitto netto con i tuoi costi, ROI, Flip Score,
  rischio e prezzo massimo da pagare).
- **Su una ricerca, un catalogo o un armadio**: *Analizza N articoli* manda a FlipFinder tutti gli
  annunci caricati nella pagina (fino a 200 per volta). FlipFinder li analizza insieme e te li
  mostra in classifica, dal migliore: filtri "Worth buying" / "Profitable" e ordinamenti per
  profitto, ROI, prezzo o sconto sul mercato. Vuoi più articoli? Scorri i risultati o vai alla
  pagina successiva e premi di nuovo; reimportare la stessa ricerca più tardi mostra i ribassi.

## Come funziona (e cosa non fa)

- Legge **solo la pagina che hai aperto**, quando premi il pulsante: sull'annuncio i dati
  strutturati (JSON-LD, meta tag, voci "Brand / Taglia / Condizioni / Colore"); su una ricerca il
  riepilogo che Vinted mette su ogni scheda ("titolo, brand: …, condizioni: …, taglia: …,
  prezzo"), con il sottotitolo e il prezzo della scheda come riserva.
- Non fa richieste a Vinted, non naviga e non scorre da sola, non compra, non invia offerte o
  messaggi.
- I dati arrivano a FlipFinder nel frammento dell'URL (`/analyze#import=…` per un annuncio,
  `/import#batch=…` compresso per una ricerca), che il browser non invia a nessun server.
  FlipFinder li ricontrolla come input non fidato: le schede senza titolo o prezzo leggibile e
  quelle in valuta diversa dall'euro vengono scartate e conteggiate, mai indovinate. Sul singolo
  annuncio puoi correggere i campi prima di salvarlo nel tuo feed.

## Installazione (Chrome, Edge, Brave)

1. Apri `chrome://extensions` (in Edge: `edge://extensions`) e attiva **Modalità sviluppatore**.
2. Premi **Carica estensione non pacchettizzata** e scegli questa cartella `extension/`.
3. Se FlipFinder non gira su `http://localhost:3000`, apri il popup dell'estensione →
   *Indirizzo di FlipFinder* e salva l'URL corretto.
4. Apri un annuncio o una ricerca su Vinted: il pulsante compare in basso a destra (oppure usa il
   popup dall'icona nella barra degli strumenti).

Se hai già installato la versione precedente, premi **Aggiorna** (o l'icona di ricarica
sull'estensione) in `chrome://extensions` e ricarica le schede di Vinted aperte.

Se non sei ancora entrato in FlipFinder, ti verrà chiesto di accedere e poi tornerai
direttamente all'analisi.

## Struttura

| File | Ruolo |
|---|---|
| `manifest.json` | Manifest V3; content script solo sui domini `www.vinted.*`, permesso `storage` |
| `src/parse.js` | Parsing puro (nessun DOM, nessuna rete): annuncio (JSON-LD, meta, etichette multilingua) e schede di ricerca (riepilogo, prezzi e valute, condizioni), codifica compressa del lotto |
| `src/content.js` | Raccoglie i dati della pagina aperta e mostra il pulsante (Shadow DOM, segue la navigazione interna di Vinted e conta gli articoli caricati) |
| `src/background.js` | Apre la scheda di FlipFinder (solo URL `http(s)://…/analyze` o `…/import`) |
| `src/popup.html`, `src/popup.js` | Pulsante "Analizza la pagina aperta" e impostazione dell'indirizzo dell'app |

## Test

```bash
node --test "extension/tests/*.test.mjs"
```

Se Vinted cambia l'impaginazione e qualche campo non viene letto, FlipFinder lo segnala: sul
singolo annuncio ti lascia completarlo a mano, su una ricerca indica quante schede ha saltato.
