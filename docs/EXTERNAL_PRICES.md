# Prezzi da altri mercati (ricerca esterna)

Stato: ottobre 2026. Prezzi e condizioni di Serper verificati il **7/10/2026** sul sito ufficiale
(link in fondo); tassi di cambio: riferimento BCE del 7/10/2026.

Per ogni modello (la "linea" riconosciuta dal motore di identificazione, per esempio *Nike Air Max
90*) FlipFinder raccoglie da altri mercati:

- **prezzi da nuovo** dei negozi (Zalando, Foot Locker, sito del marchio...);
- **prezzi chiesti per l'usato** sui mercati dell'usato (eBay, Vestiaire Collective, Depop,
  Grailed, Subito, Wallapop);
- **vendite concluse**, quando la pagina lo dice ("Venduto il 12 set 2026", "Sold Sep 12, 2026").

Ogni prezzo salvato ha **fonte** (dominio o negozio e link), **data** (quella indicata dalla fonte,
altrimenti il giorno della ricerca), **valuta** originale, **prezzo in euro**, **condizione** e
**tipo** (`new` nuovo, `asking` in vendita, `sold` venduto).

## Regole

- **Solo API ufficiale**: [Serper.dev](https://serper.dev) restituisce i risultati di Google come
  JSON. Nessuno scraping di Google né dei siti trovati: si leggono solo titolo, link, estratto,
  prezzo e data che la ricerca stessa restituisce.
- **Mai durante l'analisi di una pagina.** L'analisi legge soltanto ciò che è già nel database;
  si limita ad aggiungere i modelli che vede alla coda (`external_searches`, colonna `demand`).
  La ricerca la fa il worker, in background, una volta all'ora, entro il budget.
- **Vinted è escluso**: è la nostra fonte principale (catture dell'estensione), non un "altro
  mercato". I risultati di vinted.* vengono scartati con motivo `source`.
- **Cache per modello**: un modello cercato non viene ricercato prima di `EXTERNAL_REFRESH_DAYS`
  giorni (30 per default) e, anche dopo, **solo se nel frattempo è stato visto di nuovo** in
  un'analisi. Modelli che non interessano più non consumano query.

## Cosa si cerca

Per ogni modello, nell'ordine (ogni query = 1 credito, fino a 10 risultati):

| # | Endpoint | Query (esempio) | Serve per |
|---|----------|-----------------|-----------|
| 1 | Google Shopping (`/shopping`) | `Nike Air Max 90` | prezzi da nuovo dei negozi, offerte usate dei mercati |
| 2 | Google Web (`/search`) | `Nike Air Max 90 usato prezzo (site:ebay.it OR site:ebay.com OR site:vestiairecollective.com OR site:depop.com OR site:grailed.com OR site:subito.it OR site:wallapop.com)` | annunci dell'usato con prezzo, a volte vendite concluse |
| 3 | Google Web (`/search`), *facoltativa* | `Nike Air Max 90 (venduto OR sold) (site:ebay.it OR site:ebay.com OR site:vestiairecollective.com OR site:grailed.com)` | vendite concluse con data |

La terza query parte **solo se le prime due hanno trovato il modello** e il budget lo consente
(disattivabile con `EXTERNAL_SEARCH_SOLD_QUERY=false`). Paese e lingua dei risultati:
`EXTERNAL_SEARCH_COUNTRY=it`, `EXTERNAL_SEARCH_LANGUAGE=it`.

Dei risultati web si tengono solo le **pagine di un singolo articolo** (eBay `/itm/`, Vestiaire
`...-123456.shtml`, Depop `/products/`, Grailed `/listings/`, Subito `...-123456.htm`, Wallapop
`/item/`): pagine di ricerca o di categoria ("Da EUR 45,00") sono scartate (`not_item`).

## Costi (verificati il 7/10/2026)

| Piano Serper | Prezzo | Crediti | Costo per query |
|--------------|--------|---------|-----------------|
| Prova gratuita | 0 € | **2.500**, senza carta di credito | 0 |
| Starter | **$50** | 50.000 | **$0,001** ($1,00 ogni 1.000) |
| Standard | $375 | 500.000 | $0,00075 |
| Scale | $1.250 | 2,5 milioni | $0,0005 |

I crediti acquistati valgono **6 mesi**. Una query fino a 10 risultati costa 1 credito
(FlipFinder chiede sempre 10 risultati; 11-100 risultati costerebbero 2 crediti). Se una risposta
dichiara più crediti (`"credits"`), il budget conta quelli reali.

## Consumo previsto

Ogni modello costa 2 query, 3 quando si cercano anche le vendite concluse, una volta ogni
`EXTERNAL_REFRESH_DAYS` giorni e solo se il modello è stato visto di nuovo.

| Modelli visti in un mese | Query al mese (≈ 2,5 per modello, ciclo 30 giorni) | Valore a $0,001/query | Durata dei 2.500 crediti gratuiti |
|------|------|------|------|
| 50 | ~125 | ~$0,13 | ~20 mesi |
| 150 | ~375 | ~$0,38 | ~6-7 mesi |
| 300 | ~750 | ~$0,75 | ~3 mesi |
| 360+ | 900 (tetto di default) | $0,90 | ~2,8 mesi |

La durata dei crediti gratuiti non è indicata sul sito; quelli acquistati valgono 6 mesi.

**Costo reale dopo i crediti gratuiti.** Serper non vende query singole: il pacchetto più piccolo è
Starter, $50 per 50.000 query, valido 6 mesi. Con i consumi della tabella (al massimo 900 query al
mese, cioè 5.400 in 6 mesi) se ne usa una piccola parte, quindi la spesa effettiva è il pacchetto:
**$50 ogni 6 mesi (circa $8,30 al mese)**, finché resti sotto le 50.000 query. Il costo per query
($0,001) conta solo se un giorno i consumi salissero molto.

Il numero esatto per la tua installazione è nella web app, **Settings → Price data**
(`expected_monthly_queries` di `GET /api/v1/pricing/evidence`): modelli visti negli ultimi 30 giorni
× query medie per modello misurate × (30 / `EXTERNAL_REFRESH_DAYS`). Con i valori di default il
consumo massimo è **900 query al mese** (valore $0,90; vedi sopra il costo reale del pacchetto).

## Budget e impostazioni

| Variabile | Default | Significato |
|-----------|---------|-------------|
| `EXTERNAL_SEARCH_PROVIDER` | `none` | `serper` per attivare, `none` = spento |
| `SERPER_API_KEY` | vuota | chiave dell'API (mai scritta nei log né nei messaggi di errore) |
| `EXTERNAL_SEARCH_MONTHLY_BUDGET` | `900` | query massime nel mese (UTC) |
| `EXTERNAL_SEARCH_DAILY_MAX` | `60` | query massime nel giorno (UTC) |
| `EXTERNAL_REFRESH_DAYS` | `30` | giorni prima di ricercare un modello |
| `EXTERNAL_SEARCH_COUNTRY` / `_LANGUAGE` | `it` / `it` | paese e lingua dei risultati Google |
| `EXTERNAL_SEARCH_SOLD_QUERY` | `true` | terza query per le vendite concluse |

Il budget è contato **prima** di ogni chiamata (in `system_state`, chiave
`external_search_budget`): una query che supererebbe il tetto giornaliero o mensile non parte.
Un modello parte solo se restano almeno 2 query; altrimenti resta in coda con stato
`skipped_budget` e verrà cercato al giro successivo con budget disponibile.

Errori:

- **401/403** (chiave rifiutata), **402** o "Not enough credits" (crediti finiti), **429** (troppe
  richieste): il giro si ferma subito, l'errore è in `last_error` dello stato; i modelli restano in
  coda senza penalità.
- **5xx** o rete: un nuovo tentativo; se fallisce ancora il modello va in `error` e viene riprovato
  dopo 6 ore, poi 12, 24... (mai oltre `EXTERNAL_REFRESH_DAYS`). Dopo 3 modelli falliti di fila il
  giro si ferma.

## Come ottenere la chiave

1. Registrati su [serper.dev](https://serper.dev) (email o account Google): ricevi 2.500 crediti
   gratuiti, senza carta.
2. Nella dashboard apri **API Key** e copia la chiave.
3. Nel file `.env` (o `.env.production`):

   ```
   EXTERNAL_SEARCH_PROVIDER=serper
   SERPER_API_KEY=la-tua-chiave
   ```

4. Riavvia backend e worker (`docker compose up -d`).
5. Prova un modello senza salvare nulla (spende 2-3 crediti):

   ```
   docker compose exec backend python -m app.tools.external_prices --brand nike --model "Air Max 90" --dry-run
   ```

   Stampa ogni risultato tenuto (tipo, fonte, prezzo, cambio, data, condizione) e ogni risultato
   scartato con il motivo. Senza `--dry-run` salva i prezzi. Altri comandi: `--status` (budget,
   cache, ultimo giro), `--due` (aggiorna subito i modelli in coda), `--json`.

Quando i crediti gratuiti finiscono: acquista il pacchetto Starter ($50, 50.000 query, validi 6
mesi) oppure lascia che il giro si fermi da solo (errore "Crediti Serper esauriti"): nulla si
rompe, le analisi usano gli altri dati.

## Come il confronto scarta gli articoli sbagliati

Un risultato è tenuto solo se è **esattamente quel modello, un solo articolo, per adulti,
originale**. I controlli, in ordine (il primo che fallisce dà il motivo, contato per modello):

| Motivo | Regola | Esempi scartati per *Nike Air Max 90* |
|--------|--------|------------------------------|
| `brand` | il marchio (o un suo alias) deve comparire nel titolo o nell'estratto; basta il nome del modello solo se è distintivo | "Adidas Samba OG" |
| `other_model` | nessun altro modello dello stesso marchio (dalla tassonomia), nessun numero diverso della stessa famiglia, nessuna collaborazione o altro marchio | "Air Max 95", "Air Max 270", "Air Max 90 / Air Max 1", "Air Max 90 Off-White", "Nike x Stüssy" |
| `model` | tutte le parole del modello (o un suo alias della tassonomia, es. "AM90") nel titolo | "Nike Revolution 6" |
| `replica` | replica, fake, falso, AAA, 1:1, imitazione, non originale, ispirato/inspired, copia, custom made, "stile/tipo/like" + marchio o modello (le negazioni come "non replica" non contano) | "Air Max 90 replica 1:1", "Scarpe stile Air Max 90" |
| `kids` | bambino/a, bimbo, kids, junior, boys, girls, enfant, Kinder, niño, toddler, baby (non "baby blue"), neonato, età ("6 anni", "12y", "y/o"), GS/PS/TD, scarpe sotto il 35 EU, abbigliamento "taglia 8-16" | "Air Max 90 GS", "taglia 33", "EU 28.5", "5.5Y" |
| `lot` | lotto, lotti, stock (non "in stock"), bundle, set di, pack da N, x2, "2 paia/pezzi", lot of, kit (tranne maglie da calcio) | "Lotto 3 paia", "Set di 2 paia" |
| `accessory` | lacci, solette, scatola vuota/solo scatola, adesivi, toppe, portachiavi, charms (non "con lacci extra") | "Lacci di ricambio per Air Max 90" |
| `not_item` | pagina di ricerca/categoria invece di un articolo | eBay `/b/...` "Da EUR 45,00" |
| `source` | Vinted | vinted.it |
| `no_price`, `currency`, `price` | prezzo assente; valuta fuori tabella (es. A$, kr); prezzo sotto 5 € o sopra 10.000 € | "A$199.00" |
| `outlier` | dopo il salvataggio: prezzo implausibile per il modello (outlier in scala logaritmica, per tipo, con le condizioni normalizzate); la riga resta ma non è usata | un "Air Max 90" usato a 950 € |

I conteggi sono salvati per modello (`external_searches.results.rejected`) e sommati nello stato
(`rejected`), così si vede quanto rumore viene filtrato. `--dry-run` mostra ogni scarto con la
parola che lo ha causato.

## Lettura dei risultati

- **Prezzo**: dal campo strutturato (`price` di Shopping, `price`/`attributes` del risultato web),
  altrimenti dal titolo, altrimenti dall'estratto, ignorando spese di spedizione, prezzi di listino
  e prezzi "da ..." (pagine con più articoli). Formati: "59,99 €", "€ 59.99", "EUR 45,00", "£40",
  "$45", "US $45.00", "CHF 80.-", "120 zł", migliaia con punto o virgola.
- **Valuta**: EUR, USD, GBP, CHF, PLN, SEK, DKK, CZK convertite con la tabella fissa di
  `backend/app/external/fx.py` (tassi BCE del 7/10/2026; tasso e data salvati in `match.fx` di
  ogni riga). Altre valute: scartate.
- **Data**: per le vendite la data accanto all'indicazione di vendita ("Venduto il 12 set 2026",
  "Sold Sep 20, 2026", "Vendu le 3 sept. 2026", "Verkauft am 3. Sep. 2026", "Vendido el 5 ago
  2026"); altrimenti la data del risultato ("28 set 2026", "3 giorni fa", "3 days ago", "il y a 3
  jours", "vor 3 Tagen", "hace 3 días"). Senza data: `source_date` vuota e vale il giorno della
  ricerca (`observed_at`).
- **Venduto**: "Venduto/i/a/e", "Sold", "Vendu", "Verkauft", "Vendido" seguiti da data,
  punteggiatura o fine testo; non contano "Venduto da" (il venditore), "Sold out", "più venduti",
  "20 venduti" (quantità di un negozio), "venduto con scatola".
- **Tipo**: venduto se la pagina lo dice; in vendita se è un mercato dell'usato o una piattaforma
  di rivendita (StockX, GOAT, Klekt...); nuovo se è un negozio, salvo che il risultato dica usato.
- **Condizione**: quella indicata (nuovo con/senza cartellino, ottime/buone/discrete condizioni,
  "come nuovo", "mai indossato"...), `used` se dice solo "usato/pre-owned", `new_with_tags` per i
  prezzi da nuovo dei negozi.
- **Doppioni**: chiave `sha256(tipo|url|prezzo)`: la stessa pagina allo stesso prezzo è salvata una
  sola volta e conserva la data della prima osservazione; se la stessa pagina passa da "in
  vendita" a "venduto" diventa una nuova riga di tipo `sold`.

## Come vengono usati

- Le vendite concluse esterne diventano righe di `sold_sales` con fonte `external_sold`
  (affidabilità 4, prezzo "riportato"), subito dopo ogni giro.
- Pesi nelle stime: tue vendite 5,0; tuoi acquisti 1,5 e venduti Vinted 1,5; venduti su altri
  mercati 1,0; annunci Vinted 1,0 e annunci di altri mercati 0,5 (questi due per il fattore di
  attività dell'annuncio). I prezzi da nuovo sono solo un riferimento mostrato nell'analisi.
- Il backtest (`python -m app.tools.price_eval`) misura l'errore delle stime con e senza i dati
  esterni e li tiene solo se non peggiorano l'errore medio.
- Ogni analisi mostra da dove viene ogni numero, con fonte, data, tipo e link dei prezzi esterni
  considerati.

## Limiti

- Google indicizza solo una parte degli annunci e delle vendite: per molti modelli le vendite
  concluse esterne saranno poche o nessuna.
- L'estratto di Google può essere vecchio rispetto alla pagina: per questo si salva la data
  dichiarata quando c'è e i prezzi implausibili diventano outlier.
- I cambi sono fissi: aggiornare a mano `fx.py` se si muovono di qualche punto percentuale.

## Fonti

- Serper, prezzi e prova gratuita (2.500 query senza carta; Starter $50 per 50.000 crediti,
  $1,00/1.000; crediti validi 6 mesi): https://serper.dev — verificato il 7/10/2026.
- Costo per numero di risultati (fino a 10 = 1 credito, 11-100 = 2 crediti):
  https://apiserpent.com/blog/serper-pricing-credits-explained — consultato il 7/10/2026.
- Formato di richiesta (POST `https://google.serper.dev/<tipo>`, intestazione `X-API-KEY`, corpo
  `q`, `gl`, `hl`, `num`): client `GoogleSerperAPIWrapper` di LangChain.
- Tassi di cambio di riferimento dell'euro, BCE, 7/10/2026:
  https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml
