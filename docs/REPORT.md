# REPORT — stato di FlipFinder AI (prompt v3)

Data: 2026-10-09 · ramo `claude/sweet-curie-9xyxzg` · una pagina di fatti: cosa c'è, cosa è misurato, cosa no.

## 1. Cosa è stato fatto

| Fase | Stato | Nota |
|---|---|---|
| 0–1 Audit, dati di base | fatte | `ARCHITECTURE.md`, `DECISIONS.md` (D1–D67), `LIMITATIONS.md` (L01–L30) |
| 2–3 Motore finanziario, mercato | fatte | calcolo esatto con stati dei costi; casi 26/44/18/69,23%, E, G, H nei test |
| 4–4b Foto, OCR, dossier | fatte | OCR locale; foto caricate **dal browser** dell'utente (Q3-B), opt-in, **non provate su Vinted reale** (L21) |
| 5–6 Decisione, orchestratore | fatte | un solo verdetto (6 livelli) con veti; agente con strumenti tipizzati, budget e interruttore |
| 7 Dashboard | fatta | home per verdetto, piano d'acquisto, «Perché»; lingua dell'app non decisa (Q5, L23) |
| 8 Ciclo di vendita | fatta | stati, bozza annuncio, piano di prezzo e ribassi, offerte, contabilità, trattativa |
| 8b Autonomia | **parziale per scelta** | decide e prepara; solo canali `dry_run` e `assistito`: **non agisce mai su Vinted** (L27) |
| 8c Intelligenza avanzata | fatta, **non misurata su dati reali** | sopravvivenza, Monte Carlo, Kelly, VOI, pre-mortem, calibrazione, deriva, baseline (L25) |
| 8d Modalità imprenditore | fatta, con valori **assunti** dichiarati | piano, KPI, cash flow + stress, nicchie, soglie con fonte e data, scanner (L26, L28) |
| 9–10 Hardening, rilascio | fatte | questo file, `OPERATIONS.md`, `DEPENDENCIES.md` |

## 2. Cosa è stato misurato

- **Test**: backend 1063 passati; estensione 48 (+ prova end-to-end con l'estensione vera in Chromium su pagine Vinted simulate); frontend 35. `ruff` pulito; `mypy` 93 errori (base 95: nessuna regressione).
- **Interfaccia**: pagine nuove a 390 px senza scorrimento orizzontale né errori in console.
- **Prestazioni** (4 core, mercato sintetico di ~5.000 vendite, stesso banco per ogni commit): analisi per annuncio 102/s prima del v3, 96/s con il motore decisionale, 73/s con visione e dossier, **~75/s oggi** (13 ms). Il Monte Carlo costa 1,7 ms per analisi dopo la correzione di questa fase (con il calcolo `Decimal` per campione costava 11 ms e portava a 45/s); la correzione è provata uguale al calcolatore esatto al centesimo. L'adattamento della sopravvivenza (solo per il piano di vendita) costa 3 ms su 200 osservazioni e 143 ms su 10.000. Il «~490/s» del README è di un'altra misura e **non è stato riprodotto** su questo banco.
- **Stimatori su dati sintetici dichiarati**: la sopravvivenza recupera la sensibilità al prezzo impostata (4,0 → 4,0).
- **Sicurezza** (revisione mirata): nessun segreto nel codice; nessun SQL costruito con stringhe; ogni risorsa nuova filtrata per utente, con un test che prova che un secondo account non vede né modifica nulla (inventario, spese, autonomia, obiettivi, esperimenti). Corretti in questa fase: celle dell'export CSV non neutralizzate contro le formule (e la cella vuota che riceveva un apice), `listing_url` accettava `javascript:`, il prompt dell'analista non dichiarava il titolo come dato non fidato (il verdetto restava comunque limitato dal motore).

## 3. Cosa NON è misurato (e non va dato per vero)

1. **L'AI dal vivo non è mai stata provata**: nessuna `AI_API_KEY` in questo lavoro. Il costo per annuncio è **sconosciuto**; i prezzi dei token sono assunzioni (`DEPENDENCIES.md`). Secondo parere del verificatore e analisi delle foto da modello: solo con modelli finti nei test.
2. **Nessun esito reale**: calibrazione, controfattuale, baseline e sfidante rispondono «non misurabile». **Nessuna affermazione che l'AI batta il baseline** (L25).
3. **Vinted reale**: l'invio delle foto dal browser e il comportamento del CDN non sono verificati (L21); l'estensione resta, per i Termini, uno strumento esterno con rischio residuo dell'utente (L10).
4. **Autonomia**: mai esecuzione automatica su Vinted; il periodo di prova di 7 giorni non è stato vissuto su dati reali.
5. **Valori assunti** (resi 4%, 45 min/articolo, 0,2%/giorno, ritardo di incasso 3 giorni…) marcati `assumption` nei risultati finché non li sostituiscono le tue vendite (L26).
6. **Lingua dell'interfaccia**: Q5 senza risposta; app in inglese, estensione e messaggi del server in italiano (D55).
7. Connettori non pertinenti (Gmail, Shopify, Figma, Webflow…) non usati di proposito: toccano dati o account altrui senza motivo.

## 4. Prossimi passi, in ordine

1. Imposta `AI_API_KEY` con tetti bassi e misura il costo per annuncio nella prima settimana (`/ai/usage`).
2. Usa l'app per le catture reali e registra acquisti e vendite: la calibrazione diventa misurabile con 30 esiti reali, il confronto con il baseline con almeno 12 per segmento (marca · categoria), lo sfidante con 30 casi. Prima il rapporto dice «non misurabile».
3. Accendi l'autonomia solo in dry-run per 7 giorni e leggi il rapporto prima di passare all'assistita.
4. Rispondi a Q5 (lingua) e decidi se provare l'invio foto su Vinted con il tuo account, a tuo rischio.
5. Aggiungi al calcolo i costi personali nel piano d'acquisto (L24) e la lettura di codici a barre nello scanner (L28), se servono.
