# syntax=docker/dockerfile:1
# FlipFinder backend image: FastAPI API and arq workers share it (role chosen by the command).
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (cached layer): install the project from pyproject.toml with a stub
# package, then drop the stub so the real source below is the only copy of `app`.
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py \
    && pip install . \
    && pip uninstall -y flipfinder-backend \
    && rm -rf app build ./*.egg-info

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker/entrypoint.sh /usr/local/bin/flipfinder
RUN chmod 0755 /usr/local/bin/flipfinder \
    && useradd --uid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin flipfinder \
    && chown -R flipfinder /app

USER flipfinder
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=6 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4); sys.exit(0)" || exit 1

ENTRYPOINT ["flipfinder"]
CMD ["api"]
