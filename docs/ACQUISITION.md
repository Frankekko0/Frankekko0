# Acquisizione dei dati di Vinted: modalità, valutazione e fallback

Stato: ottobre 2026. Fonti verificate il 6/10/2026 (link in fondo).

## Vincoli di partenza

- Niente aggiramento di CAPTCHA, anti-bot, rate limit o autenticazione.
- Nessuna azione automatica sull'account (acquisti, offerte, preferiti, messaggi).
- Dati del venditore: solo valutazione media e numero di recensioni.

**Modalità rimosse (decisione Q1 dell'utente, ottobre 2026).** Lo scanner automatico delle ricerche
salvate, l'approfondimento di annunci non aperti (anche su comando), l'aggiornamento in background dei
tracciati, la lettura dal server di pagine pubbliche e i clic programmatici su Preferiti e Acquista non
esistono più nel codice (il numero della tabella sotto è rimasto, per i riferimenti). Un articolo si
aggiorna quando lo riapri tu su Vinted; i record vecchi con le modalità `extension_scan`,
`extension_deep`, `extension_refresh` e `public_fetch` restano leggibili.

Fatti rilevanti:

- **Termini di Vinted** (sez. 6): vietano di usare "strumenti software esterni (bot, programmi
  di scraping, crawler, spider)" e di "fare data mining, screen scraping, crawling". Vinted può
  bloccare gli account anche con rilevamento automatico (sez. 7). Ogni modalità diversa dalla
  consultazione manuale è quindi, alla lettera, in tensione con i Termini. Nella tabella la
  colonna "Conformità" distingue quanto ciascuna si allontana dall'uso normale del sito.
- **robots.txt di vinted.it** (`User-agent: *`): consente `/` e quindi gli annunci (`/items/…`),
  ma vieta `/member/` (gli armadi dei venditori), `/checkout`, `/inbox`. Vieta tutto agli agenti
  AI (GPTBot, ClaudeBot…).
- **Vinted Pro Integrations** (API ufficiale): solo per *"un insieme limitato di aziende Vinted
  Pro in allowlist"*. Copre i propri articoli (Items API), i propri ordini e i webhook. **Non
  offre ricerca nel catalogo né dati di altri venditori.**
- **Notifiche di Vinted**: arrivano (anche via email, se attive) per *preferito ribassato*,
  *preferito venduto* e *nuovi articoli dei membri seguiti*. Le **ricerche salvate non inviano
  notifiche**.

## Confronto

Legenda: ●●● alto · ●●○ medio · ●○○ basso.

| Modalità | Affidabilità | Copertura dati | Costo | Rischio di blocco | Conformità ai Termini |
|---|---|---|---|---|---|
| **1. Estensione: lettura passiva della pagina che apro** | ●●● è ciò che vedo | annuncio: tutti i campi visibili, tutte le foto, stato; catalogo/ricerca/armadio/preferiti: le card caricate | 0 | nessuno: zero richieste aggiuntive | ●●○ legge solo pagine aperte da me; resta uno "strumento esterno" |
| **3. Bookmarklet** | ●●○ | singolo annuncio (dati strutturati) | 0 | nessuno | ●●○ come 1 |
| **4. Import manuale di link (anche elenchi)** | ●●● per ID e link; il resto arriva con le altre modalità | ID Vinted, URL, titolo dal link; poi arricchimento | 0 | nessuno | ●●● |
| **5. Dati strutturati nel sorgente** (JSON-LD, meta, JSON incorporato) | ●●● i formati SEO cambiano raramente | titolo, prezzo, valuta, foto, brand, disponibilità; in parte preferiti, stato | — | — | (è una tecnica di lettura usata dalla 1) |
| **7. Email di notifica** | ●●● per gli eventi coperti | link all'articolo, titolo, a volte prezzo; **"venduto" certo** per i preferiti | 0 | nessuno: è la tua casella | ●●● |
| **8. Vinted Pro Integrations** | ●●● | solo i tuoi articoli e ordini, niente mercato | account Pro + approvazione | nessuno | ●●● |
| **9. Fornitori terzi** (attori Apify, Lobstr, ScrapeBadger…) | ●●○ | catalogo, ricerche | a consumo | il rischio è loro, ma il servizio può sparire | ●○○ ottengono i dati con lo scraping; nessuno risulta autorizzato da Vinted. Licenza vera: non trovata |

## Cosa è implementato e in che ordine viene usato

Ogni volta che serve leggere o aggiornare un articolo, l'orchestratore prova le modalità **attive**
in questo ordine e registra quale ha prodotto il dato (`acquisition_mode` su ogni snapshot e su
ogni tentativo, riuscito o fallito):

1. **Feed autorizzato** (`MARKETPLACE_PROVIDER=feed`), se ne hai uno con licenza (modalità 8/9
   con un contratto vero): adapter già presente.
2. **Estensione, lettura passiva** (1): sempre attiva quando l'estensione è installata. Ogni
   annuncio o card che vedo diventa uno snapshot.
3. **Email** (7): opzionale. Casella IMAP in sola lettura (credenziali solo da variabili
   d'ambiente) oppure caricamento manuale di file `.eml`. "Preferito venduto" conferma la vendita;
   "ribassato" aggiunge uno snapshot di prezzo.
4. **Manuale** (4): link o elenchi di link, modulo *Analyze a listing*, bookmarklet (3).

Non implementate: **Vinted Pro** (non dà dati di mercato; si può aggiungere se diventi venditore
Pro per gestire i tuoi annunci) e i **fornitori terzi** specifici (nessuno con licenza: se ne
scegli uno, si collega al feed generico del punto 1).

Senza le modalità opzionali, gli articoli che non riapri restano con l'ultimo stato noto e la data
dell'ultimo controllo ben visibile. Il sistema non inventa aggiornamenti.

## Fonti

- [Vinted Pro Integrations: documentazione ufficiale](https://pro-docs.svc.vinted.com/)
- [Termini e condizioni Vinted (sez. 6–7)](https://www.vinted.com/old-terms-and-conditions)
- [robots.txt di vinted.it](https://www.vinted.it/robots.txt)
- [Vinted, gestione delle notifiche](https://www.vinted.com/help/433-how-notifications-work)
- [Nuovi Termini Vinted del 5/10/2026 (analisi Redrip)](https://www.redrip.app/en/blog/new-vinted-terms-2026/)
- [Panoramica dei servizi di scraping Vinted (ScrapeBadger)](https://scrapebadger.com/blog/vinted-api-best-scraping-apis-compared-for-2026)
