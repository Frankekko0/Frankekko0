# Misura della velocità su pagine Vinted reali

Lo script `extension/e2e/real-speed.e2e.cjs` apre ricerche vere di vinted.it (96 articoli per pagina) in Chromium, con l'estensione vera collegata a un FlipFinder avviato. Misura dove va il tempo: lettura della pagina, rete, database, immagini, visualizzazione.

È riutilizzabile: stesso comando prima e dopo una modifica, poi si confrontano le righe `RESULT`.

## Preparazione (database usa-e-getta, mai il tuo)

Dalla cartella `backend/`, con il database `flipfinder_baseline` già creato:

```bash
export DATABASE_URL=postgresql+asyncpg://flipfinder:flipfinder@localhost:5432/flipfinder_baseline
export REDIS_URL=redis://localhost:6379/9 ENVIRONMENT=development IMAGE_ARCHIVE_ENABLED=false AI_API_KEY=
export JWT_SECRET=una-stringa-casuale-di-almeno-40-caratteri-0123456789
.venv/bin/alembic upgrade head
PYTHONPATH=$PWD .venv/bin/python -m tests.bench.seed_market --database-url $DATABASE_URL --redis-url $REDIS_URL
```

`seed_market` crea un **mercato sintetico** ("benchmark sintetico"):

- annunci venduti, in vendita e rimossi degli ultimi 120 giorni, per ogni brand, categoria e linea della tassonomia;
- prezzi plausibili (per esempio polo Ralph Lauren circa 24 €, Air Max 90 circa 58 €, Nuptse circa 130 €);
- poi ricalcola le statistiche di mercato.

Serve solo a dare comparabili all'analisi: nessun suo numero è un prezzo reale. Ogni riga ha `raw.synthetic = true`. Rifiuta i database il cui nome non contiene `bench`, `baseline`, `speed`, `test`, `e2e`, `tmp` e quelli con acquisti o vendite. Rilanciato sostituisce il mercato precedente (stesso `--seed`, stesso mercato).

Poi avvia API e worker:

```bash
# API: il codice che non manda ancora Server-Timing passa dalla sonda (non tocca il codice dell'app)
PYTHONPATH=$PWD .venv/bin/uvicorn --factory tests.bench.db_probe:create_app --host 127.0.0.1 --port 8100 &
PYTHONPATH=$PWD .venv/bin/python -m app.workers.main &
```

La sonda `tests/bench/db_probe.py` aggiunge `Server-Timing: db;dur=…, analysis;dur=…, total;dur=…` a ogni risposta. Se l'app manda già l'intestazione, la lascia com'è. Con `FF_DB_PROBE_LOG=<file>` scrive anche una riga JSON per richiesta.

## Comando

Dalla radice del repository:

```bash
NODE_PATH=/opt/node22/lib/node_modules APP_URL=http://localhost:8100 \
  DB_URL=postgresql://flipfinder:flipfinder@localhost:5432/flipfinder_baseline \
  OUT=/tmp/real-speed.json LABEL="prima della modifica" \
  node extension/e2e/real-speed.e2e.cjs
```

