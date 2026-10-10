# DEPENDENCIES — servizi esterni e a pagamento

Ogni servizio che esce dal tuo server o costa denaro è qui. **Niente è dichiarato gratuito se non lo è.**
Tutto è facoltativo: senza chiavi l'app lavora con le regole.

## A pagamento

| Servizio | A cosa serve | Quando viene chiamato | Controllo della spesa |
|---|---|---|---|
| **API Anthropic** (`AI_API_KEY`) | analisi dell'affare (`ai/claude_analyst.py`), lettura delle foto (`vision/analyzer.py`), ricerca in linguaggio naturale, agente di revisione (`agent/`) | solo con la chiave; mai per annunci chiaramente da scartare | **tetto giornaliero e mensile in USD** (`AI_DAILY_BUDGET_USD` 1,00 · `AI_MONTHLY_BUDGET_USD` 20,00): prima di ogni chiamata si controlla la spesa registrata in `ai_usage`; raggiunto il tetto la chiamata **non parte** e si usano le regole. Interruttore automatico dopo 5 errori consecutivi (`AI_BREAKER_*`). `GET /api/v1/ai/usage` mostra spesa e tetti |
| **Serper** (`SERPER_API_KEY`) | riferimenti di prezzo esterni (`docs/EXTERNAL_PRICES.md`) | worker, mai mentre si analizza una pagina | budget mensile e giornaliero di richieste (`EXTERNAL_SEARCH_*`) |

**I prezzi dei token sono assunzioni.** Il sistema non legge il listino del fornitore: conta i token che l'API
restituisce e li moltiplica per i prezzi in `AI_PRICE_*_PER_MTOK` (predefiniti 3/15 USD per milione di token
in/out per il modello forte e 1/5 per l'economico). Se il tuo piano costa di più, la spesa mostrata sarà sotto la
realtà: **impostali ai tuoi prezzi**. Il costo medio per annuncio non è ancora misurato (nessuna chiave in questa
fase di lavoro, vedi `LIMITATIONS.md` L05).

Routing: il volume (agente di revisione) usa `AI_MODEL_CHEAP`; le decisioni ad alto impatto usano `AI_MODEL`.
Le foto vanno al fornitore del modello (possono contenere persone); l'app chiede di ignorarle ma il dato viaggia.

## Valori assunti che muovono i numeri (non servizi, ma costano se sbagliati)

| Impostazione | Default | Perché è un'assunzione |
|---|---|---|
| `VISION_VOI_COST_EUR` | `0.50` | Costo in euro assegnato a un'analisi delle foto nel calcolo del valore dell'informazione (chiamata al modello + attesa). Non è misurato: nessuna chiave in questa fase (L05). Più basso = analizza più spesso e spende di più; la spesa reale resta limitata dai tetti `AI_DAILY_BUDGET_USD` / `AI_MONTHLY_BUDGET_USD`. |
| Resi, ore per articolo, costo del capitale, ritardo di incasso | 4% · 45 min · 0,2%/giorno · 3 giorni | Elencati in LIMITATIONS L26; ogni risultato che li usa è marcato `assumption`. |

Le fasi 8–8d non aggiungono servizi esterni: sopravvivenza, Monte Carlo, Kelly, calibrazione, deriva, piano, cash flow e
scanner girano nel tuo server. Il secondo parere del verificatore usa il modello **economico** solo se c'è la chiave e
il tetto di spesa lo permette (mai provato dal vivo, L27).

## Gemini (alternativa con chiave gratuita)

`AI_PROVIDER=gemini` usa l'API di Google (`app/ai/gemini.py`, REST, nessuna dipendenza in più). La chiave gratuita si crea in Google AI Studio. Attenzione: nel piano gratuito Google può usare i dati inviati per migliorare i suoi prodotti e i limiti sono bassi e variabili; tieni `AI_VISION_ENABLED=false` se le foto non devono uscire dal server e imposta a 0 i quattro `AI_PRICE_*`. **Provato solo con un server finto nei test, mai dal vivo** (nessuna chiave): i nomi dei modelli vanno verificati in AI Studio.

## Facoltativi senza costo diretto

| Servizio | Uso |
|---|---|
| SMTP, Telegram, Discord, Web Push | consegna degli avvisi (i tuoi account; Web Push usa i servizi del browser) |
| IMAP | lettura delle email di notifica di Vinted, sola lettura |
| Caddy / Let's Encrypt | HTTPS in produzione |

## Infrastruttura (nel tuo server)

PostgreSQL 16 (`pg_trgm`), Redis 7, Python ≥ 3.11, Node per il frontend. Nessun altro servizio.

## Software installato (extra opzionale)

| Pacchetto | Uso | Note |
|---|---|---|
| `rapidocr-onnxruntime` (extra `ocr`) | OCR locale delle etichette | solo `pip`; ONNX Runtime + OpenCV + modelli inclusi; gratuito, offline, nessuna chiave. Se manca, l'OCR è spento e il resto funziona |

## Non usati

Nessun servizio di scraping, proxy o risoluzione CAPTCHA: l'unica fonte di dati di Vinted è ciò che l'utente apre
nel suo browser (estensione). Nessun servizio di OCR a pagamento: l'OCR è locale.
