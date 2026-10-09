# Piano FlipFinder

> Documento di lavoro. Si rilegge all'inizio di ogni sessione e si aggiorna a ogni blocco concluso.
> Da qui in avanti è la fonte unica per le nuove fasi; `PROGRESS.md` resta come archivio storico (vedi C2).

| | |
|---|---|
| **Stato** | Fasi 1 e 2 completate il 2026-10-09. **In attesa del tuo OK per la Fase 3** (e delle risposte a Q4–Q7). |
| **Branch** | `claude/sweet-curie-9xyxzg` |
| **Codice analizzato** | commit `7655293` (2026-10-08). Il checkout di partenza era 72 commit indietro: l'ho riallineato con un fast-forward (nessuna modifica mia) prima di leggere. |
| **Codice toccato in Fase 1** | **Nessuno.** Unico file aggiunto: questo. |
| **Stack** | Invariato (FastAPI · PostgreSQL 16 · Redis · arq · Next.js · estensione MV3). Non propongo cambi di stack: non c'è nessuna prova che servano. |

**Legenda.** Copertura del brief: ✅ presente e conforme · 🟡 presente ma da adeguare · ❌ assente · ⛔ presente ma fuori dai tuoi limiti o in zona grigia (serve una tua decisione).
Dimensione dei lavori: **S** pochi file · **M** un modulo + migrazione + test · **L** più moduli, UI e dati.

---

## 1. Sintesi

1. **FlipFinder non parte da zero.** 99 commit, ~29k righe di backend, ~7k di estensione, ~13k di frontend, 486 + 46 + 46 test che passano (§3). Molte voci del brief esistono già in qualche forma (§5). Il lavoro vero non è "costruire tutto", è **allinearlo ai tuoi limiti e ai tuoi principi sui dati**.
2. **Tre cose cambiano il piano:**
   - **Limiti.** Otto funzioni oggi presenti sono fuori dai tuoi limiti o in zona grigia (scanner automatico delle ricerche, letture automatiche di pagine, lettura dal server, clic programmatici su Preferiti e Acquista, test automatici su Vinted reale). I Termini di Vinted **in vigore dall'8/10/2026** citano esplicitamente l'aggiunta ai preferiti tramite software esterno (§6.1). Per regola 10 e per i tuoi limiti non le tocco né le tengo senza una tua decisione: domanda **Q1**.
   - **Dati.** Il modello oggi è "un'analisi per annuncio, sovrascritta a ogni ricalcolo". Per tracciabilità, provenienza per valore e storico a sola aggiunta va ricostruito il livello dati, **in modo additivo e senza perdite** (Fase 2).
   - **Prove.** Tutte le misure di accuratezza esistenti sono su mercati simulati o benchmark sintetici: non dimostrano nulla sul mondo reale (§6.4). Numeri veri arriveranno solo in Fase 10 e solo con tuoi dati verificati (**Q9**).
3. **Costi.** Nulla di nuovo da pagare finché non scegli tu. L'unico costo nuovo è l'API per l'analisi delle foto (Fase 5): **da circa $0,003 a $0,35 per annuncio** a seconda del modello (§9). Oggi **non esiste alcun tetto di spesa AI**: non impostare `AI_API_KEY` prima della Fase 5.
4. **Ordine.** Fasi 2 → 10, una alla volta, con il tuo OK a fine fase. Ogni fase chiude con: file modificati, come verifichi a mano, criteri superati/non superati, limiti noti.

---

## 2. Domande per te

Ordinate per urgenza. Per ognuna c'è la mia raccomandazione: se condividi tutto, rispondi "**Q1–Q3: come consigliato**". Le **bloccanti** (Q1–Q3) servono per i blocchi 2.3 (foto) e 2.5 (venditore) e per chiudere la Fase 2; gli altri blocchi posso iniziarli appena mi dai l'OK.

### Bloccanti per la Fase 2

