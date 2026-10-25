# FlipFinder AI PRO — configurazione Cowork (primo compito)

Verifica del 25/10/2026, fatta nel container cloud di questa sessione sul ramo `claude/sweet-curie-9xyxzg`.
Nessun acquisto, offerta, messaggio o pubblicazione è stato fatto: nessuno di questi canali esiste nel sistema.

## 1. Cosa c'è nel progetto

| Componente | Dove | Stato verificato |
|---|---|---|
| API FastAPI + worker arq | `backend/app` | Codice completo; suite di test: vedi §4 |
| Database PostgreSQL 16 (24 migrazioni Alembic) | `backend/alembic`, `backend/app/db/models` | Migrazioni da zero fino alla 0024 eseguite con successo su un database vuoto |
| Formule economiche | `app/profit/calculator.py`, `app/profit/evaluation.py` | Verificate, conformi al brief (§3) |
| Flip Score, Risk Score, Confidence | `app/scoring/` | Separati; pesi configurabili per utente |
| Verdetto | `app/decision/engine.py` | STRONG BUY / BUY / NEGOTIATE / WATCHLIST / PASS / INSUFFICIENT EVIDENCE, con veti non compensabili |
| Piano capitale (budget, max per articolo, max articoli) | `app/decision/allocation.py`, `app/autonomy/limits.py` | Zaino 0/1 esatto, non supera mai il budget |
| Apprendimento dagli esiti | `app/analytics/`, `app/intelligence/`, tabella `prediction_outcomes` | Calibrazione attiva solo dopo 30 vendite reali; oggi nessun dato reale |
| Web app Next.js | `frontend/` | Typecheck pulito, 39/39 test |
| Estensione Chrome (cattura passiva da Vinted) | `extension/` | 52/52 test |
| Deploy (Docker, Caddy, backup) | `docker-compose*.yml`, `deploy/` | Non eseguito in questa sessione |

Gli ultimi cinque commit sono "WIP" di lavori interrotti (estrazione, confronti a livelli, ingestione,
visione). PROGRESS.md dice "In corso: nessuno", ma i messaggi di commit dicono "test non verificati
nel loro insieme": il risultato della suite (§4) è la verifica.

## 2. Integrazioni: reali, progettate, mancanti

| Integrazione | Stato | Cosa serve |
|---|---|---|
| **Grok (xAI)** | **Mancante.** Nessun codice nel backend (provider solo `anthropic` o `gemini`). In questa sessione esiste il connettore Composio `grok`, ma **senza connessione attiva**. | Collegare l'account xAI in Composio (lo fa l'utente), oppure aggiungere un provider `xai` al backend con la chiave nel `.env`. Fino ad allora il passo 5 del ciclo è "non eseguito", mai simulato. |
| Claude / Gemini nel backend | Progettato e testato con client finti (LIMITATIONS L05, L31). Nessuna `AI_API_KEY` nell'ambiente. | Chiave nel `.env` del server. |
| Fonte annunci Vinted | Reale ma passiva: estensione browser sulle pagine che l'utente apre, link incollati, email di notifica (IMAP), import manuale. Il server non legge Vinted (Termini dell'8/10/2026). | Estensione installata; IMAP facoltativo. |
| Vendite concluse | Parziale: Vinted non pubblica i prezzi di chiusura; "venduto" = annuncio osservato passare a venduto (L03). | Le vendite dell'utente registrate nell'app sono la fonte più affidabile. |
| Prezzi esterni (Serper) | Progettato, mai provato con una risposta reale. | `SERPER_API_KEY`. |
| Notifiche (in-app, push, email, Telegram, Discord) | Codice presente; in-app sempre attivo, gli altri richiedono credenziali. | Token/credenziali nel `.env` e nelle Impostazioni. |
| Acquisti, offerte, messaggi, pubblicazione su Vinted | **Assenti per scelta** (L01, L27): nessuna API ufficiale. Le bozze sono testi da copiare. | Nessuna: resta un'azione umana. |
| Database condiviso con Cowork | Il database è quello dell'installazione dell'utente (Docker). Da questa sessione cloud non è raggiungibile. | Accesso via API (`/api/v1`, chiave API dalle Impostazioni) da un ambiente autorizzato. |
| Pianificazione | Worker arq con job periodici (ricalcoli, statistiche, controlli di stato). | Stack Docker in esecuzione. |

