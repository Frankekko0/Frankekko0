#!/bin/sh
# Restore a FlipFinder backup into the running stack (replaces the current data).
#   deploy/restore.sh backups/flipfinder-20261006-020000-daily.dump
set -eu
file="${1:?usage: deploy/restore.sh <backup.dump>}"
compose="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production"
$compose stop backend worker
$compose exec -T postgres sh -c 'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$file"
$compose start backend worker
echo "restored: $file"
