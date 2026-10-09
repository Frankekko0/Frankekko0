# OPERATIONS — far girare FlipFinder ogni giorno

Per l'installazione, il dominio/HTTPS, i backup e gli aggiornamenti vedi [`deploy/README.md`](../deploy/README.md): qui non si ripete.
Questo file dice **cosa guardare, cosa fare quando qualcosa non va e cosa non fa mai il sistema**.

## 1. Cosa gira

| Pezzo | Ruolo | Se si ferma |
|---|---|---|
| API (FastAPI) | tutte le pagine e l'estensione | l'app non risponde; i dati restano |
| Worker (arq) | analisi, job schedulati, ciclo di autonomia, rapporto giornaliero | niente nuove analisi né avvisi finché non riparte; si recupera da solo |
| PostgreSQL 16 | tutti i dati | l'API risponde 5xx; ripristina dal backup solo se i dati sono danneggiati |
| Redis 7 | code, cache, limiti di frequenza, blocchi | i job non partono; l'API può rispondere con errori sui limiti |
| Frontend (Next.js) | l'interfaccia | l'API e l'estensione continuano a funzionare |
| Estensione | legge **solo** ciò che apri su Vinted | nessuna cattura nuova |

Controllo rapido: `GET /api/v1/health` (vivo) e `GET /api/v1/health/ready` (database e Redis raggiungibili); `GET /api/v1/system/status` per i job e le code.

## 2. Job programmati (UTC, nel worker)

| Job | Quando | Cosa fa |
|---|---|---|
| `recompute_market_statistics` | :00 :15 :30 :45 | statistiche per marca/categoria/modello |
| `refresh_listings` | ogni 10 minuti | ciclo di vita degli annunci con un provider |
| `mark_stale_listings` | :05 :20 :35 :50 | segna «da verificare» gli annunci non visti da troppo |
| `recompute_learning` | :07 :37 | punteggio personale dai flip reali |
| `review_candidates` | :09 :39 | agente di revisione (solo con `AI_API_KEY`, entro i tetti) |
| `autonomy_cycle` | :12 :42 | un ciclo di autonomia **solo se attiva** e non sospesa/fermata |
| `business_daily` | 06:11 | rapporto giornaliero, avvisi sulle soglie, ricalcoli |
| `prune` | 03:17 | pulizia dei log tecnici |
| prezzi esterni / evidenze | ogni ora / ogni 30 minuti / di notte | con i loro limiti di richieste |

Un job che fallisce ritenta con attesa crescente; ogni analisi è tracciata in `analysis_jobs`.

## 3. Ogni giorno (2 minuti)

1. **Autonomia** (`/autonomy`): stato (Off / Dry run / Assistita), eventuale sospensione e motivo, azioni «In attesa di te».
2. **Spesa AI**: `GET /api/v1/ai/usage`: spesa di oggi e del mese contro i tetti.
3. **Errori**: Impostazioni → *Error log* (avvisi ed errori di API e worker, dati sensibili oscurati).
4. **Rapporto di apprendimento** (`GET /api/v1/learning/report`): se compare un allarme di deriva, leggi l'azione proposta; nessun parametro cambia da solo.

## 4. Autonomia: come si ferma e come si riprende

- **Fermare subito**: pagina *Autonomy* → «Stop everything now» (o `POST /api/v1/autonomy/kill`). Vale all'istante: non si propone né si prepara più nulla finché non riprendi (le azioni già preparate restano a te da confermare o rifiutare).
- **Sospensione automatica**: scatta da sola con tasso d'errore > 30%, perdita > 50 € nella finestra di 14 giorni o errore di previsione > 35% (soglie modificabili nei limiti). Il motivo è scritto in pagina e nel registro. Si toglie con «Lift suspension» **dopo** aver capito perché; riprendere senza capire riporta alla stessa sospensione.
- **Attivazione**: senza un budget nei limiti non si prepara alcun acquisto. Si parte in dry-run (7 giorni); il rapporto del dry-run dice cosa avrebbe comprato e se gli annunci erano ancora disponibili.
- **Registro di audit**: `GET /api/v1/autonomy/audit` (a sola aggiunta: il database rifiuta modifiche e cancellazioni).
- L'autonomia **non agisce mai su Vinted** (L27): ogni azione preparata la fai tu e poi la confermi.