## 3. Verifica delle formule

Le formule del motore corrispondono al brief:

- **Costo totale** = prezzo + protezione acquisti (quella mostrata dall'annuncio, altrimenti 0,70 + 5%) + spedizione (quella dell'annuncio, altrimenti 3,49) + altri costi + ripristino.
- **Ricavo netto** = prezzo di vendita − commissioni − pagamento − promozione − imballaggio − spedizione a carico del venditore − altri − riserva.
- **Profitto** = ricavo netto − costo totale. **ROI** = profitto / costo totale.
- La protezione acquisti entra solo nel costo d'acquisto (la paghi tu come acquirente); le commissioni di vendita sono 0 per default (su Vinted le paga l'acquirente): **nessuna spesa contata due volte, nessuna commissione dell'acquirente attribuita al venditore**.
- Ogni costo ha uno stato: `confirmed` (letto sull'annuncio o salvato dall'utente), `estimated`, `unknown` (escluso dai totali, il profitto è dichiarato "al lordo di"). Le imposte non sono stimate.
- Il prezzo massimo d'acquisto è l'inverso esatto delle formule, arrotondato per difetto.

Differenze rispetto al brief, da decidere:

1. **Flip Score**: il motore usa 8 componenti (profitto 25, ROI 15, domanda 15, prezzo vs mercato 15, condizioni 10, rischio 10, informazioni 5, tempo 5). I pesi del brief (30/25/20/15/10) si ottengono con i pesi per utente (`score_weights`), ma oggi cambiano solo il punteggio mostrato, non il verdetto (L11). Il collaudo usa i pesi del brief con il rischio a peso zero, così il Risk Score resta indipendente.
2. **Prezzo prudente**: il motore calcola tre scenari (prudente, atteso, ottimistico); il brief chiede di decidere sul prudente. Il verdetto del motore usa più segnali (Monte Carlo, comparabili, veti). Il collaudo decide solo sul prudente.
3. **Spesa massima per articolo**: `max_purchase_price` del motore confronta il prezzo dell'annuncio; il collaudo confronta il **costo totale** (più prudente).
4. **Soglie**: le predefinite del motore sono profitto 10, ROI 40%, alert Flip 85, rischio alert 60. Le soglie del brief vanno salvate nelle Impostazioni (tabella in §5).

## 4. Test eseguiti

- Backend: vedi la sezione "Risultato della suite" in fondo.
- Frontend: `tsc --noEmit` pulito, `vitest` 39/39.
- Estensione: `node --test` 52/52.
- Collaudo del flusso minimo: `tests/unit/test_cowork_smoke.py` 7/7.

## 5. Soglie iniziali proposte e dove si impostano

| Soglia del brief | Valore | Campo nell'app (Impostazioni → Preferenze / Notifiche) |
|---|---|---|
| Budget complessivo | 100 € | `total_budget` |
| Spesa massima per articolo | 25 € | `max_purchase_price` (sul prezzo) e `autonomy.max_per_item` |
| Profitto netto prudente minimo | 12 € | `min_profit`, `alert_min_profit` |
| ROI minimo | 50% | `min_roi`, `alert_min_roi` |
| Flip Score minimo per l'alert | 75 | `alert_min_flip_score`, `min_flip_score` |
| Risk Score massimo | 30 | `alert_max_risk_score`, `max_risk_score` |
| Pesi Flip Score | 30/25/20/15/10 | `score_weights` = `{"profit":30,"roi":25,"demand":20,"sale_time":15,"info":10,"price_vs_market":0,"condition":0,"risk":0}` |

Sono valori di partenza, non calibrati: si rivedono dopo almeno 30 vendite registrate.

## 6. Flusso minimo collaudabile con dati di prova

```bash
cd backend
python -m app.tools.cowork_smoke            # report leggibile
python -m app.tools.cowork_smoke --json     # schede e report in JSON
python -m app.tools.cowork_smoke --input mio_lotto.json
```

