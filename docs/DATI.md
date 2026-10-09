# Modello dati (stato dopo la Fase 2)

Documento di riferimento per capire **cosa viene salvato, con quale significato e come verificarlo**.
Lo stato dei lavori e le decisioni stanno in `PIANO_FLIPFINDER.md`.

## Principi

* **Ogni valore ha un tipo.** *Osservato* (letto sulla pagina o sulla card così com'è), *dichiarato*
  (scritto o scelto dal venditore), *dedotto* (calcolato da altro, con la sua confidenza). Nelle
  osservazioni il tipo è nel `payload` (`t`: observed / declared / inferred; `c`: read / approx).
* **Mai un valore al posto di "non visibile".** Un campo che una cattura non contiene non compare nel
  `payload`; non viene scritto come vuoto. Una data di pubblicazione ignota resta `NULL`
  (`published_at_kind = 'unknown'`), non diventa il momento in cui abbiamo visto l'annuncio.
* **Prezzo richiesto ≠ prezzo di vendita.** In `sold_sales`: `asking_price` (ultimo prezzo richiesto visto
  mentre era in vendita) e `realized_price` (prezzo realmente pagato o incassato: i tuoi acquisti e
  rivendite) sono colonne separate e un vincolo del database le tiene distinte. I prezzi riportati da altri
  siti (`reported`) non hanno né l'una né l'altra.
* **Storia a sola aggiunta.** Osservazioni e analisi non si modificano né si cancellano: una modifica
  aggiunge una riga. Un trigger del database rifiuta gli `UPDATE` su `analyses`.

## Tabelle

| Tabella | Cosa contiene | Note |
|---|---|---|
| `listings` | L'annuncio: identità (ID interno, `provider`+`external_id` = ID Vinted, `url`), campi dichiarati, stato, date | Mai cancellata. `published_at` + `published_at_kind`; `first_seen_at`, `last_seen_at`, `last_verified_at`; `status_before_verify`; `duplicate_of_id` + `duplicate_evidence` |
| `listing_snapshots` | Una **osservazione**: prezzo, stato, preferiti, foto (chiavi in ordine), versione estensione e parser | Una riga alla prima vista, a ogni cambio (prezzo, stato, preferiti, foto, cattura più ricca) e ogni 6 h (`SNAPSHOT_HEARTBEAT_HOURS`). `reason` dice perché esiste |
| `listing_price_history` | **Vista** sugli snapshot: primo prezzo e ogni variazione | Prezzo iniziale = primo; attuale = ultimo; minimo = `min(price)` |
| `listing_images` | Le foto dell'articolo | `image_key` stabile; `first_seen_at`/`last_seen_at`; `removed_at` se il venditore l'ha tolta (la riga e la copia restano); `sha256` e `phash` (dHash) della copia locale |
| `analyses` | Ogni analisi che ha dato un risultato diverso | Cinque blocchi (`product`, `visual`, `economic`, `market`, `decision`) ciascuno con `v`; ID interno e Vinted, URL, fonte, data, `schema_version`, `algorithm_version`, `trigger`, `input_hash`, `result_hash` |
| `opportunities` | L'analisi **corrente** per le query del feed | `analysis_id` → `analyses` |
| `opportunity_scores` | Storico dei punteggi | Una riga per analisi nuova (non per ogni ricalcolo identico) |
| `sold_sales` | Vendite concluse | `price_kind`: `last_seen` / `paid` / `received` / `reported`; `window_start`–`window_end` = intervallo in cui è avvenuta; `sold_at` = punto medio; `days_to_sell` (dalla pubblicazione, se nota) e `observed_days` (dalla prima vista: minimo) |
| `sellers` | Chiave opaca, valutazione media, numero di recensioni | Chiave `s2:` = HMAC con `SELLER_KEY_SECRET` |

## Stati di un annuncio

`active` · `reserved` · `sold` · `removed` · `to_verify` ("da verificare") · `unknown` (solo link, stato mai letto).

* `sold` richiede una prova positiva (pagina, e-mail, fonte). Un annuncio sparito è `removed`: nessuna vendita dedotta.
* Un annuncio `active` non rivisto da 48 h (o `reserved` da 24 h: `STALE_ACTIVE_HOURS`, `STALE_RESERVED_HOURS`) diventa
  `to_verify` ad opera del worker (ogni 15 min): non è più un'opportunità attiva né compare come acquistabile.
  Alla prima nuova osservazione torna a ciò che la pagina dice.
* "Visto" = record leggero (link, card, o analizzato solo dalla card); "analizzato" = pagina dell'annuncio letta e
  analizzata a fondo (`record_level` nelle API e nei CSV).

## Cosa fa scattare una nuova analisi (`analyses.trigger`)

`new` (prima volta) · `price_change` · `photos` (foto aggiunte, tolte, cambiate, riordinate) · `status_change` ·
`data_changed` (testo e altri campi) · `recompute` (stessi dati, risultato diverso: il mercato è cambiato o la versione
dell'algoritmo) · `manual` · `migrated` (record iniziale creato dalla migrazione). Se input, versione e risultati sono
identici all'analisi corrente **non si crea nulla**.

## Esportazioni

`GET /api/v1/items/export.csv` (articoli) · `/items/export-observations.csv` (storico) · `/items/export-analyses.csv`
(analisi) · `/pricing/sold-prices/export.csv` (prezzi venduti per modello). Parametro `delimiter=semicolon` per Excel
italiano. Sono nel menu "Export CSV" della pagina Archivio.

## Migrazioni (0010–0016)

Additive e reversibili; **fai un backup prima** (`deploy/backup.sh`). Verificate con 30.000 annunci, 90.000 foto e 60.000
osservazioni: 14 s in avanti, 3 s all'indietro, nessuna riga persa. La chiave dei venditori (0014) non si può "tornare
indietro" (un HMAC non è reversibile): il `downgrade` lascia le chiavi protette.
