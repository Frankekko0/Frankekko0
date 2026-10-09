# ARCHITECTURE — mappa operativa (1 pagina)

Da consultare al posto di riesplorare il repo. Dettagli: formule e API in [`DESIGN.md`](DESIGN.md) · modello dati in [`DATI.md`](DATI.md) ·
**stato, decisioni dell'utente e piano per fase in [`PIANO_FLIPFINDER.md`](PIANO_FLIPFINDER.md) (fonte unica)** · decisioni/limiti del prompt v3 in [`DECISIONS.md`](DECISIONS.md), [`LIMITATIONS.md`](LIMITATIONS.md).

## Stack
Backend Python 3.11+ · FastAPI · SQLAlchemy 2 async + asyncpg · Alembic · Pydantic v2 · arq (Redis) · structlog · PostgreSQL 16 (`pg_trgm`) · Redis 7.
Frontend Next.js 16 / React 19 / Tailwind v4 / TanStack Query. Estensione browser MV3 (`extension/`, JS puro, side panel). AI: Anthropic SDK (`ai/llm.py`), opzionale e spenta senza chiave.
Deploy: Docker Compose (`docker-compose*.yml`), Caddy HTTPS, backup in `deploy/`.

## Cartelle (`backend/app/`)
`core` config/sicurezza/cache/money · `db/models` ORM · `marketplace` `MarketplaceProvider` (= il `DataSource` del prompt) + `capabilities` (cosa fornisce ogni modalità di cattura) ·
`acquisition` cattura da estensione/link/email, parser (`vinted_parser.json`) · `ingestion` normalizza/dedup/upsert · `identification` · `vision` hash e controlli foto ·
`media` archivio foto e repost visivi · `authenticity` · `pricing` comparabili, FMV, prove di prezzo (`evidence`) · `external` prezzi esterni opzionali (Serper) ·
`demand` · `profit` calcolatore+offerte · `scoring` (Flip Score a 8 componenti, confidenza, rischio) · `decision` (verdetti, completezza, veti, classifica, allocazione del capitale) · `opportunities` pipeline/engine/query · `tracking` ciclo di vita e "da verificare" · `alerts` ·
`analytics` portfolio/calibrazione · `ai` analista, ricerca NL, client del modello con **budget** e interruttore · `agent` strumenti, ciclo, guardrail, revisione, fasi S0–S5 · `analysis` plugin per categoria, condizioni, etichette, copertura, testo multilingua, matrice di coerenza, dossier · `workers` arq · `api/v1` REST · `tools` CLI. Frontend: `frontend/src/app/(app)/*`.

## Schema DB (38 tabelle, migrazioni `0001`–`0020`, prossima **0021**)
Annunci: `listings` `listing_snapshots`(osservazioni tipizzate) `listing_price_history`(vista) `listing_images` `sellers`. Catalogo: `brands` `categories` `products`.
Analisi: `analyses`(immutabili, 5 blocchi) `opportunities`(= analisi corrente) `opportunity_scores` `market_comparables` `market_statistics` `analysis_jobs`.
Prezzi: `sold_sales`(prezzo richiesto ≠ reale) `model_price_stats` `external_prices` `external_searches`. Utente: `users` `user_preferences` `notification_settings` `push_subscriptions` `api_keys` `watchlists` `favorites` `user_affinities`.
Agente e spesa AI: `ai_usage` `agent_runs` `events`(append-only) `vision_cache`. Business: `purchases` `sales` `inventory`. Avvisi: `alerts` `alert_deliveries`. Altro: `acquisition_attempts` `marketplace_actions` `system_state`. Importi `Decimal`; ROI memorizzato come rapporto (0,6923), mostrato in %.