| Variabile | Default | Cosa fa |
|---|---|---|
| `LOADS` | 20 (massimo 25) | caricamenti, a giro sulle ricerche |
| `SEARCHES` | `ralph lauren polo\|nike air max 90\|the north face nuptse\|levis 501\|carhartt` | le ricerche |
| `PAUSE_MS` | 4000 | pausa tra due caricamenti |
| `SETTLE_MS` | 45000 | attesa massima dei verdetti del server |
| `VISION_TOP`, `VISION_WAIT_MS` | 5, 20000 | migliori candidati di cui si attende l'analisi foto |
| `PROXY_PORT` | 8190 (0 = nessuno) | proxy di misura tra estensione e FlipFinder |
| `DB_URL` | — | serve per foto e stato del database; senza, "non misurato" |
| `SAVED_PAGE` | — | pagina Vinted salvata al posto del sito, solo per sviluppo (segnalato nell'output) |

**Delicatezza con Vinted:**

- al massimo 25 pagine per giro, a qualche secondo di distanza;
- nessuno scorrimento e nessun clic: i banner di consenso e di paese restano lì;
- nessun annuncio aperto oltre a quelli che apre l'estensione da sola.

**Al primo rifiuto la misura si ferma.** Se Vinted risponde 403 o 429, o mostra una verifica anti-bot, lo script non riprova e non tenta di aggirarla: lo scrive in `refused`.

## Cosa significa ogni numero

I tempi sono in millisecondi dall'inizio della navigazione. "da DOM" vuol dire dal momento in cui il browser ha finito di leggere l'HTML (DOM interattivo).

Per ogni metrica: mediana, minimo–massimo e numero di caricamenti. Poi la stessa cosa divisa tra **prima visita** di una ricerca e **visite ripetute**: nelle ripetute l'estensione ha già in memoria le valutazioni del server.

| Famiglia | Numeri |
|---|---|
| **Pagina** (rete e lettura di Vinted) | primo byte, HTML scaricato, DOM interattivo, DOMContentLoaded, load, primo contenuto disegnato, peso dell'HTML, schede sulla pagina |
| **Estensione** | avvio sul documento; lettura delle schede (durata e fine); primo verdetto rapido; migliore evidenziata; verdetto rapido su tutte. Poi: schede con stima rapida, schede con un verdetto in quel momento, verdetti ridisegnati e badge persi se la pagina si ridisegna da sola |
| **Server** | primo e ultimo verdetto completo sui badge, schede con verdetto del server |
| **Rete** | ogni richiesta dell'estensione a FlipFinder, misurata dal proxy: durata, primo byte, KB, schede inviate (doppioni compresi). Ci sono una tabella per endpoint e una per le richieste una tantum: associazione, configurazione, riepilogo di mercato |
| **Database** | somma del tempo SQL per pagina e per richiesta, e quota sul tempo del server. Viene da `Server-Timing` `db`: tempo dentro le istruzioni SQL |
| **Analisi** | somma di `Server-Timing` `analysis` per pagina: analisi completa, SQL compreso |
| **Immagini** | analisi foto (vision) dei migliori `VISION_TOP` candidati della pagina, ordinati per profitto atteso del server: quante finite e in quanto tempo. Controllo nel database ogni secondo |
| **Visualizzazione** | badge rapidi e migliore disegnati (frame successivo al segnale dell'estensione); task lunghi del thread principale durante il lavoro dell'estensione e fino ai verdetti; task lungo massimo |

**Da dove vengono i tempi dell'estensione:** dai suoi segnali `performance.mark("ff:…")` e dagli attributi `data-ff-*` su `<html>`, letti nel momento in cui compaiono. Il nuovo codice aggiunge `cards-read`, `quick`, `stats`, `server-first`, `server-all` e `data-ff-timing`: lo script li usa quando ci sono.

**Come si riconosce un badge:** dal testo e dall'etichetta. "≈ €" è stima rapida, "score · €" è verdetto del server. Se cambia il badge, va aggiornato `kind()` nello script.

## Limiti da sapere

- **Mercato sintetico.** Le analisi del server lavorano su comparabili sintetici. I tempi sono realistici, i prezzi no.
- **Archivio foto spento** (`IMAGE_ARCHIVE_ENABLED=false`) per non scaricare dal CDN di Vinted ogni foto vista. In produzione l'archivio è acceso: il worker ha in più lo scaricamento delle foto e ogni cattura accoda quel lavoro.
- **Stessa macchina.** Browser, API, worker e database stanno sullo stesso computer, condiviso con altri processi. Il carico medio è riportato per ogni caricamento.
- **Uscita verso internet.** Le pagine di Vinted passano dal proxy di uscita dell'ambiente, se c'è (`HTTPS_PROXY`). La rete verso Vinted pesa sul primo byte.
- **Chromium headless.** Vinted (Cloudflare) può rispondere con una verifica anti-bot: in quel caso la misura si ferma. Non va mai aggirata.