**Q1 · Funzioni fuori dai limiti o in zona grigia.** Per ognuna scegli *Rimuovere* / *Sostituire* (con l'alternativa indicata) / *Tenere spenta* / *Tenere*. Dettagli e riferimenti in §6.1.

| # | Funzione oggi presente | Perché la segnalo | Raccomandazione |
|---|---|---|---|
| a | **Scanner automatico delle ricerche salvate** (estensione v1.1, `scan.js`, permessi `offscreen`/`notifications`): scarica da solo pagine di ricerca ogni 15–240 min | Il brief: l'estensione legge solo le pagine che apri e scorri tu. Opt-in e spento di default, ma presente | **Rimuovere** |
| b | **Lettura automatica dei "migliori candidati"** (`autoDeep`): scarica pagine di annunci che non hai aperto | Stesso motivo | **Rimuovere** |
| c | **Aggiornamento in background degli articoli tracciati** (`slowRefresh`, coda `refresh-queue`) | Stesso motivo | **Rimuovere** |
| d | **Lettura dal server di pagine pubbliche** (`public_fetch.py`, `VINTED_PUBLIC_FETCH_ENABLED`) | Il brief ammette il ricontrollo periodico "solo se compatibile con le regole": i Termini vietano crawling e scraping | **Rimuovere** |
| e | **"Analisi approfondita"** che scarica la pagina di un annuncio non aperto da te | È una richiesta partita dal software, non una pagina che hai aperto | **Sostituire**: analisi sui dati già catturati; se mancano campi, il pulsante apre l'annuncio e la cattura passiva completa il record |
| f | **Preferiti con clic programmatico** sul cuore di Vinted (`content.js`) | I Termini in vigore dall'8/10/2026 nominano "adding Items to favourite" con strumenti esterni. Selettori mai verificati su Vinted reale | **Sostituire** con "Apri su Vinted" (il clic sul cuore lo fai tu) |
| g | **Acquista con clic programmatico** su "Acquista" (`ff:vinted-buy`) | Stessa natura di (f): l'avvio del checkout lo fa il software. Il pagamento resta tuo | **Sostituire** con "Apri su Vinted" |
| h | **Misure automatiche su vinted.it reale** (`extension/e2e/real-speed.e2e.cjs`, `docs/SPEED_MEASURE.md`) | Un Chromium automatico apre ricerche vere (il log di lavoro cita ~30 caricamenti) | **Non eseguire più**; misure solo da te a mano o su pagine salvate |

Nota: il brief (Fase 3) chiede "aggiungi ai preferiti" e "apri il checkout" con un clic. Con (f)/(g) *Sostituire* li copro nel modo compatibile: un clic ti porta all'annuncio giusto su Vinted, il gesto finale lo fai tu. Se preferisci tenere i clic programmatici, lo implemento solo dopo un tuo "sì" esplicito scritto qui, sapendo che è il caso nominato dai Termini.

**Q2 · Dati del venditore.** Il brief consente solo valutazione media e numero di recensioni. Oggi si conserva anche una **chiave pseudonima** (SHA-256 senza segreto, invertibile per forza bruta perché gli ID sono numerici), che serve a: riannunci dello stesso venditore, foto riciclate fra venditori, anomalia "stesso articolo in più taglie", abitudine di ribasso. Proposta: **tenere la chiave ma proteggerla con un segreto del tuo server (HMAC lato server, migrazione sul posto), eliminare le colonne inutilizzate del venditore e il campo `seller_username` ancora accettato dall'API**. Alternativa: nessuna chiave, e perdi quelle quattro funzioni. → **Raccomando di tenere la chiave protetta.**

**Q3 · Foto.** Tre sotto-scelte:
- **Trasporto.** (A) come oggi: il *server* scarica le foto dal CDN di Vinted; (B) le carica *l'estensione* dal tuo browser, che le ha già caricate per mostrarti l'annuncio. Con (B) il backend non contatta mai Vinted (lo faccio verificare da un test) e, per l'analisi AI, mando ad Anthropic i byte della copia locale, non gli URL di Vinted. Costo: più lavoro in Fase 3. → **Raccomando B.**
- **Quali.** Tutte le foto degli articoli *analizzati*; per i *visti* solo la copertina già caricata dalla card. Stima spazio: ~2,4 MB per articolo analizzato (8 foto × ~0,3 MB, **da misurare** sui primi dati reali).
- **Per quanto.** Per sempre, oppure N giorni dopo vendita/rimozione. → **Raccomando di conservare sempre le foto degli articoli tracciati o acquistati e per 90 giorni le altre.**

### Prima della Fase 3

**Q4 · Pagine reali per i test.** Dal tuo browser (Ctrl+S, "solo HTML"): una pagina **Preferiti**, un **armadio** di venditore, una **ricerca con ~96 card**, un annuncio **riservato**, uno **venduto**, e un annuncio in lingua/dominio diverso se lo usi. Le uso solo come fixture anonimizzate: da qui non leggo né uso annunci di Vinted, quindi senza queste pagine non posso verificare la lettura di Preferiti, armadio e "riservato" (§6.4, N6).

**Q5 · Interfaccia.** Oggi è in gran parte in inglese ("Deals", "My Flips", "Quick sale", "No opportunities yet") con pezzi in italiano. Vuoi **tutta in italiano**? Inoltre, nel progetto non ci sono skill di design installate (`.claude/` non esiste): per le Fasi 7–8 posso usare quelle disponibili in sessione (`impeccable` per l'interfaccia, `dataviz` per i grafici). → **Raccomando italiano ovunque e uso di quelle skill.**

### Prima della Fase 4

**Q6 · I tuoi costi reali e due definizioni.**
- Dimmi (o imposta in Impostazioni) i tuoi costi tipici: imballaggio per capo, spedizione se la paghi tu, promozioni, ripristino (lavaggio/stiratura). Finché non li dai restano **"sconosciuti"** e il profitto è mostrato come "al lordo di costi non impostati": niente 0,50 € di imballaggio o 3,49 € di spedizione inventati (oggi sono i valori predefiniti, §6.2 D5).
- Conferma le definizioni: **ROI = profitto netto ÷ costo totale** (come nel brief) e **margine sulla vendita = profitto netto ÷ prezzo di vendita**.

**Q7 · Prezzi da altri mercati (Serper).** È già integrato, **spento** finché non imposti una chiave: 2.500 query gratis, poi $50 per 50.000 query valide 6 mesi (tetto di default 900 query/mese). Lo tengo come modulo **opzionale, spento di default**? Oppure lo rimuovo? → **Raccomando: opzionale e spento.**

### Prima della Fase 5

**Q8 · Modello AI e tetto di spesa.** La scelta formale è all'inizio della Fase 5 (opzioni e costi in §9). Anticipo la tua preferenza e il **tetto giornaliero** (proposta: **$1 al giorno**, ~€0,9, con arresto visibile quando raggiunto). Dichiaro anche che **le foto degli articoli escono dal tuo server verso Anthropic**: i contenuti possono includere persone; ignorerò le persone nell'analisi, ma il dato viaggia. Per Fable 5.1 la documentazione richiede conservazione dei dati a 30 giorni (non è disponibile con zero data retention).

### Prima delle Fasi 6 e 10

**Q9 · Dati reali per le misure.** Senza dati verificati da te, la Fase 10 può dire solo "campione insufficiente". Puoi darmi: (a) i tuoi **acquisti e vendite reali** (export CSV dalla tua istanza) e (b) un **elenco di almeno 30 annunci** con marca, modello, condizione e, quando noto, prezzo di rivendita reale? In alternativa le misure le lanci tu sul tuo server con script che preparo e mi incolli i risultati.

**Q10 · Capitale iniziale (Fase 6).** Budget totale, massimo per articolo, profitto minimo, ROI minimo, rischio massimo, numero massimo di articoli. Li imposti tu in Impostazioni; mi servono per i test dei casi A–H.

### Conferme rapide (se non rispondi, procedo così)

| # | Proposta |
|---|---|
| C1 | Soglia "da verificare": **48 h** senza nuova osservazione per gli annunci attivi, **24 h** per i riservati; configurabile. |
| C2 | `PROGRESS.md` diventa archivio (`docs/archivio/`) quando tocco la documentazione; questo file è l'unico stato. |
| C3 | Le misure su mercato simulato spariscono da README/PROGRESS (o sono marcate "non valide") quando tocco quei file. |
| C4 | Un commit per blocco funzionante sul branch `claude/sweet-curie-9xyxzg`; nessuna pull request se non la chiedi. |
| C5 | Permesso host dell'estensione: tengo il permesso *opzionale* per un solo indirizzo (è l'unico modo per scegliere dove sta il tuo server) e rimuovo `offscreen`, `notifications` e `scripting` se Q1 li rende inutili. |

---

## 3. Stato di partenza misurato

Misurato in questa sessione sul commit `7655293`, con PostgreSQL 16 e Redis locali, in un ambiente virtuale fuori dal repository (il repository è rimasto pulito).

| Area | Dimensione | Test | Esito |
|---|---|---|---|
| Backend (`backend/app`) | 179 file Python, 28,9k righe, 9 migrazioni Alembic | 486 test (43 file, 8,8k righe) | **486 passati** in 71 s · `ruff` pulito |
| Estensione (`extension/src`) | 13 file, 6,9k righe, manifest v1.1.0 | 46 test unitari | **46 passati** |
| Frontend (`frontend/src`) | 81 file, 13,4k righe, Next.js 16 / React 19 | 46 test (vitest) | **46 passati** · `tsc` e `eslint` puliti |

**Non eseguiti:** i test end-to-end dell'estensione (Chromium) e i benchmark. Da qui non ho letto né usato annunci di Vinted: ho consultato solo i suoi Termini e il `robots.txt`, che sono pubblici.

---

## 4. Come funziona oggi

### 4.1 Componenti

```
Browser su Vinted                         Tuo server (Docker)
┌─────────────────────────┐   HTTPS     ┌────────────────────────────────────────────┐
│ Estensione MV3           │ ─────────▶ │ FastAPI  /capture/*  /extension/*  /api/v1  │
│  content.js  legge card  │  chiave    │   ingestione → identificazione → analisi    │
│   e annuncio, badge      │  ff_ext_   │   → opportunities + avvisi                  │
│  background.js  coda     │            │ Worker arq (Redis): analisi, foto, statistiche│
│  panel.js  pannello      │ ◀───────── │   prezzi esterni, archivio immagini         │
└─────────────────────────┘ valutazioni │ PostgreSQL 16 · Redis 7 · var/media         │
                                        └──────────────────▲─────────────────────────┘
Web app Next.js ───────────────────────────────────────────┘
```

Pagine della web app (le tue "Analisi" e "tracking"): **Analisi** = `/analyze`; **tracking** = `/items` (Archivio) e `/items/[ref]` (scheda). Poi `/` (dashboard), `/deals` e `/deals/[id]`, `/import`, `/flips`, `/analytics`, `/watchlists`, `/saved`, `/alerts`, `/search`, `/settings`.

### 4.2 Il percorso di un annuncio

1. Apri una ricerca o un annuncio su Vinted. L'estensione legge la pagina (card quando entrano a schermo, annuncio intero se aperto) con un parser guidato da `vinted_parser.json`, servito dal backend e quindi aggiornabile senza ripubblicare.
2. **Verdetto rapido locale** (`quick.js`): usa un riepilogo di mercato scaricato ogni 3 h e le statistiche per modello di `POST /extension/page-stats`.
3. Le catture finiscono in una coda locale e partono verso `POST /capture/cards|item` con la chiave `ff_ext_…` (nessun cookie o token di Vinted: `credentials: "omit"`).
4. **Ingestione** (`ingestion/service.py`): dedup per (provider, ID Vinted) più URL, venditore+titolo, hash foto; snapshot a sola aggiunta; macchina a stati (`tracking/status.py`).
5. **Analisi** (`opportunities/pipeline.py` + `engine.py`): identificazione (tassonomia di **28 marchi**) → comparabili → valore di mercato → profitto → rischio/autenticità → Flip, Confidence, Risk → una riga in `opportunities` e una in `opportunity_scores`.
6. **Worker:** copia locale delle foto, analisi foto (solo con `AI_API_KEY`), statistiche per modello, tabella `sold_sales`, prezzi esterni (opzionale), calibrazioni notturne, avvisi (in-app, push, e-mail, Telegram, Discord).

### 4.3 Dati: tabelle che contano

`listings` (identità, stato, ultimo prezzo) · `listing_snapshots` (osservazioni a sola aggiunta: prezzo, stato, preferiti, visualizzazioni, n. foto) · `listing_price_history` (duplica in parte gli snapshot) · `listing_images` (URL, posizione, hash, copia locale) · `sellers` (valutazione e recensioni + chiave pseudonima) · `opportunities` (**1 riga per annuncio, sovrascritta**) · `opportunity_scores` (storico dei soli punteggi) · `market_comparables` (cancellati e riscritti a ogni analisi) · `sold_sales` (vendite concluse con **tipo di prezzo**: pagato / incassato / ultimo visto / riportato) · `model_price_stats` (mediana, fascia, giorni, campioni per modello/taglia/condizione) · `external_prices`/`external_searches` (Serper) · `purchases`/`sales`/`inventory` (My Flips) · `marketplace_actions` · `alerts`.

### 4.4 Stato di vendita

`active · reserved · sold · removed · unknown`. "Venduto" richiede una prova positiva (pagina, e-mail, fonte); una pagina sparita è "rimosso" e **non** produce una vendita. La data di vendita è la metà fra l'ultima volta visto attivo e la rilevazione. Un controllo non riuscito non cambia lo stato. Questa parte è solida e testata.

### 4.5 Servizi esterni oggi integrati (tutti opzionali e spenti per default)

Anthropic (analisi foto e commento dell'analista) · Serper (prezzi da altri mercati) · SMTP/Telegram/Discord/Web Push (avvisi) · IMAP in sola lettura (e-mail di Vinted). Nessuno è attivo senza chiave.

---

## 5. Copertura del brief

### Fase 2 — Dati, tracciabilità, stato di vendita

| Requisito | Stato | Dove / nota |
|---|---|---|
| Schema versionato a blocchi (prodotto, visiva, economica, mercato, decisione) | 🟡 | `opportunities` è 1 riga/annuncio sovrascritta; il blocco visivo vive in `listings.identification`; nessuna versione di schema (D8) |
| ID interno, ID Vinted, URL, fonte, data, versione analisi | 🟡 | Ci sono tutti ma su tabelle diverse; una decisione passata non è ricostruibile |
| "Visto" (leggero) e "analizzato" (completo) | 🟡 | `capture_level` link/card/full e `analysis_depth` quick/full: mappabili, manca la regola esplicita |
| Storico a sola aggiunta: prezzo, preferiti, stato | ✅ | `listing_snapshots` |
| Storico delle foto | ❌ | Solo `photo_count`; il set viene cancellato e reinserito (D7) |
| Prezzo iniziale, attuale, minimo osservato | 🟡 | Derivabili dagli snapshot, non esposti; il "primo prezzo" è datato alla pubblicazione presunta (D6) |
| Dedup per ID Vinted | ✅ | Chiave unica (provider, ID) con test |
| Dedup visiva tramite hash | 🟡 | `dHash` + SHA-256 esistono, ma l'estensione non invia hash: si calcolano solo per gli annunci che passano l'analisi foto |
| Copia locale di tutte le foto, in ordine, senza avatar | 🟡 | `media/archive.py` ✅ (esclude avatar); scaricata dal server (Q3); riscritta a ogni cattura più ricca |
| Stati attivo/riservato/venduto/rimosso/**da verificare** | 🟡 | Manca "da verificare": `lifecycle_stale_hours` è definito e **mai usato** (D9) |
| Stato aggiornato a ogni passaggio dell'estensione | ✅ | Snapshot + macchina a stati |
| Preferiti: aggiornamento in blocco | 🟡 | Le card dei Preferiti entrano come ogni card; nessuna logica dedicata |
| Ricontrollo periodico lento | ⛔ | Lettura dal server, `slowRefresh`, scanner (Q1) |
| Alla vendita: data stimata, ultimo prezzo visto, giorni online → tabella prezzi venduti | 🟡 | `sold_sales` + `model_price_stats` ✅ (con tipo di prezzo); manca CSV, vista dedicata, e i prezzi "ultimo visto" e "reali" sono nella stessa mediana (D1) |
| Ricalcolo su cambio prezzo/foto | 🟡 | Prezzo ✅ (`analysis_needed`); foto da verificare con test |
| Migrazione senza perdite · CSV | 🟡 | CSV degli articoli ✅ (`/items/export.csv`); migrazione da scrivere |

### Fase 3 — Estensione

| Requisito | Stato | Dove / nota |
|---|---|---|
| Cattura: annuncio (tutti i campi e foto), catalogo/ricerca (tutte le card) | ✅ | Parser JSON + DOM, 15 lingue; fixture reali di pagina articolo e card dell'armadio |
| Cattura: armadio, Preferiti | 🟡 | Tipi di pagina riconosciuti; nessuna fixture reale dei Preferiti (Q4) |
| Coda locale, retry, dedup, indicatore | ✅ | `core.js` (1 voce per ID, backoff), badge della toolbar |
| Stato connessione visibile · ricollegamento con un clic | 🟡 | Stato ✅; il ricollegamento richiede di incollare la chiave |
| Badge sulle card: punteggio, profitto netto, già tracciato | ✅ | `content.js` |
| Azioni a un clic: traccia · apri nel tracking | ✅ | |
| Azioni a un clic: analisi approfondita · preferiti · checkout | ⛔ | Q1 e–g |
| Parser/selettori in un modulo, config aggiornabile | ✅ | `vinted_parser.json` → `parser-config.js` |
| Observer + debounce, nessun rallentamento | ✅ | `IntersectionObserver`/`MutationObserver`; nessun task > 92 ms nelle misure |
| MV3, permessi minimi | 🟡 | `storage, alarms, sidePanel, scripting, offscreen, notifications` + host opzionali http/https qualunque (C5) |
| Pagina impostazioni | ✅ | `options.html` |

### Fase 4 — Valore di mercato e profitto

| Requisito | Stato | Dove / nota |
|---|---|---|
| Fonti in ordine: vendite reali > comparabili Vinted > esterni | ✅ | `sold_sales` (tue vendite > tuoi acquisti > venduti Vinted > esterni), `pricing/evidence.py`, Serper; una fonte resta accesa solo se il backtest non peggiora l'errore |
| Statistica robusta, duplicati rimossi, campione insufficiente dichiarato | ✅ | Outlier su log-prezzi (IQR + MAD), `data_quality` ok/limited/insufficient |
| Prezzo rapido / di mercato / ottimistico con intervallo e confidenza | 🟡 | Esistono quick/expected/optimistic + minimo–massimo calibrati + confidenza; intervallo non uniforme |
| Taglie normalizzate solo se affidabile | 🟡 | Conversioni UK/US→EU con scarti fissi, nessuna taglia di marca, nessun flag di affidabilità (D11) |
| Sinonimi/abbreviazioni/errori senza presumere il modello | 🟡 | Confronto di modello rigido per i prezzi esterni ✅; per i comparabili Vinted il modello pesa 0,20/1,00 (D10); 28 marchi |
| Costo totale = prezzo + spedizione + protezione + altri + **ripristino** | 🟡 | Manca il ripristino |
| Ricavo netto con costi di vendita realmente applicabili | 🟡 | Formula ✅; default inventati (D5) |
| Costi confermati / stimati / sconosciuti | ❌ | |
| Margine sulla vendita · prezzo di pareggio | ❌ | |
| Prezzo massimo d'acquisto · scenari | ✅ | `profit/calculator.py` |
| Caso 20+4+2 → 26 / 44 / 18 / 69,23 % | 🟡 | La formula lo dà se i costi sono espliciti; manca il test |
| "Valutazione non verificata" senza comparabili affidabili | 🟡 | Esiste `insufficient`; da rinominare e collegare |

### Fase 5 — Immagini, condizioni, etichette

| Requisito | Stato | Dove / nota |
|---|---|---|
| Modello multimodale via API | 🟡 | `ClaudeVisionAnalyzer`: tutte le foto (max 20) in una chiamata, `claude-opus-5-5`, URL di Vinted passati ad Anthropic |
| Analisi solo sui candidati (pre-punteggio) | 🟡 | `worth_vision`: Flip ≥ 60 o profitto atteso > 0; senza tetto |
| Cache per hash immagine · tetto giornaliero · registro costi | ❌ | T3 |
| Identificazione: marca, categoria, modello, colore, materiali, codici | 🟡 | Campi presenti con certezza; non per riga/linea |
| Condizioni per tipo di articolo | 🟡 | 7 tipi di difetto generici; niente suola, angoli, cerniere… |
| Ogni difetto con confidenza **e foto** | 🟡 | Ha la certezza, non la foto né il riquadro (le prove di autenticità sì) |
| Classe di condizione 0–100, parti non osservabili, contraddizioni | ❌ | Esiste una stima testuale e la "condizione effettiva" |
| Etichette: 5 stati (leggibile / non leggibile / non visibile / assente dalle foto pertinenti / insufficiente) | ❌ | Oggi `has_label_photo` booleano e esiti consistent/concern/unreadable (D3) |
| Confronto etichetta ↔ titolo (taglia, composizione) | 🟡 | Si leggono `size_label` e `composition`, ma il confronto copre solo marca, categoria, colore |
| Rischio contraffazione in 4 classi, blocco se elevato | 🟡 | 4 esiti ma con "probabilmente autentico" (D4) |
| Qualità foto e copertura ispezione (2 punteggi) · foto da chiedere | 🟡 | Qualità e foto da chiedere ✅; la copertura non esiste |
| Protezione da prompt injection | ❌ | T2 |

### Fase 6 — Motore decisionale

| Requisito | Stato | Dove / nota |
|---|---|---|
| Verdetti STRONG BUY / BUY / NEGOTIATE / WATCHLIST / PASS / EVIDENZA INSUFFICIENTE | ✅ | `decision/engine.py`; il vecchio BUY/CONSIDER/SKIP è derivato (v3 D27) |
| Flip Score con i pesi del brief | ✅ | `scoring/flip.py`, 25/15/15/15/10/10/5/5 con pilastro economico (v3 D24); pesi per utente salvati ma non ancora applicati (L11) |
| Flip, Confidenza, Rischio, **Completezza** separati | ✅ | `decision/completeness.py`; colonna `data_completeness_score` |
| Veti non compensabili | ✅ | `Decision.vetoes`: contraffazione, marca a rischio senza prove, rischio ≥ 75, confidenza bassa, annuncio non disponibile |
| Motivi, avvisi, informazioni mancanti leggibili | ✅ | `reasons[]`, `warnings[]`, `missing_info[]`, requisiti dello STRONG BUY; sezione «Verdict» nella scheda |
| 7 modalità di ricerca | 🟡 | Preset: best_deals, high_profit, high_roi, fast_flip, low_risk, just_listed, hidden_gems, under_20, ultra. Brand Hunter assente |
| Capitale e combinazione migliore entro il budget | 🟡 | `decision/allocation.py` (zaino esatto) ✅; mancano le impostazioni di capitale (Q10) e la schermata |

### Fasi 7–10

| Requisito | Stato | Dove / nota |
|---|---|---|
| **F7** Pannello laterale con classifica top 5, evidenziazione, clic che scorre, filtri, avviso sonoro, contatori, blocco ed esportazione, vista dettaglio | ✅ | `panel.js` (Side Panel di Chrome). Da verificare: 200 card fluide, nessuna traccia a pannello spento, "dati insufficienti" al posto dei punteggi |
| **F7** Scelta pannello vs riquadro, motivata | ✅ | Pannello laterale: non copre la griglia, resta aperto durante la navigazione, isolato dal CSS/JS della pagina |
| **F8** Dashboard mobile-first | 🟡 | Elenchi per preset e statistiche ✅, accessibilità AA ✅; mancano capitale disponibile/investito, potenziale vs realizzato, precisione delle raccomandazioni |
| **F8** Scheda prodotto | 🟡 | `/items/[ref]` con galleria (tastiera, swipe, schermo intero, segnaposto) ✅, storico ✅, provenienza dei numeri ✅; difetti sulla foto ✗; esistono **due** pagine per lo stesso articolo (T8) |
| **F8** Avvisi utili senza ripetizioni | 🟡 | Dedup per evento ✅; mancano "cambio di rischio" e "diventato conveniente"; il testo non ha foto, costi, motivo, ultima verifica |
| **F8** Assistente trattativa | 🟡 | Prezzo ideale/massimo ✅ (`profit/offers.py`); mancano sconto per il ROI obiettivo, profitto a più prezzi, messaggio pronto |
| **F9** Portfolio con 8 stati | 🟡 | `inventory`: in_stock/listed/sold/returned |
| **F9** Statistiche giorno/settimana/mese | 🟡 | Oggi solo mensili |
| **F9** Apprendimento con tuo OK | ⛔ | Calibrazione e filtro delle fonti si aggiornano **da soli ogni notte** (D13) |
| **F9** Correzioni tue su identificazione e condizioni | ❌ | |
| **F10** Test su parsing, dedup, stati, profitto, ranking, avvisi, sync | ✅ | Esistono (486 + 46 + 46); mancano i veti |
| **F10** Log strutturati, retry/backoff, job idempotenti | ✅ | `structlog`, id di job, `Retry` |
| **F10** Tracciamento costi AI · misure su casi reali · prompt injection | ❌ | |

---

## 6. Audit

### 6.1 Conflitti con i limiti non negoziabili

**Fonte dei Termini.** `vinted.com/terms-and-conditions`, "New version applicable from 08-10-2026", sez. 6 "You must not" — letto il 9/10/2026 con una lettura automatica della pagina: **verifica sul testo originale**. Estratti: *"use any kind of external software tools (including but not limited to: bots, scraping programs, crawling programs, spiders)"* … *"for the purpose of promoting Items, adding Items to favourite"* … *"unless such a use is authorised, offered or in any other way allowed by us"*; *"data mine, screen scrape, crawl, disassemble, decompile or reverse engineer any part of the Site"*. La sez. 7 prevede rilevamento e blocco anche automatici. Un commento di terzi (Redrip) data l'entrata in vigore al 5/10: il testo ufficiale dice 8/10. `robots.txt` di vinted.it: vietati `/checkout`, `/member/`, `/inbox`, `/util`, `/apipie/`; `Allow: /`; esclusi i crawler IA (GPTBot, ClaudeBot…) e `ai-train=no`.

| ID | Cosa | Riferimento | Esito |
|---|---|---|---|
| L1 | Scanner automatico di ricerche | `extension/src/scan.js`, `background.js: scanStep/scanTick` | Q1a |
| L2 | Lettura automatica dei migliori candidati | `background.js: planAutoDeep`, `content.js: readPage` | Q1b |
| L3 | Aggiornamento in background dei tracciati | `background.js: pollRefreshQueue`, `GET /capture/refresh-queue` | Q1c |
| L4 | Lettura dal server | `acquisition/public_fetch.py`, job `refresh_tracked_public` | Q1d |
| L5 | Analisi approfondita = fetch di pagina non aperta | `content.js: readPage` | Q1e |
| L6 | Preferiti con clic programmatico | `content.js` (`.click()` sul cuore), `app-bridge.js` | Q1f |
| L7 | Acquista con clic programmatico | `content.js` (`btn.click()`), `background.js: ff:vinted-buy` | Q1g |
| L8 | Misure automatiche su Vinted reale | `extension/e2e/real-speed.e2e.cjs` | Q1h |
| L9 | Dati del venditore oltre i consentiti | `db/models/seller.py` (5 colonne mai usate), `api/v1/listings.py: seller_for` (accetta `seller_username`), `extension/src/parse.js: sellerKey` (SHA-256 senza segreto, 96 bit) | Q2 |
| L10 | Foto scaricate dal server dal CDN e URL Vinted passati ad Anthropic | `media/archive.py`, `vision/analyzer.py` | Q3 |
| L11 | Permessi più larghi del minimo | `manifest.json`: host opzionali `http://*/*` e `https://*/*`, `scripting`, `offscreen`, `notifications` | C5 |
| L12 | **Rischio residuo che non posso eliminare** | L'estensione è comunque uno "strumento software esterno" che legge le pagine mentre le usi; i Termini lo vietano salvo autorizzazione e i blocchi possono essere automatici. La lettura passiva è la modalità con meno impatto (nessuna richiesta aggiuntiva) ma il rischio non è zero | Premessa del brief; lo dichiaro, non lo "risolvo" |

**Cosa è già conforme** (verificato nel codice): nessun cookie/token Vinted letto o inviato (`credentials: "omit"`); chiave dell'estensione revocabile con solo hash salvato; l'estensione gira solo sui domini `www.vinted.*`; le azioni su Vinted partono solo da un gesto reale; nessun CAPTCHA o anti-bot aggirato (al primo 403/429 tutto si ferma); nessun endpoint privato, account falso o proxy.

### 6.2 Principi sui dati non rispettati

| ID | Problema | Fase |
|---|---|---|
| D1 | **Prezzo richiesto ≠ prezzo di vendita.** `sold_sales.price_kind` distingue già `last_seen` da `paid/received` (bene), ma `model_price_stats` li fonde in **una sola mediana pesata**, e il codice chiama "realized" l'ultimo prezzo visto (`acquisition/market_cache.py`, `analytics/backtest.py`). Mitigazione esistente: sconto da trattativa misurato sui tuoi acquisti (≥ 3), altrimenti non applicato | 2, 4 |
| D2 | **Provenienza per valore** (origine, tipo osservato/dichiarato/dedotto, confidenza): c'è solo nel blocco identificazione (`identification.Attribute`) e nelle prove foto; non per prezzo, spedizione, condizione… | 2 |
| D3 | **"Non visibile" ≠ "assente":** nessuno stato "assente dalle foto pertinenti"; `has_label_photo` è un booleano | 5 |
| D4 | **"Probabilmente autentico"** (`authenticity/assess.py`, fino a 97 %) contraddice "mai autentico sulle sole foto" | 5 |
| D5 | **Valori inventati o non calibrati presentati come parametri:** imballaggio 0,50 €, spedizione in 3,49 €, protezione 0,70 € + 5 %, rapporto richiesto/venduto 0,88, moltiplicatori di condizione (1,15 / 1,08 / 1,00 / 0,88 / 0,70 / 0,95), curve del Flip Score (profitto pieno a 25 €, ROI a 90 %, sconto a 50 %), probabilità di contraffazione per marca, rapporti di verosimiglianza dell'autenticità. Sono ipotesi, mai tarate su dati reali | 4, 6 |
| D6 | **`published_at` mancante → istante di osservazione** (`ingestion/service.py: "published_at": pl.published_at or now`); da lì età, giorni per vendere e "primo prezzo", datato alla pubblicazione presunta (`observed_at: row["published_at"]`). Un dato dedotto presentato come osservato | 2 |
| D7 | **Storia delle foto persa:** a ogni cattura più ricca il set viene cancellato e reinserito (`replace_images`): si perdono ordine storico, date e legame con il file archiviato | 2 |
| D8 | **Analisi non versionate:** `opportunities` sovrascritta, `market_comparables` cancellati e riscritti, blocco visivo dentro `listings.identification`. Una decisione passata non è ricostruibile | 2 |
| D9 | **Nessun "da verificare":** un annuncio non rivisto da giorni resta "attivo" e acquistabile in feed e avvisi | 2 |
| D10 | **Modello presunto dalla marca:** senza modello noto, `product_key` = marca\|categoria\|-\|genere e i comparabili superano la soglia 0,5 anche con modello diverso (categoria + condizione + taglia + genere bastano). Il verdetto rapido usa medie marca × categoria quando manca il livello modello (ora dichiarato `brand_category`) | 4 |
| D11 | **Taglie:** UK/US→EU per le scarpe con scarti fissi (UK+34, US+33,5), tabella IT→lettera, lettere uguali fra marche, nessun indicatore di affidabilità | 4 |
| D12 | **"Live" e tempo reale:** l'etichetta "Live · scanned…" vale solo con un feed; molti pannelli si aggiornano ogni 30 s e non mostrano ovunque data/ora dell'ultima verifica | 8 |
| D13 | **Parametri di produzione che cambiano da soli:** ogni notte `fit_price_calibration_task` riadatta le stime, il "gate" delle fonti si accende/spegne dal backtest e si rianalizzano tutte le opportunità attive. Il brief (Fase 9) vieta modifiche senza il tuo OK | 9 |

### 6.3 Fragilità tecniche

| ID | Problema | Fase |
|---|---|---|
| T1 | **Logica duplicata JS/Python:** `quick.js` replica le formule del server; l'allineamento è verificato con valori fissi, non con vettori di test condivisi → rischio di divergenza quando cambiano i verdetti | 6, 7 |
| T2 | **Nessuna protezione da prompt injection, né test** (cercato in tutto il repo). Il titolo entra nel prompt (`ai/claude_analyst.py` come JSON, `vision/analyzer.py` come riga "Titolo: …") senza delimitazione né istruzione "è un dato". Mitigazioni di fatto: risposta vincolata a schema JSON, guardrail sul verdetto, nessuno strumento dato al modello | 5 |
| T3 | **Spesa AI non limitata:** con la chiave, visione automatica su ogni annuncio con Flip ≥ 60 o profitto > 0 e analista LLM automatico con Flip ≥ 80 (`workers/tasks.py`, `workers/vision_queue.py`); modello predefinito `claude-opus-5-5`, fino a 20 foto per chiamata, `max_tokens` 16.000; nessun tetto, nessuna cache per hash, nessun registro dei costi (solo log dei token). Oggi la chiave non c'è: costo 0 | 5 |
| T4 | **Foto passate come URL del CDN:** se il link scade (articolo venduto) l'analisi fallisce; le copie locali non sono usate; impossibile cachare per hash | 5 |
| T5 | **Ricalcolo giornaliero di tutte le opportunità attive** e una riga in `opportunity_scores` a ogni analisi: crescita illimitata e CPU sprecata quando nulla è cambiato | 2 |
| T6 | `batch-import.test.ts` legge file dell'estensione fuori da `frontend/`: ok nel monorepo, fragile se si isola il frontend | basso |
| T7 | **Residui:** riferimenti a `/api/v1/demo/images` in `core/middleware.py` (modalità demo rimossa); `listing_price_history` duplica gli snapshot; `market_statistics` (vecchia) e `model_price_stats` (nuova) coesistono; colonne venditore inutilizzate. Pulizia mirata dentro le fasi, non una fase a parte | 2–8 |
| T8 | **Interfaccia:** lingua mista; due pagine di dettaglio per lo stesso articolo (`/deals/[id]` e `/items/[ref]`) | 8 |
| T9 | **Sicurezza di base buona:** Argon2id, JWT in cookie httpOnly + CSRF, rate limit a finestra scorrevole, header di sicurezza, download immagini a prova di SSRF, registrazione chiusa dopo il primo account, segreti solo da variabili d'ambiente. Da rifare in Fase 10: scansione segreti, dipendenze, prompt injection | 10 |
| T10 | **Instabilità del ponte pagina↔estensione:** negli ultimi giorni diversi commit di correzione (`Duplicate script ID`, errore su nodi con un campo `id`, estensione aggiornata sotto una scheda aperta). Se Q1 rimuove il ponte delle azioni, la superficie si riduce | 3 |

### 6.4 Numeri e affermazioni che non vanno dati per dimostrati

| ID | Affermazione | Realtà |
|---|---|---|
| N1 | "Errore medio 18,13 → 17,66 €", "10,33 → 8,93 €", "MAPE 17,2 → 13,9 %", "vendite dentro min–max 44 → 79 %" (`PROGRESS.md`) | **Mercato simulato / benchmark sintetico.** Nel DB di quella sessione c'era 1 sola vendita reale. Non sono prove di accuratezza |
| N2 | Set di prova dell'autenticità (16 + 16, 0 falsi negativi) | Sono **scenari**, non foto: misura le regole di decisione, non la lettura delle foto |
| N3 | "Migliore evidenziata in 1,13 s" | Misurato su pagine vinted.it reali ma con mercato sintetico nel DB e Chromium automatico; il primo byte di Vinted è ~0,93 s. Ordine di grandezza utile, non una prova per te |
| N4 | Selettori di cuore, "Acquista", pagina di pagamento | Costruiti sulla struttura nota, **mai verificati su Vinted reale** (lo dice `PROGRESS.md`) |
| N5 | Formato delle risposte di Serper | Costruito da documentazione, non da una risposta reale: serve un `--dry-run` alla prima chiave |
| N6 | Parser | Verificato su pagine reali di annuncio in vendita e venduto e su card dell'armadio; **non** su Preferiti né su "riservato" (Q4) |
| N7 | Tassi di cambio | Tabella fissa BCE del 7/10/2026 (`external/fx.py`) |

**Pulsanti decorativi:** nelle parti lette non ne ho trovati, ma non posso escluderlo. In Fase 8 aggiungo un controllo per pagina: ogni controllo è collegato a un endpoint o a un'azione verificata.

---

## 7. Cosa riuso e cosa ritiro

**Riuso (non lo riscrivo):** stack e infrastruttura (Docker, backup, deploy) · ingestione e dedup · `listing_snapshots` · macchina a stati · `sold_sales` e `model_price_stats` (il tipo di prezzo è già distinto) · statistica robusta e outlier · identificazione (`Attribute` diventa il modello di provenienza) · `profit/calculator.py` e `offers.py` (si estendono) · pre-elaborazione foto (`phash`, qualità, SSRF) · avvisi e dedup per evento · parser condiviso `vinted_parser.json` · coda e badge dell'estensione · Side Panel · kit UI e accessibilità AA · le suite di test.

**Ritiro o sostituzione (secondo Q1–Q3):** L1–L8 (come risponderai a Q1) · il verdetto `probably_authentic` · `Verdict` BUY/CONSIDER/SKIP → nuovo insieme · `opportunities` come unica fonte di verità → puntatore all'ultima analisi · `listing_price_history` (duplicato) · calibrazione e gate automatici → proposte da approvare · colonne del venditore inutilizzate · `PROGRESS.md` → archivio.

---

## 8. Piano per fase

Regole trasversali per tutte le fasi: migrazioni **additive e reversibili**, nessuna perdita di dati, backup prima di ogni migrazione · funzioni nuove dietro interruttore finché non verificate · logica pura testata per prima · **ogni numero nuovo porta origine, tipo e confidenza** · mai un punteggio senza spiegazione · "evidenza insufficiente" è un risultato di prima classe · il backend **non contatta mai** `vinted.*` (test che lo garantisce se Q3 = B) · ogni servizio a pagamento passa da un registro dei costi · un commit per blocco funzionante.

### Fase 2 — Dati, tracciabilità e stato di vendita · dimensione **L** · dopo Q1–Q3

**Obiettivo.** Un record per ogni cosa letta o analizzata, sempre riconducibile all'annuncio, con storia a sola aggiunta.

**Blocchi (un commit ciascuno):**
- **2.1 Vincoli e dati dedotti.** `url` e date non vuoti (CHECK + NOT NULL dove manca); `published_at` resta nullo se ignoto, con `published_at_kind`; "primo prezzo osservato" datato all'osservazione vera (D6).
- **2.2 Osservazioni.** Estendo `listing_snapshots` (non creo una tabella nuova): `parser_version`, `extension_version`, `payload` con i campi letti e il loro tipo (osservato / dichiarato dal venditore / dedotto), `image_set` ordinato. Lo stato dell'annuncio (`last_seen_at`, `last_verified_at`) si aggiorna a **ogni** passaggio dell'estensione; una *nuova riga* di osservazione si aggiunge solo se cambia qualcosa o dopo 6 h ("battito"), per non far crescere la tabella a ogni scorrimento. `listing_price_history` passa a vista derivata.
- **2.3 Immagini.** `image_key`, `first_seen_at`, `last_seen_at`, `removed_at`, `source`; niente più cancella-e-reinserisci; SHA-256 e `dHash` per ogni file; prova del riannuncio salvata (quale regola ha scattato).
- **2.4 "Da verificare".** `last_verified_at`, soglie C1 in Impostazioni, job che marca lo stato, funzione pura testata per tutte le transizioni; feed, avvisi, estensione e UI non mostrano mai come acquistabile un annuncio da verificare.
- **2.5 Venditore.** HMAC lato server con segreto, migrazione sul posto, colonne inutili rimosse, `seller_username` eliminato (Q2).
- **2.6 Analisi immutabili a blocchi.** Tabella `analyses`: `listing_id`, `vinted_id`, `url`, `source`, `created_at`, `schema_version`, `algorithm_version`, parametri usati, `trigger` (nuovo annuncio / cambio prezzo / foto aggiunte / manuale / nuova versione), `input_hash`, e i blocchi **prodotto · visiva · economica · mercato · decisione**, ciascuno con la propria versione. `opportunities` resta come "ultima analisi" per le query del feed (con `analysis_id`), così non riscrivo decine di query. Comparabili compattati nel blocco mercato.
- **2.7 Tabella dei prezzi venduti.** Rifinisco `sold_sales`/`model_price_stats`: colonne **separate** per "ultimo prezzo richiesto visto" e "pagato/incassato", finestra di incertezza della data di vendita (ultimo visto attivo → rilevazione), vista in Impostazioni e CSV.
- **2.8 Esportazioni CSV.** Osservazioni, analisi, prezzi venduti (accanto a quella degli articoli).
- **2.9 Migrazione e prove.** Migrazione eseguita su una copia: conteggi prima/dopo, 0 violazioni dei vincoli.

**Accettazione (dal brief) e come la provo:** rianalizzare lo stesso articolo non crea duplicati (stessa cattura due volte → 1 annuncio, 1 analisi, osservazioni solo se cambia qualcosa) · un cambio di prezzo genera una nuova riga di storico e un ricalcolo · un articolo venduto compare nella tabella dei prezzi venduti · nessun record senza URL e data (vincoli del DB + controllo in migrazione).

**Verifica manuale tua.** (1) Migrazione su una copia del tuo DB con lo script che ti do e confronto dei conteggi; (2) apri un articolo tracciato: storico prezzi con date di osservazione, foto in ordine, stato; (3) porta la soglia "da verificare" a 1 minuto e guarda lo stato cambiare; (4) scarica i CSV; (5) cambia il prezzo di un annuncio di prova e guarda la nuova analisi.

### Fase 3 — Estensione · dimensione **M** · dopo Q1, Q3, Q4

**Obiettivo.** Cattura completa e sincronizzazione affidabile, solo con le pagine che apri e scorri tu.

- **3.1 Allineamento ai limiti** secondo Q1: rimozione o sostituzione di L1–L7, dei permessi non più necessari e dei relativi test; tutte le funzioni facoltative restano spente e visibili una per una in Impostazioni.
- **3.2 Azioni a un clic compatibili:** *Traccia*, *Analisi* (sui dati catturati), *Apri nel tracking*, *Apri su Vinted*.
- **3.3 Cattura:** annuncio (tutti i campi e foto), ricerca/catalogo, armadio, **Preferiti con aggiornamento di stato in blocco** (e "da verificare" per ciò che non compare più). Richiede le pagine di Q4.
- **3.4 Foto dal browser** (se Q3 = B): coda con limiti, ordine originale, solo il contenitore della galleria, mai avatar o elementi dell'interfaccia.
- **3.5 Sincronizzazione:** stato sempre visibile, indicatore per voce in coda, **"Ricollega" con un clic** (abbinamento dalla web app senza incollare la chiave).
- **3.6 Manifest:** ogni permesso motivato in una riga nel README.
- **Accettazione:** pagina con 96 card letta senza scatti (fixture, nessun task lungo) · rete staccata → i dati restano in coda e partono al ritorno · un cambio di selettore si corregge in un solo file · **test che l'estensione non faccia richieste verso `vinted.*` oltre a quelle della pagina**.
- **Verifica manuale tua.** Carichi l'estensione; scorri una ricerca; stacchi la rete e la riattivi; apri DevTools → Network e controlli che non partano richieste che non hai causato.

### Fase 4 — Valore di mercato e profitto · **M–L** · dopo Q6 e Q7

- **4.1 Costi v2** (`profit/`): ogni costo ha stato *confermato / stimato / sconosciuto* e fonte; aggiungo **ripristino**, **margine sulla vendita**, **prezzo di pareggio**; scenari conservativo/base/ottimistico con costi sconosciuti dichiarati. Test d'accettazione: prezzo 20 + spedizione 4 + protezione 2 = **26**; rivendita 45 − costi 1 = **44**; profitto **18**; ROI **69,23 %**.
- **4.2 Valore di mercato:** il modello noto diventa un criterio di ammissione (non un peso del 20 %); senza modello noto confronto solo con annunci senza modello e confidenza limitata; **base dichiarata** ("N prezzi reali + M ultimi prezzi visti"); tre prezzi (rapido, mercato, ottimistico) ciascuno con intervallo e confidenza; **"valutazione non verificata"** senza comparabili affidabili. I parametri di D5 diventano "ipotesi" etichettate, con la loro origine e una penalità di confidenza quando sostituiscono dati.
- **4.3 Taglie:** sistema riconosciuto (EU/UK/US/numerica/marca) e flag di affidabilità; conversioni solo con tabelle ufficiali per marca, con fonte nel codice; senza tabella, confronto solo nello stesso sistema. La taglia incide su valore e liquidità solo dove i dati la sostengono.
- **4.4 Titoli:** sinonimi, abbreviazioni, errori; il modello **non** si assegna per sola somiglianza (servono due segnali). Report delle marche/modelli non riconosciuti più frequenti da approvare per ampliare la tassonomia (oggi 28 marchi).
- **Accettazione:** il caso numerico sopra · casi **E** (15 € → rivendita 23 €, costi che assorbono il margine) e **G** (raro, nessun comparabile: nessun prezzo inventato).

### Fase 5 — Immagini, condizioni ed etichette · **L** · dopo Q8

- **5.1 Costi e cache prima di tutto:** registro `ai_usage` (modello, token, costo, annuncio), **tetto giornaliero** con arresto visibile, cache per hash di ogni immagine, `visual_analyses` (blocco "visiva" della Fase 2) riusata finché il set di foto non cambia; con foto aggiunte analizzo solo le nuove.
- **5.2 Trasporto:** ridimensionamento a ~1280 px e invio dei byte della copia locale (mai URL di Vinted).
- **5.3 Risultato strutturato:** identificazione (marca, categoria, modello, linea, colore, materiali, codici) come fatti con tipo e confidenza; difetti per tipo di articolo con **foto e riquadro**; classe di condizione con punteggio 0–100, parti non osservabili e contraddizioni con l'annuncio; etichette con i **5 stati**; testo delle etichette letto e confrontato **in codice** con titolo e descrizione (taglia, composizione).
- **5.4 Rischio contraffazione** in 4 classi (nessuna anomalia evidente · verifica consigliata · rischio elevato · prove insufficienti); **mai "autentico"**; rischio elevato blocca la raccomandazione.
- **5.5 Due punteggi indipendenti** (qualità foto, copertura ispezione) e **elenco foto da chiedere**.
- **5.6 Prompt injection:** testo degli annunci come dato (inserito in un blocco delimitato con caratteri di controllo e delimitatori neutralizzati, mai nel prompt di sistema, con istruzione esplicita di non eseguire né il testo dell'annuncio né quello scritto nelle foto) · risposta solo a schema · nessuno strumento al modello · i campi numerici decisionali non vengono mai dal modello · un testo che si rivolge a un assistente diventa esso stesso un segnale di rischio · **suite di test avversari** (titolo, descrizione, testo nell'immagine).
- **5.7 Pilota** su ~30 annunci tuoi prima di attivare il resto (costo in §9).
- **Accettazione:** foro in una foto + "ottime condizioni" nel titolo → incongruenza · nessuna foto delle etichette → "non visibile", mai "assente" né "autentico" · la stessa foto non viene analizzata due volte (contatore di chiamate).

### Fase 6 — Motore decisionale e Flip Score · **L** · dopo Q10

- **6.1 Modulo `decision`** puro: verdetti STRONG BUY / BUY / NEGOTIATE (con prezzo soglia) / WATCHLIST / PASS e stato prioritario **EVIDENZA INSUFFICIENTE**; quattro punteggi separati (Flip, Confidenza, Rischio, **Completezza dei dati**) con motivi, avvisi e informazioni mancanti.
- **6.2 Flip Score** con i pesi del brief (profitto 25, ROI 15, domanda e liquidità 15, prezzo rispetto al mercato 15, condizioni 10, rischio 10, qualità delle informazioni 5, tempo di vendita 5), configurabili. Per **non contare due volte** profitto, ROI e prezzo-vs-mercato (derivano dalla stessa coppia prezzo/valore) il contributo combinato dei tre è limitato e la formula è provata con test di monotonia. Ti mostro la formula prima di fissarla.
- **6.3 Veti** non compensabili da un ROI alto: contraffazione elevata, dati insufficienti, valutazione non verificata, articolo "da verificare". STRONG BUY solo con modello, costo totale e comparabili verificati.
- **6.4 Sette modalità:** Brand Hunter, Hidden Gems (titolo generico, categoria sbagliata, modello riconoscibile solo dalle foto — dipende dalla Fase 5), High Margin, High ROI, Quick Flip, Low Budget, Low Risk (riuso i preset attuali dove coincidono).
- **6.5 Capitale:** budget totale, massimo per articolo, profitto e ROI minimi, rischio massimo, numero massimo di articoli; il sistema sceglie la **combinazione migliore entro il budget** (zaino esatto sul profitto corretto per il rischio), non dieci acquisti quando ne puoi fare due.
- **6.6 Vettori di test condivisi JS/Python** per eliminare T1.
- **Accettazione:** casi **A–H** (appendice A). Il caso **H** (B con profitto 15 €, bassa incertezza, domanda provata deve stare sopra A con 20 €, alta incertezza, bassa domanda) è un test esplicito.

### Fase 7 — Finestra attiva mentre scorri · **M**

Il Side Panel di Chrome è già la soluzione scelta: non copre la griglia di Vinted, resta aperto navigando, è isolato dal CSS e dal JavaScript della pagina. Un riquadro flottante dovrebbe iniettare e mantenere elementi nella pagina di Vinted, con più rischio di conflitti e di tracce.
- Collego il pannello al nuovo motore (verdetti, 4 punteggi, "dati insufficienti" al posto del punteggio) con gli stessi vettori della Fase 6.
- Mostra sempre data/ora dell'ultima verifica; "visto" per tutto ciò che valuta.
- **Accettazione:** scorrendo 200 card la pagina resta fluida (fixture da 200, misura dei task lunghi) · tutto ciò che il pannello valuta è nei dati come "visto" · spegnendolo non lascia tracce (confronto del DOM prima/dopo).

### Fase 8 — Dashboard, scheda prodotto e avvisi · **L** · dopo Q5

- **Dashboard** veloce e mobile-first: migliori opportunità, Strong Buy, maggior profitto, miglior ROI, minor rischio, prezzi scesi di recente, capitale disponibile e investito, profitto potenziale **distinto** dal realizzato, precisione delle raccomandazioni ("dati insufficienti" finché non ci sono vendite).
- **Scheda prodotto unica** (unisco `/deals/[id]` e `/items/[ref]`): galleria completa (miniature, schermo intero, tastiera, swipe, segnaposto), **difetti sulla foto corrispondente**, identità, condizioni, etichette, mercato con comparabili e provenienza, calcolatore modificabile, verdetto, rischi, "perché questo affare", grafico di prezzo e preferiti, stato e ultima verifica, link all'annuncio. "Aggiorna ora" diventa **"Ricalcola"** (sui dati che ho) e **"Apri su Vinted per verificare"**: non posso né devo leggere Vinted da solo.
- **Avvisi** solo utili (nuovo Strong Buy, profitto o ROI sopra soglia, calo di prezzo significativo, articolo diventato conveniente, cambio di rischio), ciascuno con foto, costi, profitto, punteggi, motivo, link e ultima verifica; nessuna ripetizione per lo stesso evento.
- **Assistente trattativa:** prezzo ideale e massimo, sconto necessario per il ROI obiettivo, profitto a più prezzi, messaggio educato pronto da copiare (lo invii tu).
- Operazioni lunghe con stato reale e risultati parziali.
- **Accettazione:** ogni numero in pagina arriva dal database (test di coerenza UI ↔ API) · la scheda di un articolo venduto mostra ancora tutte le foto · tutto usabile da smartphone (test su viewport mobile).

### Fase 9 — Portfolio e apprendimento · **M–L**

- **9.1 Portfolio:** 8 stati (identificato, acquistato, in arrivo, da pubblicare, pubblicato, venduto, restituito, invenduto) con transizioni datate a sola aggiunta; costi reali, date, prezzo richiesto, ribassi, prezzo di vendita, profitto realizzato, giorni per vendere.
- **9.2 Statistiche** per giorno, settimana e mese; ROI medio, quota venduti, tempo medio, capitale immobilizzato, migliori marchi e categorie.
- **9.3 Apprendimento:** scarto previsto/reale su prezzo e tempo per marca, categoria e fascia di prezzo; analisi dei falsi positivi; **tue correzioni** su identificazione e condizioni (valgono subito, con tipo "utente").
- **9.4 Proposte, non modifiche:** nuove tarature di pesi e stime con confronto prima/dopo sullo storico; **nulla in produzione senza il tuo OK**. Calibrazione e gate notturni (D13) diventano proposte.
- **Accettazione:** una vendita registrata aggiorna statistiche, tabella dei prezzi venduti e misura dell'errore di previsione.

### Fase 10 — Verifica finale · **M**

- Test automatici su parsing, dedup, transizioni di stato, profitto, ranking, **veti**, avvisi, sincronizzazione dell'estensione; job idempotenti, retry con backoff, timeout, errori dei fornitori, log strutturati, **costi AI tracciati**.
- **Misure reali** su articoli verificati da te (Q9): accuratezza di marca e modello, errore sulle stime di rivendita con intervalli di confidenza, quota di avvisi inutili. Riporto i numeri misurati **con la numerosità del campione**; se è piccola lo scrivo.
- **Sicurezza:** scansione segreti, dipendenze, permessi minimi, prompt injection verificata con i test avversari.
- README e istruzioni operative aggiornati (tolte le misure simulate); relazione finale: completato / parziale / non realizzabile e perché.

---

## 9. Costi previsti

**Regola 9.** Nulla di quanto segue viene attivato senza la tua scelta.

### 9.1 API Anthropic (nuovo, Fase 5)

Prezzi da `platform.claude.com/docs/en/about-claude/pricing`, letti il 9/10/2026 (USD per milione di token). Sconto Batch −50 % (asincrono: non adatto al clic interattivo).

| Modello | ID | Input | Output | Lettura da cache | Note |
|---|---|---|---|---|---|
| Haiku 5.5 | `claude-haiku-5-5` | $0,10 | $0,50 | $0,01 | $0,50 / $2,50 oltre 100.000 token di prompt |
| Sonnet 5.5 | `claude-sonnet-5-5` | $2 | $10 | $0,10 | |
| Opus 5.5 | `claude-opus-5-5` | $4 | $20 | $0,20 | ragionamento sempre attivo |
| Fable 5.1 | `claude-fable-5-1` | $10 | $50 | $0,25 | richiede conservazione dati a 30 giorni |

**Ipotesi di calcolo** (da verificare con `count_tokens` su foto reali): 8 foto per annuncio ridimensionate a ~1280 px ≈ 1.570 token ciascuna (circa un token ogni 28×28 pixel), più ~2.000 token di istruzioni e schema ⇒ **~14.600 token in ingresso**; **~4.000 token in uscita** compreso il ragionamento. A piena risoluzione (2576 px) i token per foto salgono fino a ~4.784 (≈ 3×).

| Modello | Per annuncio (1280 px) | 100 analisi/mese | 500/mese | 2.000/mese | Per annuncio (piena risoluzione) |
|---|---|---|---|---|---|
| Haiku 5.5 | **$0,0035** | $0,35 | $1,7 | $6,9 | $0,006 |
| Sonnet 5.5 | **$0,069** | $6,9 | $35 | $138 | $0,121 |
| Opus 5.5 | **$0,138** | $13,8 | $69 | $277 | $0,241 |
| Fable 5.1 | **$0,346** | $34,6 | $173 | $692 | $0,603 |

(1 USD ≈ 0,89 € con il cambio BCE del 7/10/2026 usato in `external/fx.py`.)

**Come tengo la spesa bassa** (ordine di efficacia): pre-punteggio (solo i candidati) · cache per hash (una foto mai due volte; con foto aggiunte, solo le nuove) · ridimensionamento a 1280 px · **cascata** Haiku → Sonnet solo per i candidati promettenti (es. 500 analisi/mese con il 25 % inoltrato a Sonnet: ≈ **$10/mese**) · Batch per le rianalisi in background · tetto giornaliero. La cache dei prompt serve poco: la parte fissa (~2.000 token) è piccola rispetto alle foto, che cambiano a ogni annuncio.

**Pilota di ~30 annunci:** ≈ $0,10 (Haiku 5.5) · $2,1 (Sonnet 5.5) · $4,2 (Opus 5.5). **Tetto proposto: $1 al giorno.**

**Da chiarire con te (Q8):** le foto lasciano il tuo server verso Anthropic; il commento testuale dell'analista LLM oggi parte in automatico con Flip ≥ 80 e propongo di renderlo **solo su tuo clic**.

### 9.2 Servizi già presenti

| Servizio | Stato | Costo |
|---|---|---|
| Serper (prezzi esterni) | Spento finché non imposti la chiave (Q7) | 2.500 query gratis; poi pacchetto Starter $50 per 50.000 query, validi 6 mesi (~$8,3/mese equivalenti). Tetto di default 900 query/mese. Fonte: `docs/EXTERNAL_PRICES.md`, verificata il 7/10/2026 (non riverificata oggi) |
| Hosting | Il tuo PC, oppure Oracle Cloud Always Free + DuckDNS (`deploy/install.sh`) | €0 (carta richiesta solo per verifica) |
| SMTP / Telegram / Discord / Web Push / IMAP | Facoltativi | Quelli dei tuoi account |

### 9.3 Spazio su disco

Stima prudenziale per le copie locali delle foto (Q3): ~2,4 MB per articolo analizzato ⇒ ~2,4 GB al mese ogni 1.000 articoli analizzati; più le copertine dei "visti". **Numeri da misurare** sui primi dati reali (Fase 2) e da confrontare con lo spazio del tuo server.

### 9.4 Cosa non userò

Nessun servizio di scraping o proxy, nessuna API non ufficiale di Vinted, nessun altro servizio a pagamento (anche se disponibile in sessione, per esempio Firecrawl) senza una tua scelta esplicita.

---

## 10. Rischi

| # | Rischio | Prob. | Impatto | Mitigazione |
|---|---|---|---|---|
| R1 | **Blocco o sospensione dell'account Vinted** (Termini dall'8/10/2026; rilevamento anche automatico) | Media | Alto | Solo lettura passiva; Q1; nessun clic programmatico; nessuna richiesta aggiuntiva; arresto al primo rifiuto. Il rischio residuo (L12) è tuo da accettare |
| R2 | **Dati insufficienti** (uso personale ⇒ poche vendite osservate) ⇒ molti "evidenza insufficiente" | Alta | Medio | È il comportamento voluto; prezzi esterni (opzionali); le tue vendite pesano ×5; l'aspettativa va gestita |
| R3 | **"Ultimo prezzo visto" scambiato per prezzo reale** | Media | Alto | Tipo di prezzo sempre visibile; colonne separate; sconto da trattativa misurato sui tuoi acquisti |
| R4 | **Vinted cambia la pagina** e i selettori smettono di funzionare | Alta | Medio | Parser in un file aggiornabile; fixture reali; l'estensione dichiara "parser da aggiornare" quando mancano campi; mai punteggi su dati mancanti |
| R5 | **Spesa AI fuori controllo** | Media | Medio | Tetto giornaliero, registro costi, pre-punteggio, cache; niente chiave prima della Fase 5 |
| R6 | **Prompt injection** da titoli, descrizioni o testo nelle foto | Media | Alto | §8 Fase 5.6; i numeri decisionali non vengono mai dal modello; test avversari |
| R7 | **Allucinazioni di difetti o etichette** | Media | Alto | Ogni difetto con confidenza e foto; "non visibile ≠ assente"; mai "autentico"; pilota con tue foto prima di fidarsi |
| R8 | **Regressioni** in una base di codice estesa | Media | Medio | Migrazioni additive, interruttori, 578 test come rete, un commit per blocco |
| R9 | **Divergenza JS/Python** nelle valutazioni | Media | Medio | Vettori di test condivisi (6.6) |
| R10 | **Privacy:** foto che contengono persone, chiave pseudonima del venditore, e-mail IMAP | Bassa | Medio | Q2, Q3, Q8; ignorare le persone; segreti solo in variabili d'ambiente |
| R11 | **Dipendenza da un'API di ricerca di terzi** (Serper ottiene risultati Google, non è un'API di Google) | Bassa | Basso | Opzionale e spenta; il resto funziona senza |
| R12 | **Non posso verificare nulla su Vinted reale** da qui (e non devo) | Certa | Medio | Fixture tue (Q4), misure tue (Q9), nessuna affermazione su ciò che non ho potuto provare |

