#!/bin/sh
# Container entrypoint. Roles:
#   api      apply migrations + idempotent seed (brand and category catalog), then serve the API
#   worker   scanner, analysis queues, alerts and scheduled jobs (waits for the seed)
#   migrate  only apply migrations + seed
# Any other command is executed as-is (e.g. `pytest`, `python -m app.seed`).
set -eu

migrate() {
    alembic upgrade head
    python -m app.seed
}

case "${1:-api}" in
    api)
        if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
            migrate
        fi
        exec uvicorn app.main:app \
            --host 0.0.0.0 \
            --port "${PORT:-8000}" \
            --workers "${WEB_CONCURRENCY:-2}" \
            --no-server-header
        ;;
    worker)
        exec python -m app.workers.main
        ;;
    migrate)
        migrate
        ;;
    *)
        exec "$@"
        ;;
esac