Nessun database e nessuna rete. Dati: `backend/app/tools/cowork_sample.json` (9 articoli inventati,
dichiarati come tali). Passi coperti:

1. dati in ingresso dal file; 2. controllo di URL, data e prezzo; 3. deduplica per ID Vinted (uguale su
tutti i domini); 4. scheda articolo; 5. Grok: dichiarato "non collegato", nessun risultato inventato;
7. profitto prudente e ROI con `evaluate_deal` (formule del motore); 8. Flip Score (pesi del brief) e
Risk Score (`assess_risk`); 9-10. classificazione **SCARTA / MONITORA / REVISIONE UMANA / OPPORTUNITÀ
DA APPROVARE**; controllo del budget; 11. alert preparati una sola volta per articolo, non inviati;
12. report di sessione.

Regole di classificazione:

- contraffazione sospetta o Risk Score ≥ 75 → **SCARTA**, qualunque sia il margine;
- dati essenziali mancanti o nessun comparabile → **REVISIONE UMANA**;
- soglie economiche non raggiunte → **MONITORA** se il prezzo massimo d'acquisto è almeno il 75% del prezzo (trattabile), altrimenti **SCARTA**;
- soglie raggiunte ma Risk Score > 30, meno di 3 vendite concluse comparabili, costi non conosciuti o domanda/tempi senza dati → **REVISIONE UMANA**;
- tutto in regola e Flip Score ≥ 75 → **OPPORTUNITÀ DA APPROVARE** (se rientra nel budget residuo), altrimenti **MONITORA**.

Prezzo prudente: primo quartile delle vendite concluse; con soli prezzi richiesti, primo quartile −20%
(ipotesi dichiarata nella scheda, da calibrare). Il profilo costi va confermato: senza conferma
promozione e spedizione a carico del venditore restano "non conosciute" e nulla supera la revisione.

Esito sui dati di prova: 8 analizzati, 1 duplicato, 1 opportunità (TEST-001: costo 16,82, profitto
prudente 15,18, ROI 90%, Flip 86, Risk 5, prezzo massimo 15,53), 3 in revisione, 2 da monitorare,
2 scartati. Capitale impegnato 0, alert inviati 0.

## 7. Formato condiviso per Grok (quando sarà collegato)

Richiesta (una per articolo):

```json
{
  "request_id": "ff-<item_id>-<timestamp>",
  "item": {"id": "...", "url": "...", "captured_at": "ISO 8601", "title": "...", "brand": "...",
           "size": "...", "condition": "...", "price": "12.50", "shipping": "2.99",
           "description": "...", "photo_urls": []},
  "known_comparables": [{"source": "...", "date": "...", "price": "...", "sold": true}],
  "questions": ["prezzo realizzato tipico", "domanda", "rischi di autenticità", "dati da verificare"]
}
```

Risposta attesa:

```json
{
  "request_id": "...",
  "comparables": [{"source": "...", "url": "...", "date": "...", "brand_model": "...", "size": "...",
                   "condition": "...", "price": "...", "price_kind": "sold|asking", "differences": "..."}],
  "demand": {"evidence": "...", "sell_through": null, "median_days_to_sell": null},
  "risks": ["..."],
  "to_verify": ["..."],
  "confidence": "low|medium|high"
}
```

Regole di controllo della risposta: ogni comparabile senza fonte o data viene scartato; `price_kind`
assente vale `asking`; numeri di domanda senza `evidence` valgono "non disponibili". Il profitto e i
punteggi li calcola sempre FlipFinder, mai Grok.

## 8. Prossime azioni (decisioni dell'utente)

1. Collegare Grok (Composio o chiave xAI nel backend) — oppure decidere di usare Claude/Gemini già previsti.
2. Salvare le soglie del §5 e confermare il profilo costi nelle Impostazioni.
3. Decidere se il verdetto del motore deve usare i pesi del brief (oggi non li usa, L11).
4. Avviare lo stack (`docker compose up -d`) e catturare i primi annunci reali con l'estensione.
5. Registrare acquisti e vendite reali: senza di essi nessuna calibrazione è possibile.
