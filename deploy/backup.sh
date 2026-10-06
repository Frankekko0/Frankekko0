#!/bin/sh
# PostgreSQL backups for FlipFinder (runs in the postgres image; PG* variables set by compose).
#   backup.sh once <label>   one backup now (e.g. before starting a new version)
#   backup.sh daily          one backup every day at BACKUP_HOUR_UTC, forever
# Files: /backups/flipfinder-<date>-<label>.dump (pg_dump custom format), older than
# BACKUP_KEEP_DAYS days removed. Restore: deploy/restore.sh <file>.
set -eu
KEEP="${BACKUP_KEEP_DAYS:-14}"

backup() {
    label="$1"
    file="/backups/flipfinder-$(date -u +%Y%m%d-%H%M%S)-${label}.dump"
    pg_dump --format=custom --no-owner --file="${file}.part"
    mv "${file}.part" "$file"
    find /backups -name 'flipfinder-*.dump' -mtime +"$KEEP" -delete
    echo "backup written: $file ($(du -h "$file" | cut -f1))"
}

case "${1:-once}" in
    once)
        backup "${2:-manual}"
        ;;
    daily)
        while true; do
            now=$(date -u +%s)
            next=$(date -u -d "$(date -u +%Y-%m-%d) ${BACKUP_HOUR_UTC:-2}:00:00" +%s 2>/dev/null || echo $((now + 86400)))
            [ "$next" -le "$now" ] && next=$((next + 86400))
            sleep $((next - now))
            backup daily || echo "backup failed" >&2
        done
        ;;
    *)
        echo "usage: backup.sh once <label> | daily" >&2
        exit 2
        ;;
esac
