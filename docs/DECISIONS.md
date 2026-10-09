# DECISIONS — prompt v3 (una riga ciascuna: decisione → motivo)

Le decisioni già prese dall'utente (Q1–Q3, C1, ecc.) stanno in [`PIANO_FLIPFINDER.md` §11.2](PIANO_FLIPFINDER.md) e sono vincolanti: qui non si riaprono.

- D01 `PIANO_FLIPFINDER.md` resta la fonte unica di stato e decisioni; il prompt v3 si innesta sulle sue fasi (mappa in `ARCHITECTURE.md`), nessun secondo piano → evita due verità.
- D02 Il vecchio `docs/ARCHITECTURE.md` (design completo: formule, API) è `docs/DESIGN.md` (`git mv`, link aggiornati); `ARCHITECTURE.md` è ora la mappa di 1 pagina richiesta dal prompt.
- D03 Si costruisce sul codice esistente: nessun secondo pipeline, DB o servizio; stack invariato.
- D04 `MarketplaceProvider` è il `DataSource` del prompt; `marketplace/capabilities.py` dichiara cosa fornisce ogni modalità di cattura ed è verificato contro il parser con test → la tabella non può promettere più di quanto si legge.
- D05 L'AI-agente usa tool use dell'SDK Anthropic già presente; modelli solo da configurazione, routing per livello (economico → potente), senza chiave l'app degrada alle regole. **Nessuna chiamata a pagamento senza tetto di spesa giornaliero** (PIANO Q8 propone $1/giorno).
- D06 Autonomia (§3.2): si costruisce il framework intero (limiti hard, dry-run 7 giorni di default, kill switch, audit append-only, auto-sospensione, `ExecutionChannel`). Per Vinted i canali sono `DryRun` e `Assisted` ("Apri su Vinted": prepara l'azione, il gesto finale è dell'utente). **Nessun canale non presidiato su Vinted**: non esiste API ufficiale, i Termini in vigore dall'8/10/2026 vietano strumenti esterni per i preferiti, l'utente ha già deciso (Q1 f/g) di sostituire i clic programmatici, e il prompt (§2, §3.2) vieta bot e automazione dell'interfaccia. Un altro marketplace con API ufficiale si aggiunge implementando `ExecutionChannel`.
- D07 Il dry-run resta attivo finché l'utente non lo azzera esplicitamente → previsto dal prompt; nessuna spesa reale decisa dall'AI senza quel passaggio.
- D08 ROI memorizzato come rapporto (0,6923), mostrato in % → conversione solo in presentazione.
- D09 Il log generale `events` (audit di azioni e tracce dell'agente) si introduce nella fase che lo usa (Fasi 6 e 8b), non prima: per gli annunci osservazioni e `analyses` sono già a sola aggiunta e un secondo log li duplicherebbe.
- D10 "Non verificato di recente" è lo stato `to_verify` del PIANO (C1: 48 h attivi / 24 h riservati, configurabili); una mia prima versione (1 h/6 h) è stata scartata perché contraddiceva C1.
- D11 Test su Postgres e Redis reali come già in `conftest.py`; ambiente di sessione = venv e cluster locali fuori dal repo, nessuna dipendenza nuova per i test.
- D12 Connettori della sessione senza attinenza (Shopify, Gmail, Webflow, Viewmax, traidingkit…) non usati: nessuno serve al prodotto e alcuni agirebbero verso l'esterno. Firecrawl e simili **non** leggono Vinted (PIANO §9.4: nessun servizio di scraping); solo documentazione di librerie.
- D13 Lingua: documenti in italiano come il repo; identificatori e commenti di codice in inglese come il codice esistente. Interfaccia: resta com'è finché non c'è Q5.
- D14 Prima di ogni push si fa `fetch`; se il remoto è avanti si riallinea senza mai forzare (sul branch lavora anche un'altra sessione).
