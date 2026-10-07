#!/usr/bin/env bash
# Installazione in un comando su un server Linux nuovo (Ubuntu/Debian, x86 o ARM).
#   bash deploy/install.sh flip-tuonome.duckdns.org
# Installa Docker se manca, apre le porte 80/443, genera i segreti, scrive .env.production
# e avvia FlipFinder con HTTPS automatico (Caddy + Let's Encrypt).
set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "Uso: bash deploy/install.sh <dominio>   (es. flip-mario.duckdns.org)" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
fi

# Le immagini Oracle Cloud bloccano 80/443 con iptables anche se la Security List le apre.
if command -v iptables >/dev/null 2>&1; then
  for p in 80 443; do
    sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null \
      || sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport "$p" -j ACCEPT
  done
  command -v netfilter-persistent >/dev/null 2>&1 && sudo netfilter-persistent save || true
fi

if [ ! -f .env.production ]; then
  secret() { python3 -c "import secrets; print(secrets.token_urlsafe(48))"; }
  cp .env.production.example .env.production
  sed -i "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" .env.production
  sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$(secret)|" .env.production
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(secret)|" .env.production
  chmod 600 .env.production
  echo ".env.production creato (segreti generati)."
fi

sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d --build
echo
echo "FlipFinder è online su https://${DOMAIN}"
echo "Aprilo dal telefono e scegli 'Aggiungi a schermata Home' per installarlo come app."