Probabilità e impatto sono mie stime qualitative, non misure.

---

## 11. Checklist, decisioni e punti aperti

### 11.1 Checklist delle fasi

- [x] **Fase 1** — Audit e piano (2026-10-09)
- [x] **Fase 2** — Dati, tracciabilità e stato di vendita *(completata il 2026-10-09; in attesa del tuo OK per la Fase 3, vedi §11.6)*
- [ ] **Fase 3** — Estensione
- [ ] **Fase 4** — Valore di mercato e profitto
- [ ] **Fase 5** — Immagini, condizioni ed etichette
- [ ] **Fase 6** — Motore decisionale e Flip Score
- [ ] **Fase 7** — Finestra attiva
- [ ] **Fase 8** — Dashboard, scheda prodotto e avvisi
- [ ] **Fase 9** — Portfolio e apprendimento
- [ ] **Fase 10** — Verifica finale

Dipendenze: 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10. Hidden Gems (6) usa la Fase 5; "precisione delle raccomandazioni" (8) ha dati solo dopo le prime vendite (9).

### 11.2 Decisioni prese

| Data | Decisione | Origine |
|---|---|---|
| 2026-10-09 | Stack invariato | Regola 3 |
| 2026-10-09 | Nessun file di codice toccato in Fase 1 | Regola della fase |
| 2026-10-09 | Le misure su mercato simulato non vengono più citate come accuratezza | Principi sui dati |

