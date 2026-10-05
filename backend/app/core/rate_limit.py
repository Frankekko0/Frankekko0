"""Fixed-window rate limiting backed by Redis.

Used as a FastAPI dependency. Keys combine the client identity (user id when authenticated,
otherwise client IP) and a bucket name, so login attempts are limited independently from
regular API traffic. If Redis is down the limiter fails open (availability over strictness)
and logs a warning.
"""

from __future__ import annotations

import time

from fastapi import Request
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.core.logging import get_logger
from app.core.redis import get_redis

log = get_logger(__name__)


def client_ip(request: Request) -> str:
    """Client address for rate limiting.

    Behind our own proxies (Next.js rewrite, optionally nginx) each hop *appends* the address it
    received the request from, so only the rightmost ``TRUSTED_PROXY_HOPS`` entries of
    X-Forwarded-For are trustworthy; anything to their left can be forged by the client.
    Set TRUST_PROXY_HEADERS=false when the backend is exposed directly.
    """
    settings = get_settings()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and settings.trust_proxy_headers:
        hops = [part.strip() for part in forwarded.split(",") if part.strip()]
        if hops:
            return hops[max(0, len(hops) - settings.trusted_proxy_hops)]
    return request.client.host if request.client else "unknown"


async def hit(key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
    """Register a hit; returns (allowed, current_count)."""
    bucket = int(time.time() // window_seconds)
    redis_key = f"ff:rl:{key}:{bucket}"
    try:
        redis = get_redis()
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds + 1)
            count, _ = await pipe.execute()
    except RedisError as exc:
        log.warning("rate_limit.redis_unavailable", error=str(exc))
        return True, 0
    return int(count) <= limit, int(count)


class RateLimit:
    """Dependency factory: ``Depends(RateLimit("auth", per_minute=10))``."""

    def __init__(self, bucket: str, per_minute: int | None = None) -> None:
        self.bucket = bucket
        self.per_minute = per_minute

    async def __call__(self, request: Request) -> None:
        settings = get_settings()
        if settings.environment == "test" and not request.headers.get("x-test-rate-limit"):
            return
        limit = self.per_minute or settings.rate_limit_per_minute
        identity = getattr(request.state, "user_id", None) or client_ip(request)
        allowed, _ = await hit(f"{self.bucket}:{identity}", limit)
        if not allowed:
            raise RateLimitedError()
