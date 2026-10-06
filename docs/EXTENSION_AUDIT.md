# Estensione "FlipFinder for Vinted": analisi della versione 0.2 e piano

## Come è fatta oggi (v0.2.0)

| Aspetto | Situazione |
|---|---|
| Manifest | MV3, `minimum_chrome_version` 120 |
| Permessi | solo `storage` (bene) |
| Dove gira | content script su 26 domini `www.vinted.*` (bene) |
| Componenti | `parse.js` (parser puro), `content.js` (raccolta + pulsante flottante), `background.js` (apre schede), popup (indirizzo dell'app + pulsante) |
| Comunicazione con il sistema | **nessuna chiamata diretta**: i dati vengono messi nel frammento dell'URL e si apre una scheda di FlipFinder (`/analyze#import=…`, `/import#batch=…`) |
| Test | 12 test del parser in Node |

## Punti deboli

1. **Nessun canale di ritorno.** L'estensione non riceve nulla dal sistema: non può mostrare
   punteggi sulle card, sapere se un articolo è già tracciato o aggiornarne lo stato.
2. **Cattura solo su clic, e ogni cattura apre una scheda.** Tutto ciò che vedi senza premere il
   pulsante va perso.
3. **Nessuna coda e nessun nuovo tentativo.** Se FlipFinder è spento o la rete cade, il dato si
   perde. Non c'è deduplica né indicatore di sincronizzazione.
4. **Dati incompleti.**
   - Dall'annuncio mancano preferiti, visualizzazioni, materiale, stato (riservato/venduto),
     valutazione e numero di recensioni del venditore, data di caricamento, categoria e costi di
     protezione e spedizione.
   - Dalle card prende una sola immagine.
   - Armadio e preferiti non sono riconosciuti come tali.
5. **Invia lo username del venditore**, un dato personale non necessario.
6. **Parser rigido.** Etichette, regex e selettori sono scritti nel codice: se Vinted cambia la
   pagina serve una nuova versione dell'estensione. Il backend non condivide nulla con il parser.
7. **Prestazioni.**
   - Un `setInterval` ogni 800 ms gira su ogni pagina Vinted e rifà `querySelectorAll` su tutti
     i link.
   - La ricerca della card risale il DOM con `querySelectorAll` a ogni livello.
   - Non ci sono `MutationObserver`/`IntersectionObserver`: si lavora anche su ciò che non è a
     schermo.
8. **Nessuna pagina di impostazioni.** Mancano soglie, filtri e interruttori delle funzioni;
   l'indirizzo dell'app è nel popup.
9. **Autenticazione implicita.** Funziona solo perché la scheda aperta usa il login di FlipFinder
   nel browser: non si può inviare nulla in background.
10. **Doppioni di interfaccia.** Pulsante flottante e popup fanno la stessa cosa.

## Piano (v1.0)

- **Hub nel service worker.** Coda persistente in `chrome.storage.local`, invio a lotti, nuovo
  tentativo con backoff esponenziale e deduplica per ID Vinted. Lo stato di sincronizzazione si
  vede sull'icona e nel pannello.
- **Associazione con il sistema.** Da *Impostazioni → Estensione* di FlipFinder si genera una
  **chiave di estensione** revocabile, valida solo per gli endpoint di cattura. È l'unica
  credenziale in gioco ed è di FlipFinder: cookie e token di Vinted non vengono mai letti né
  inviati. Il permesso per l'indirizzo di FlipFinder viene chiesto al momento
  (`optional_host_permissions`), non all'installazione.
- **Parser unico guidato da configurazione.** `parser-config.json` contiene etichette, regex,
  selettori e percorsi JSON, ed è lo stesso file usato dal backend. Il sistema lo serve
  all'estensione con un numero di versione, quindi si aggiorna senza ripubblicare. Una copia
  inclusa fa da riserva.
- **Lettura efficiente.** `MutationObserver` con debounce per le card nuove,
  `IntersectionObserver` per valutarle quando entrano a schermo e `requestIdleCallback` per il
  parsing. Nessun polling.
- **Pagine riconosciute:** annuncio, catalogo/ricerca, armadio, preferiti.
- **Sulle card:** badge con punteggio, margine e "già tracciato", più le azioni rapide (traccia,
  analisi approfondita, apri nel tracking).
- **Pannello laterale** (Side Panel API) con la classifica live: vedi
  [Obiettivo 7 nel README](../README.md).
- **Pagina di impostazioni:** indirizzo, chiave, soglie, filtri predefiniti, interruttori, suoni.