| 2026-10-09 | Branch di lavoro: `claude/sweet-curie-9xyxzg` | Tua risposta |
| 2026-10-09 | **Q1 come consigliato.** Rimuovere: scanner automatico (a), lettura automatica candidati (b), aggiornamento in background dei tracciati (c), lettura dal server (d), misure automatiche su Vinted reale (h). Sostituire con "Apri su Vinted": analisi approfondita (e), preferiti (f), acquista (g). Esecuzione: **Fase 3** (estensione); in Fase 2 nessuna logica nuova dipende da (a)–(d). | Tua risposta |
| 2026-10-09 | **Q2 come consigliato.** Si tiene la chiave pseudonima del venditore, protetta da HMAC con segreto del server (migrazione sul posto); si eliminano le colonne inutilizzate del venditore e il campo `seller_username`. Blocco 2.5. | Tua risposta |
| 2026-10-09 | **Q3 come consigliato.** Foto caricate dall'estensione (B), tutte per gli analizzati e solo la copertina per i visti; conservate per sempre se tracciati/acquistati, 90 giorni le altre. Il caricamento dall'estensione è Fase 3: in Fase 2 si prepara solo il modello dati (blocco 2.3) e il server **non** scarica più nuove foto da Vinted dopo la Fase 3. | Tua risposta |
| 2026-10-09 | Le skill citate (impeccable, apple-design, emil-design-eng, animate, dataviz, ui-ux-pro-max, ponytail, caveman) si useranno dalle fasi con interfaccia (7–8); `claude-api` dalla Fase 5. In Fase 2 non c'è interfaccia nuova. | Tua richiesta |

