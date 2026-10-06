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

## In corso
- Obiettivo 2: autenticità da ogni foto.

## Ipotesi
- Le misure di errore sono sul mercato simulato (nel DB c'è 1 sola vendita reale): sulle tue vendite reali la calibrazione si attiva da sola dopo 30 vendite osservate; le tue rivendite pesano 5 volte.
- Tempo di risposta del venditore: Vinted non lo mostra nella pagina articolo, quindi è "non disponibile" (non stimato).
- Prezzo di listino e periodo di uscita: nessuna fonte pubblica affidabile; mostrato solo il prezzo originale dichiarato dal venditore nel testo.
- La pagina "salvata" del test è costruita sulla struttura nota di Vinted (non ho una tua pagina reale): se ne salvi una (Ctrl+S) in `backend/tests/fixtures/vinted/real/`, va aggiunta ai test.
- Foto di articoli suggeriti presenti una sola volta in analisi salvate con la versione vecchia non sono riconoscibili dall'URL: vengono sostituite alla prossima apertura dell'annuncio (cattura dalla galleria = autorevole).