## 5. Spesa AI

- Tetti: `AI_DAILY_BUDGET_USD` (1,00) e `AI_MONTHLY_BUDGET_USD` (20,00). Raggiunto un tetto non parte più nessuna chiamata e l'app lavora con le regole.
- Senza `AI_API_KEY` l'AI è spenta e tutto il resto funziona. **L'AI dal vivo non è mai stata provata** in questo lavoro (L05): alla prima accensione guarda la spesa dopo le prime analisi e correggi `AI_PRICE_*_PER_MTOK` ai tuoi prezzi.
- Interruttore del fornitore: dopo 5 errori consecutivi l'app smette di chiamarlo per 60 secondi (usa le regole), poi prova una chiamata sola.
- `VISION_VOI_COST_EUR` decide quanto spesso conviene pagare l'analisi delle foto (assunzione, vedi `DEPENDENCIES.md`).

## 6. Aggiornare il database e tornare indietro

1. Backup (`deploy/backup.sh`; il compose di produzione lo fa già prima di ogni avvio).
2. `alembic upgrade head` (l'avvio lo esegue). La **0022** aggiunge colonne a `inventory` e sei tabelle; le righe esistenti ricevono `to_list` (o `returned`).
3. Se qualcosa va storto: `deploy/restore.sh backups/<file>.dump` (sostituisce i dati attuali) oppure `alembic downgrade 0021` (rimuove le tabelle e le colonne nuove: **perde** autonomia, obiettivi, spese ed esiti registrati).

## 7. Problemi comuni

| Sintomo | Causa probabile | Cosa fare |
|---|---|---|
| Nessuna analisi nuova | worker fermo o Redis giù | riavvia il worker; controlla `/health/ready` |
| «Acquisto forte» non compare mai | mancano foto analizzate da un modello o l'etichetta (requisiti), o `AI_API_KEY` assente | è voluto: servono foto analizzate da un modello, l'etichetta vista e un rischio di perdita basso (D61); apri l'annuncio con l'invio foto acceso nell'estensione |
| Foto non analizzate | galleria incompleta o permesso `*.vinted.net` non concesso nell'estensione | l'estensione dichiara a fine lavoro cosa non ha letto; il dossier lo riporta |
| Autonomia ferma da sola | sospensione automatica | leggi il motivo in `/autonomy`, poi «Lift suspension» |
| Cifre del piano marcate «assumption» | meno di 5 vendite chiuse | registra le vendite: il piano passa a `measured` da solo |
| «Non misurabile» nel rapporto di apprendimento | esiti reali insufficienti | normale all'inizio (L25); non va forzato |
| Spesa AI ferma al tetto | tetto raggiunto | attendi il giorno/mese successivo o alza il tetto consapevolmente |
| Errore 429 | limite di frequenza | rallenta; `RATE_LIMIT_PER_MINUTE` |

## 8. Cosa il sistema non fa mai

- Non contatta Vinted dal server; l'estensione legge solo le pagine che apri tu, senza clic, scroll o richieste automatiche.
- Non compra, non offre, non scrive ai venditori: prepara testi e prezzi che invii tu.
- Non inventa prezzi, soglie fiscali o aliquote: ciò che non è misurato è scritto come assunzione o «non verificato».
- Non cambia da solo i propri parametri: ricalibrazione e sfidanti passano da una regola di promozione con dati tenuti da parte.
- Non conserva segreti nel repository: tutto da variabili d'ambiente.