## Punti di integrazione (dove si innesta il prompt v3)
- Pipeline per annuncio: `opportunities/pipeline.py::analyze` → `engine.py::run_analysis` (completezza → Flip → **decisione** → analista). Stadi S0–S5 e dossier si innestano qui; **nessun secondo pipeline**.
- Calcolo esatto: `profit/calculator.py` (formule, una sola copia) + `profit/evaluation.py` (`evaluate_deal`: stati dei costi, margine, pareggio) = futuro tool `finance_calc`.
- Prove di prezzo: `pricing/evidence.py` + `sold_sales`/`model_price_stats` (oggi fondono ancora i tipi di prezzo: D1 del PIANO, v3 Fase 3).
- AI: `ai/llm.py` (`structured`, `converse`; budget in `ai/budget.py`, interruttore in `ai/breaker.py`, routing economico/forte), `vision/analyzer.py`, `workers/vision_queue.py` → cache per hash (fase di visione).
- Agente: `agent/tools.py` (registro), `agent/loop.py` (ciclo), `agent/guardrails.py`, `agent/review.py` (revisione dei candidati, job `review_candidates_task` ogni 30 min), `agent/stages.py` (cosa si rifà dopo una modifica).
- Job: `workers/tasks.py` + `workers/main.py` (`_functions`, `_cron_jobs`) per orchestratore, monitor, learner.
- Azioni su Vinted: **nessuna**. L'estensione legge solo le pagine che l'utente apre; "Apri su Vinted" è un link. Guardiani: `extension/tests/no-automation.test.mjs`, `backend/tests/unit/test_server_reads_no_vinted_pages.py`.
- Test: `backend/tests/{unit,integration,api}` (Postgres+Redis reali, migrazioni eseguite), `extension/tests`+`e2e`, `frontend` Vitest.

## Da preservare (non rompere)
Parser Vinted per ID (niente foto profilo/suggeriti) · verdetto rapido locale `quick.js` · dedup a livelli + repost visivo con prova · osservazioni a sola aggiunta e `analyses` immutabili ·
`to_verify` (48 h/24 h) · `sold_sales` con `price_kind` · autenticità per foto (mai "al 100%") · profitto corretto per rischio · tracking · produzione (Caddy, backup, registrazione chiusa) · `labeled_cases.json`.

## Copertura del prompt v3 sulle fasi del PIANO (stato reale)
| Fase v3 | Stato | Dove / nota |
|---|---|---|
| 0 Audit | fatta | questo file, `DECISIONS.md`, `LIMITATIONS.md` |
| 1 Core data | **fatta** | PIANO Fase 2 (osservazioni, `last_verified_at`, `to_verify`, `analyses`, dedup, CSV) + `marketplace/capabilities.py` |
| conformità §2 (estensione solo su ciò che l'utente vede) | **fatta** | rimossi scanner, letture automatiche, lettura dal server, clic su Preferiti/Acquista, permessi `scripting`/`offscreen`/`notifications` (Q1); foto caricate dal browser (Q3-B, D50–D52) |
| 2 Financial engine | **fatta** | PIANO 4.1: `profit/evaluation.py` (stati dei costi, ripristino, riserva, margine, pareggio, capitale), `/profit/calculate`, blocco economico delle analisi; test 26/44/18/69,23%, casi E e H |
| 3 Market intelligence | **fatta** (perimetro v3) | motore e provenienza già completi; aggiunti test G end-to-end e dichiarazione della base di misura (D1). Taglie per marca e titoli/sinonimi: PIANO 4.3–4.4, nella fase di dossier |
| 4/4b Vision e dossier | **fatta** (foto via Q3-B: opt-in, non provato su Vinted reale, L21) | `analysis/` (dossier P0–P12, coverage, matrice di coerenza, condizioni, etichette), OCR locale RapidOCR, cache delle analisi, difetti con zona/riquadro, ruoli delle foto; migrazioni 0019–0020; test B, C, D, F, T, U, V, W. Foto caricate dall'estensione: `PUT /capture/photos/…`, `media/archive.py` (validazione), analisi sui byte |
| 5 Decisione | **fatta** | `decision/` (6 verdetti, 4 punteggi separati, veti, STRONG BUY a requisiti, classifica, zaino esatto), Flip Score a 8 componenti, migrazione 0017; casi A, C, E, G, H e B (lato decisione) |
| 6 Agente orchestratore | **fatta** (perimetro v3) | registro di 9 strumenti, ciclo con tool use, guardrail nel codice, budget AI con tetti e stop, interruttore, traccia in `agent_runs`, `events` append-only, ricalcolo incrementale (35→23: solo decisione, un avviso), avvisi coalescenti e legati al verdetto, revisione con ripiego a regole. Strumenti delle fasi successive: L16 |
| 7 Dashboard | da fare | PIANO Fase 8 (serve Q5, lingua) |
| 8 Vendita e portfolio | parziale | PIANO Fase 9; `purchases/sales/inventory` esistono |
| 8b Autonomia · 8c Intelligenza avanzata · 8d Imprenditore | da fare | nuovi in v3; esecuzione su Vinted solo dry-run/assistita (LIMITATIONS L01) |
| 9–10 Hardening e release | da fare | PIANO Fase 10 |
