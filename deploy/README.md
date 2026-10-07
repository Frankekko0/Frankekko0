# FlipFinder in produzione

Un computer sempre acceso (mini PC, NAS, VPS) con Docker. Risultato: FlipFinder a un indirizzo
HTTPS fisso, accessibile solo a te, con backup automatici e registro errori.

## 0. Gratis in 20 minuti (consigliato)

Server **Oracle Cloud Always Free** (gratis per sempre, 4 CPU ARM e 24 GB di RAM) + sottodominio
gratuito **DuckDNS** + HTTPS automatico. Il risultato si installa sul telefono come un'app.

1. Registrati su <https://www.oracle.com/cloud/free/> (serve una carta solo per la verifica,
   non viene addebitato nulla se resti nelle risorse "Always Free").
2. *Compute → Instances → Create*: immagine **Ubuntu 24.04**, forma **VM.Standard.A1.Flex**
   (es. 2 OCPU, 12 GB). Scarica la chiave SSH. Annota l'**IP pubblico**.
3. *Networking → VCN → Security List*: aggiungi regole Ingress TCP per le porte **80** e **443**
   da `0.0.0.0/0`.
4. Su <https://www.duckdns.org> accedi, crea un sottodominio (es. `flip-mario`) e inserisci
   l'IP pubblico del server.
5. Collegati e installa:
   ```bash
   ssh -i chiave.key ubuntu@IP_PUBBLICO
   git clone https://github.com/frankekko0/frankekko0.git flipfinder && cd flipfinder
   bash deploy/install.sh flip-mario.duckdns.org
   ```
6. Apri `https://flip-mario.duckdns.org`, crea il tuo account (le registrazioni poi si
   chiudono), poi dal telefono: **Condividi → Aggiungi a schermata Home** (iPhone) o
   **⋮ → Installa app** (Android).

Se il repository è privato, per il `git clone` usa un token GitHub personale come password.

## 1. Indirizzo stabile e HTTPS

Scegli **una** delle due strade.

**A. Dominio tuo (Caddy, certificato automatico)**

1. Crea un record DNS (es. `flip.tuodominio.it`) che punta all'IP pubblico del computer.
2. Apri sul router le porte 80 e 443 verso quel computer.
3. Prosegui dal punto 2: Caddy ottiene e rinnova da solo il certificato HTTPS.

**B. Senza dominio né porte aperte (Tailscale)**

1. Installa Tailscale sul computer e sul telefono con lo stesso account.
2. Avvia lo stack *senza* Caddy (`docker compose up -d`, solo in locale) e pubblica l'app nella
   tua rete privata: `tailscale serve --bg https / http://localhost:3000`.
3. L'indirizzo è `https://<nome-computer>.<tailnet>.ts.net`, raggiungibile solo dai tuoi
   dispositivi. In `.env` imposta `ENVIRONMENT=production`, `COOKIE_SECURE=true`,
   `ALLOW_REGISTRATION=false`, `ERROR_LOG_DIR=/data/logs`, `PUBLIC_APP_URL=` quell'indirizzo e i
   segreti come al punto 2; usa i backup con `deploy/backup.sh` (vedi punto 4).

## 2. Configurazione

```bash
cp .env.production.example .env.production
# genera i due segreti:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # POSTGRES_PASSWORD
```

Compila `DOMAIN`, `JWT_SECRET`, `POSTGRES_PASSWORD` (ed eventualmente `AI_API_KEY`).
Il file è escluso da git.

## 3. Avvio

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d --build
```

Serve Docker Compose 2.24 o successivo. All'avvio, nell'ordine: database, **backup**,
migrazioni, API, worker, web app, Caddy. Apri `https://DOMAIN` e crea il tuo account: è l'unico
possibile (`ALLOW_REGISTRATION=false` chiude le registrazioni dopo il primo). L'API non è
esposta: si raggiunge solo attraverso la web app.

Se arrivi da una versione con la vecchia modalità demo, la migrazione `0007` elimina il mercato
simulato e l'account demo; il backup fatto subito prima dell'avvio è in `backups/`.

## 4. Backup

- Prima di ogni avvio (quindi prima di ogni aggiornamento e migrazione) e ogni giorno alle
  `BACKUP_HOUR_UTC`: `backups/flipfinder-<data>-<tipo>.dump`, tenuti `BACKUP_KEEP_DAYS` giorni.
- Copia la cartella `backups/` anche altrove (disco esterno, cloud): un backup sullo stesso disco
  non protegge da un guasto del disco.
- Backup manuale: `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production run --rm backup-before-start sh /backup.sh once manuale`
- Ripristino (sostituisce i dati attuali): `deploy/restore.sh backups/<file>.dump`
- Le foto archiviate sono nel volume `media`: per salvarle `docker run --rm -v flipfinder_media:/m -v "$PWD/backups":/b alpine tar czf /b/media.tgz -C /m .`

## 5. Registro errori

Avvisi ed errori dell'API e del worker vanno in `/data/logs` (rotazione a 5 MB, 3 file per
processo; password, token e chiavi oscurati) e si leggono in **Impostazioni → Error log**.
Log completi: `docker compose logs -f backend worker`.

## 6. Estensione collegata alla produzione

1. In FlipFinder → Impostazioni → *Browser extension* crea una chiave.
2. Nelle opzioni dell'estensione imposta l'indirizzo `https://DOMAIN`, incolla la chiave e
   conferma il permesso che il browser chiede per quell'indirizzo.
3. Apri una ricerca su Vinted: i badge e la migliore opportunità compaiono subito.

## 7. Aggiornare

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d --build
```

Il backup prima dell'avvio parte da solo.