| 2026-10-09 | **C1 applicata** come conferma rapida: «da verificare» dopo 48 h (attivi) / 24 h (riservati), configurabili (`STALE_ACTIVE_HOURS`, `STALE_RESERVED_HOURS`) | Conferma rapida C1 |
| 2026-10-09 | Una riga di storico solo se cambia qualcosa o dopo 6 h («battito», `SNAPSHOT_HEARTBEAT_HOURS`); le visualizzazioni non contano come cambiamento | Scelta tecnica di Fase 2 (modificabile) |
| 2026-10-09 | Date di pubblicazione uguali al primo avvistamento (dedotte dal vecchio codice) azzerate in migrazione: l'istante resta in `first_seen_at`, nessuna informazione persa; `days_to_sell` calcolato su quelle date azzerato (restano i giorni osservati) | Principio «mai inventare», D6 |
| 2026-10-09 | `listing_price_history` diventa vista sugli snapshot (prima era una seconda copia); `lifecycle_stale_hours`, mai usato, rimosso | Regola 4 (niente duplicati) |
| 2026-10-09 | Etichette nuove dell'interfaccia in inglese come il resto della pagina, in attesa di Q5 | Coerenza con l'esistente |

*(Restano da dare Q4–Q10 e C2–C5: valgono le conferme rapide se non rispondi.)*

