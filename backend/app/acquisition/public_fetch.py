"""Opt-in, polite server-side read of public Vinted item pages.

Off by default (``VINTED_PUBLIC_FETCH_ENABLED=false``): Vinted's terms forbid automated
collection, so turning this on is the user's choice. When on, it behaves like a well-mannered
reader and never tries to get around a refusal:

* only ``https://www.vinted.<tld>/items/<id>`` pages, and only if robots.txt allows them;
* an honest User-Agent, no cookies, no login, no proxy rotation, no headless browser;
* at most one request every ``min_interval`` seconds (>= 30) and ``daily_cap`` per day,
  shared by every worker process (Redis);
* a cache of ``cache_hours``: the same page is not read twice in that window;
* any sign of a block (403, 429, CAPTCHA/anti-bot page) opens a circuit breaker: reading stops
  for ``block_pause_hours`` and the other acquisition modes take over.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from urllib import robotparser
from urllib.parse import urlparse

import httpx
from redis.asyncio import Redis

from app.acquisition.identity import VINTED_HOST
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.redis import get_redis

log = get_logger(__name__)

PREFIX = "ff:vfetch"
CHALLENGE_MARKERS = (
    "datadome",
    "captcha-delivery",
    "geo.captcha",
    "cf-chl",
    "challenge-platform",
    "just a moment",
    "attention required",
    "please enable js",
    "verify you are human",
)
MAX_BYTES = 3 * 1024 * 1024


class FetchOutcome(StrEnum):
    OK = "ok"
    CACHED = "cached"
    NOT_FOUND = "not_found"
    BLOCKED = "blocked"
    ERROR = "error"
    # Not attempted:
    DISABLED = "disabled"
    DISALLOWED = "disallowed"
    PAUSED = "paused"
    WAIT = "wait"
    CAP_REACHED = "cap_reached"
    INVALID = "invalid"

    @property
    def attempted(self) -> bool:
        return self in (FetchOutcome.OK, FetchOutcome.NOT_FOUND, FetchOutcome.BLOCKED, FetchOutcome.ERROR)


@dataclass(frozen=True)
class FetchResult:
    outcome: FetchOutcome
    message: str
    http_status: int | None = None
    html: str | None = None
    retry_after: int | None = None  # seconds, for WAIT / PAUSED

    @property
    def has_page(self) -> bool:
        return self.html is not None and self.outcome in (FetchOutcome.OK, FetchOutcome.CACHED)


def user_agent(settings: Settings) -> str:
    return f"FlipFinder/1.0 (+{settings.vinted_public_fetch_contact})"


def canonical_item_url(url: str) -> tuple[str, str] | None:
    """``(https url without query, vinted id)`` for a Vinted item page, else None."""
    if not VINTED_HOST.match(url):
        return None
    parsed = urlparse(url)
    parts = parsed.path.split("/")
    if len(parts) < 3 or parts[1] != "items":
        return None
    vid = parts[2].split("-")[0]
    if not vid.isdigit():
        return None
    return f"https://{parsed.hostname}{parsed.path}", vid


class PublicPageFetcher:
    def __init__(
        self,
        settings: Settings | None = None,
        redis: Redis | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.s = settings or get_settings()
        self.redis = redis or get_redis()
        self.transport = transport

    # ---------------------------------------------------------------- state
    async def status(self) -> dict[str, object]:
        today = datetime.now(UTC).strftime("%Y%m%d")
        paused_ttl = await self.redis.ttl(f"{PREFIX}:paused")
        used = int(await self.redis.get(f"{PREFIX}:day:{today}") or 0)
        reason = await self.redis.get(f"{PREFIX}:paused")
        return {
            "enabled": self.s.vinted_public_fetch_enabled,
            "paused_for_seconds": paused_ttl if paused_ttl and paused_ttl > 0 else 0,
            "pause_reason": reason.decode() if isinstance(reason, bytes) else reason,
            "used_today": used,
            "daily_cap": self.s.vinted_public_fetch_daily_cap,
            "min_interval_seconds": self.s.vinted_public_fetch_min_interval_seconds,
        }

    async def _pause(self, reason: str) -> None:
        await self.redis.set(
            f"{PREFIX}:paused", reason, ex=self.s.vinted_public_fetch_block_pause_hours * 3600
        )
        log.warning("public_fetch.paused", reason=reason, hours=self.s.vinted_public_fetch_block_pause_hours)

    # ---------------------------------------------------------------- robots.txt
    async def _robots_allows(self, client: httpx.AsyncClient, host: str, url: str) -> bool | None:
        """True/False from robots.txt (cached 24 h); None when it cannot be read (not fetched)."""
        key = f"{PREFIX}:robots:{host}"
        body = await self.redis.get(key)
        if body is None:
            try:
                resp = await client.get(f"https://{host}/robots.txt")
            except httpx.HTTPError:
                return None
            if resp.status_code >= 500 or resp.status_code in (401, 403, 429):
                return None
            body = resp.text if resp.status_code == 200 else ""
            await self.redis.set(key, body, ex=24 * 3600)
        text = body.decode() if isinstance(body, bytes) else body
        rp = robotparser.RobotFileParser()
        rp.parse(text.splitlines())
        return rp.can_fetch(user_agent(self.s), url)

    # ---------------------------------------------------------------- fetch
    async def fetch_item(self, url: str) -> FetchResult:
        s = self.s
        if not s.vinted_public_fetch_enabled:
            return FetchResult(
                FetchOutcome.DISABLED,
                "Lettura dal server disattivata (VINTED_PUBLIC_FETCH_ENABLED=false): l'articolo "
                "verrà aggiornato quando lo apri con l'estensione.",
            )
        canon = canonical_item_url(url)
        if canon is None:
            return FetchResult(FetchOutcome.INVALID, "Indirizzo non riconosciuto come annuncio Vinted.")
        page_url, vid = canon
        cached = await self.redis.get(f"{PREFIX}:cache:{vid}")
        if cached is not None:
            html = cached.decode() if isinstance(cached, bytes) else cached
            return FetchResult(FetchOutcome.CACHED, "Pagina letta di recente (cache).", 200, html)
        paused = await self.redis.ttl(f"{PREFIX}:paused")
        if paused and paused > 0:
            return FetchResult(
                FetchOutcome.PAUSED,
                f"Lettura dal server sospesa per altri {paused // 60} minuti dopo un blocco di Vinted.",
                retry_after=paused,
            )
        # One request every min_interval seconds across all processes.
        interval_ms = s.vinted_public_fetch_min_interval_seconds * 1000
        if not await self.redis.set(f"{PREFIX}:last", str(time.time()), nx=True, px=interval_ms):
            wait = max(1, (await self.redis.pttl(f"{PREFIX}:last")) // 1000)
            return FetchResult(
                FetchOutcome.WAIT, f"Prossima lettura possibile tra {wait} secondi.", retry_after=wait
            )
        today = datetime.now(UTC).strftime("%Y%m%d")
        day_key = f"{PREFIX}:day:{today}"
        used = await self.redis.incr(day_key)
        if used == 1:
            await self.redis.expire(day_key, 2 * 86400)
        if used > s.vinted_public_fetch_daily_cap:
            return FetchResult(
                FetchOutcome.CAP_REACHED,
                f"Limite giornaliero di {s.vinted_public_fetch_daily_cap} letture raggiunto: si riprende domani.",
            )

        host = urlparse(page_url).hostname or ""
        headers = {
            "User-Agent": user_agent(s),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "it,en;q=0.8",
        }
        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=15, follow_redirects=True, max_redirects=3, transport=self.transport
            ) as client:
                allowed = await self._robots_allows(client, host, page_url)
                if allowed is False:
                    return FetchResult(
                        FetchOutcome.DISALLOWED, "robots.txt di Vinted non consente questa pagina."
                    )
                if allowed is None:
                    return FetchResult(
                        FetchOutcome.ERROR, "robots.txt di Vinted non leggibile: lettura non effettuata."
                    )
                resp = await client.get(page_url)
        except httpx.HTTPError as exc:
            return FetchResult(FetchOutcome.ERROR, f"Vinted non raggiungibile: {type(exc).__name__}.")

        final_host = resp.url.host or ""
        if final_host != host:
            return FetchResult(
                FetchOutcome.ERROR, "Reindirizzamento fuori da Vinted: pagina scartata.", resp.status_code
            )
        body = resp.text[:MAX_BYTES]
        lowered = body[:20000].lower()
        challenged = any(m in lowered for m in CHALLENGE_MARKERS) or "x-datadome" in {
            k.lower() for k in resp.headers
        }
        if resp.status_code in (403, 429) or (resp.status_code in (200, 503) and challenged):
            reason = f"HTTP {resp.status_code}" + (" con verifica anti-bot" if challenged else "")
            await self._pause(reason)
            return FetchResult(
                FetchOutcome.BLOCKED,
                f"Vinted ha rifiutato la lettura automatica ({reason}). Lettura dal server sospesa per "
                f"{s.vinted_public_fetch_block_pause_hours} ore, nessun tentativo di aggiramento.",
                resp.status_code,
            )
        if resp.status_code in (404, 410):
            return FetchResult(
                FetchOutcome.NOT_FOUND, "Annuncio non più disponibile su Vinted.", resp.status_code
            )
        if resp.status_code != 200:
            return FetchResult(
                FetchOutcome.ERROR,
                f"Risposta inattesa da Vinted (HTTP {resp.status_code}).",
                resp.status_code,
            )
        await self.redis.set(f"{PREFIX}:cache:{vid}", body, ex=s.vinted_public_fetch_cache_hours * 3600)
        return FetchResult(FetchOutcome.OK, "Pagina letta.", 200, body)
