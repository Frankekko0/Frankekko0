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

## In corso
- Obiettivo 1: analisi prodotto.

## Ipotesi
- La pagina "salvata" del test è costruita sulla struttura nota di Vinted (non ho una tua pagina reale): se ne salvi una (Ctrl+S) in `backend/tests/fixtures/vinted/real/`, va aggiunta ai test.
- Foto di articoli suggeriti presenti una sola volta in analisi salvate con la versione vecchia non sono riconoscibili dall'URL: vengono sostituite alla prossima apertura dell'annuncio (cattura dalla galleria = autorevole).