### 11.3 Punti aperti

Q1–Q10 e C1–C5 (§2) · formula del Flip Score da farti vedere prima di fissarla (6.2) · tabelle ufficiali delle taglie per marca da reperire con fonte (4.3) · quanto pesano davvero le foto sul disco (9.3) · verifica del formato reale di Serper alla prima chiave (N5).

### 11.4 Registro delle sessioni

| Data | Cosa |
|---|---|
| 2026-10-09 | Prompt v3, esecuzione di Q1 = blocchi 3.1 e 3.2 del piano: rimossi scanner, analisi approfondita di pagine non aperte, aggiornamento in background, lettura dal server e clic su Preferiti/Acquista; "Apri su Vinted" al loro posto; permessi ridotti a `storage`/`alarms`/`sidePanel`; estensione v1.2.0. e2e con l'estensione vera (live e velocità) verdi; guardiani sui sorgenti. Restano 3.3–3.6 (cattura armadio/Preferiti, foto dal browser Q3-B, "Ricollega") |
| 2026-10-09 | Prompt v3, Fase 3 (mercato): test del caso G su tutta la catena (oggetto raro: nessun prezzo, profitto, offerta, avviso né numero nel verdetto rapido dell'estensione) e base di misura dichiarata nel report di accuratezza (D1: errore contro ultimo prezzo richiesto, non prezzo incassato). 698 test backend |
| 2026-10-09 | Prompt v3, Fase 2 = blocco 4.1 del piano: `profit/evaluation.py` (stati confermato/stimato/sconosciuto, ripristino, riserva imprevisti, margine sulla vendita, prezzo di pareggio, capitale immobilizzato), `/profit/calculate` esteso, valutazione nel blocco economico delle analisi, calcolatore "What-if" aggiornato. Prova numerica 20+4+2=26 · 44 · 18 · 69,23 % e casi E/H verdi (693 test backend) |
| 2026-10-09 | Prompt v3 ("FlipFinder AI"): letto contro questo piano, nessuna decisione dell'utente riaperta. Mappa delle fasi v3 sulle fasi del piano in `ARCHITECTURE.md`; decisioni e limiti propri di v3 in `DECISIONS.md` e `LIMITATIONS.md`; aggiunto `marketplace/capabilities.py` (cosa fornisce ogni modalità di cattura, verificato contro il parser) |
| 2026-10-09 | Fase 2 (blocchi 2.1–2.9): provenienza delle date, osservazioni tipizzate, foto nel tempo, «da verificare», venditori minimi con chiave protetta, analisi immutabili a blocchi, tabella dei prezzi venduti, CSV, migrazioni 0010–0016 provate anche su 30.000 annunci |
| 2026-10-09 | v3 Fase 5 (= PIANO Fase 6.1–6.3, 6.5): modulo `decision` (6 verdetti, 4 punteggi separati, veti, STRONG BUY a requisiti, classifica, zaino esatto), Flip Score con i pesi del brief senza doppio conteggio, migrazione 0017; casi A, C, E, G, H e B (lato decisione) coperti da test. Restano 6.4 (sette modalità) e 6.6 (vettori JS/Python) |
| 2026-10-09 | v3 Fase 6 (orchestratore): budget AI con tetti e stop (`ai_usage`), interruttore sul fornitore, routing economico/forte, registro di 9 strumenti tipizzati, ciclo dell'agente con guardrail nel codice e traccia (`agent_runs`), `events` append-only, revisione dei candidati con ripiego a regole, ricalcolo incrementale (35→23), avvisi coalescenti e legati al verdetto; migrazione 0018. Mai provato col modello reale (L13) |
| 2026-10-08/09 | Fase 1. Lettura di backend, estensione, frontend, documentazione e test; esecuzione della suite (486 + 46 + 46); lettura dei Termini di Vinted e del listino Anthropic; scoperto e corretto il ritardo del checkout (72 commit); scrittura del piano |

### 11.5 Avanzamento Fase 2

- [x] 2.1 Vincoli e dati dedotti (migrazione 0010: `published_at_kind`, URL non vuoto, niente data di pubblicazione inventata; primo prezzo datato alla prima osservazione)
- [x] 2.2 Osservazioni (migrazione 0011: snapshot tipizzati con motivo, versioni di estensione e parser, serie foto; una riga solo se cambia qualcosa o dopo 6 h; `last_verified_at`; `listing_price_history` ora è una vista sugli snapshot, nessun prezzo perso)
- [x] 2.3 Immagini (migrazione 0012: chiave stabile, prima/ultima vista, `removed_at`, origine; niente cancella-e-reinserisci, copie e hash sopravvivono; SHA-256 e dHash alla copia; riannuncio visivo con prova in `duplicate_evidence`; il fingerprint delle foto entra negli input di rianalisi)
- [x] 2.4 "Da verificare" (migrazione 0013: stato `to_verify` con funzione pura testata e job ogni 15 min; soglie 48 h attivi / 24 h riservati in `STALE_ACTIVE_HOURS` / `STALE_RESERVED_HOURS`; `last_verified_at` esposto nelle API; mai `is_active`, torna a ciò che la pagina dice alla prima nuova osservazione; `lifecycle_stale_hours`, che non era usato, è stato rimosso)
- [x] 2.5 Venditore (migrazione 0014: chiavi `s2:` = HMAC con `SELLER_KEY_SECRET` (altrimenti derivato da `JWT_SECRET`), migrate sul posto; tolte le 5 colonne mai riempite, i campi relativi nei motori di rischio/autenticità e `seller_username`; punteggio di affidabilità invariato)
- [x] 2.6 Analisi immutabili a blocchi (migrazione 0015: tabella `analyses` con ID interno/Vinted, URL, fonte, data, versione schema e algoritmo, motivo, hash di input e risultato e i cinque blocchi; un trigger del database vieta le modifiche; `opportunities.analysis_id` punta all'ultima; rianalizzare con stessi input e risultati non crea righe; `GET /items/{ref}/analyses[/{id}]`)
- [x] 2.7 Tabella prezzi venduti (migrazione 0016: `asking_price` e `realized_price` separate con vincolo, finestra della data di vendita, giorni osservati; `GET /pricing/sold-prices[/export.csv]` con prezzi reali, richiesti e riportati mai fusi; sotto 3 vendite «insufficiente». **Non toccato**: `model_price_stats` fonde ancora i tipi di prezzo in una mediana: è la Fase 4 (D1))
- [x] 2.8 Esportazioni CSV (osservazioni `/items/export-observations.csv`, analisi `/items/export-analyses.csv`, prezzi venduti `/pricing/sold-prices/export.csv`, accanto a quella degli articoli; ogni riga ha ID interno, ID Vinted, URL; testi di terzi neutralizzati contro le formule; voci nel menu «Export CSV» di Archivio)
- [x] 2.9 Migrazione e prove (test su 120 annunci in tutti gli stati con conteggi prima/dopo; prova di scala su 30.000 annunci / 90.000 foto: 14 s in avanti, 3 s all'indietro; `record_level` visto/analizzato; `docs/DATI.md`)

### 11.6 Report di fine Fase 2

**Commit** (branch `claude/sweet-curie-9xyxzg`): 2.1 `21aea46` · 2.2 `cd72ac3` · 2.3 `4d973f8` · 2.4 `565a608` · 2.5 `2087e72` ·
2.6 `67e9a9c` · 2.7 `eabdf61` · 2.8 `4c48b7e` · 2.9 (questo commit).

**Criteri di accettazione della Fase 2**

| Criterio | Esito | Prova |
|---|---|---|
| Rianalizzare lo stesso articolo non crea duplicati | Superato | `tests/api/test_phase2_acceptance.py` (4 catture identiche: 1 annuncio, 1 analisi, 1 osservazione); `test_phase2_analyses.py` |
| Un cambio di prezzo genera una nuova riga di storico e un ricalcolo | Superato | stesso test: +1 osservazione (`reason = price`), +1 analisi (`trigger = price_change`), storico prezzi `[14, 11]` |
| Un articolo venduto compare nella tabella dei prezzi venduti | Superato | stesso test + `test_phase2_sold_prices.py` (prezzo richiesto separato dal reale, finestra e giorni) |
| Nessun record senza URL e data | Superato | vincoli CHECK in `listings` e `analyses`; controllo su tutte le tabelle nel test di accettazione e nella migrazione |
| Migrazione dei dati esistenti senza perdite | Superato | `test_phase2_migration.py` (conteggi prima/dopo, andata e ritorno) + prova di scala |
| Esportazione CSV | Superato | articoli, osservazioni, analisi, prezzi venduti |

**Test:** backend 558 (erano 486), estensione 46, frontend 46 — tutti verdi; `ruff`, `tsc`, `eslint` puliti.

**Verifica a mano (tua)**
1. `deploy/backup.sh`, poi `alembic upgrade head` sulla **copia** del tuo database; confronta i conteggi di `listings`, `listing_images`,
   `listing_snapshots`, `opportunities`, `sold_sales` prima e dopo (devono coincidere; `listing_snapshots` può crescere per i prezzi storici migrati).
2. Imposta `SELLER_KEY_SECRET` (e conservalo) **prima** di migrare: altrimenti viene derivato da `JWT_SECRET`.
3. Apri un articolo tracciato: storico prezzi con le date vere di osservazione; `GET /api/v1/items/<id>/analyses` mostra le analisi con il motivo.
4. Porta `STALE_ACTIVE_HOURS=1` e riavvia il worker: gli articoli non rivisti da un'ora passano a «To verify» e spariscono dalle opportunità attive.
5. Scarica i CSV dal menu «Export CSV» della pagina Archivio.

**Limiti noti e cose che NON sono state fatte in questa fase**
* Le funzioni fuori dai limiti (Q1: scanner automatico, letture automatiche, lettura dal server, clic programmatici su Preferiti e Acquista, misure su Vinted reale) **sono ancora nel codice**: la loro rimozione/sostituzione è la Fase 3 (nessuna logica nuova di questa fase ne dipende).
* Le foto vengono ancora scaricate dal **server** dal CDN di Vinted (Q3-A). Il passaggio al caricamento dall'estensione (Q3-B) è la Fase 3; in Fase 2 è pronto solo il modello dati (chiavi, hash, storico).
* La deduplica visiva (dHash) scatta **dopo** la copia locale delle foto (non alla cattura, perché l'estensione non invia hash). Finché l'archivio di un annuncio non è fatto, il riannuncio con foto uguali e titolo diverso non è riconosciuto.
* Provenienza per valore: oggi a livello di cattura (card/pagina), non di parte della pagina; arriva con le fonti per campo del parser (Fase 3).
* `model_price_stats` (le statistiche per modello usate dall'analisi) **fonde ancora** prezzi richiesti, reali e riportati in una sola mediana: la nuova tabella `sold-prices` li tiene separati, ma l'analisi non è stata ancora ricollegata. È la Fase 4 (D1).
* La calibrazione notturna che cambia i parametri da sola (D13) e le curve/default non tarati (D5) non sono toccati: Fasi 4, 6, 9.
* L'interfaccia è ancora in gran parte in inglese: i soli ritocchi sono le etichette di «To verify» e le voci di esportazione. La lingua si decide con Q5.
* Il confronto «prima/dopo» sulle statistiche dopo la rimozione di `account_created_at`/`sold_count` dai punteggi del venditore: i punteggi sono invariati perché quei campi non venivano mai valorizzati (verificato dai test).

### 11.7 Domande aperte per la Fase 3

Q4 (pagine reali per i test), Q5 (lingua dell'interfaccia), Q6/Q7 (Fase 4) restano da te. Per iniziare la Fase 3 mi serve **solo il tuo OK**:
senza Q4 comincio dai blocchi che non dipendono dalle pagine reali (rimozione di L1–L7 secondo Q1, azioni «Apri su Vinted», foto dal browser,
coda e «Ricollega») e rimando la verifica di Preferiti, armadio e «riservato» finché non mi dai le pagine.

---

## Appendice A — I casi di test A–H: dove saranno coperti

| Caso | Cosa verifica | Fase |
|---|---|---|
| A · Felpa Ralph Lauren 18 €, foto nitide, etichetta visibile | STRONG BUY solo dopo verifica di modello, costo totale e comparabili | 4, 5, 6 |
| B · Felpa Nike 10 €, "ottime condizioni" ma foro sulla manica | Incongruenza rilevata, condizioni penalizzate, convenienza ricalcolata | 5, 6 |
| C · Maglione firmato 40 €, due foto, nessuna etichetta | Informazioni mancanti elencate, nessuna dichiarazione di autenticità | 5, 6 |
| D · "Felpa blu uomo taglia M" con capo di marca riconoscibile nelle foto | Possibile Hidden Gem, da verificare | 5, 6 |
| E · Prodotto 15 €, rivendita 23 €, costi che assorbono il margine | PASS | 4, 6 |
| F · Annuncio dichiara L, etichetta mostra M | Discrepanza segnalata | 5 |
| G · Prodotto raro senza comparabili affidabili | Nessun prezzo di rivendita inventato | 4 |
| H · A: 20 €, alta incertezza, bassa domanda · B: 15 €, bassa incertezza, domanda provata | B sopra A | 6 |

Prova numerica di Fase 4: prezzo 20, spedizione 4, protezione 2, rivendita 45, costi di vendita 1 ⇒ investimento 26, ricavo netto 44, profitto 18, ROI 69,23 %.

## Appendice B — Fonti

- Termini di Vinted, sez. 6–7, versione applicabile dall'8/10/2026: <https://www.vinted.com/terms-and-conditions> (letti il 9/10/2026 tramite lettura automatica della pagina; da confrontare con l'originale).
- `robots.txt` di vinted.it, letto il 9/10/2026: <https://www.vinted.it/robots.txt>.
- Commento di terzi sui nuovi Termini (non ufficiale): <https://www.redrip.app/en/blog/new-vinted-terms-2026/>.
- Prezzi dei modelli Anthropic, cache e Batch, letti il 9/10/2026: <https://platform.claude.com/docs/en/about-claude/pricing>.
- Costo in token delle immagini: guida della skill `claude-api` (≈ 1 token per riquadro 28×28; fino a ~4.784 token a piena risoluzione); da calibrare con `count_tokens`.
- Serper e BCE: `docs/EXTERNAL_PRICES.md` (verificati dal progetto il 7/10/2026).
- Documentazione interna: `docs/DESIGN.md`, `docs/ARCHITECTURE.md` (mappa di una pagina), `docs/ACQUISITION.md`, `docs/EXTENSION_AUDIT.md`, `extension/README.md`.

## Appendice C — Glossario

- **Visto:** annuncio o card letto, con record leggero (identità, osservazione, valutazione rapida).
- **Analizzato:** annuncio con record completo e analisi versionata a blocchi.
- **Osservato / dichiarato / dedotto:** letto sulla pagina o sulla foto · scritto dal venditore · stimato da regole o da un modello.
- **Ultimo prezzo visto:** ultimo prezzo richiesto osservato su un annuncio poi venduto. **Non** è il prezzo di vendita reale.
- **Prezzo reale:** pagato o incassato da te (le tue vendite e i tuoi acquisti registrati).
- **Da verificare:** annuncio non osservato da troppo tempo; mai mostrato come acquistabile.
