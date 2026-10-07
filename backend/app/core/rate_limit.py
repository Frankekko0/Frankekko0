"""Sliding-window rate limiting backed by Redis.

Used as a FastAPI dependency. Keys combine the client identity (user id when authenticated,
otherwise client IP) and a bucket name, so login attempts are limited independently from
regular API traffic. The window slides: at most ``limit`` requests are accepted in *any* 60
seconds, also across a minute boundary (a fixed window would let twice the limit through
there). Refused requests are not counted, so the stored log never exceeds ``limit`` entries.
If Redis is down the limiter fails open (availability over strictness) and logs a warning.
"""

from __future__ import annotations

import math
import time
import uuid

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


# Atomic check-and-record on a sorted set of accepted request times (score = time in ms).
_SLIDING_WINDOW = """
local key, now, window, limit, member = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
redis.call('PEXPIRE', key, window)
if count < limit then
  redis.call('ZADD', key, now, member)
  return {1, count + 1, 0}
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
return {0, count, tonumber(oldest[2]) + window - now}
"""


async def hit(key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int, int]:
    """Register a request; returns (allowed, requests in the window, seconds until one is allowed)."""
    now_ms = int(time.time() * 1000)
    try:
        args = (str(now_ms), str(window_seconds * 1000), str(limit), f"{now_ms}:{uuid.uuid4().hex[:8]}")
        allowed, count, wait_ms = await get_redis().eval(_SLIDING_WINDOW, 1, f"ff:rl:{key}", *args)  # type: ignore[misc]
    except RedisError as exc:
        log.warning("rate_limit.redis_unavailable", error=str(exc))
        return True, 0, 0
    return bool(int(allowed)), int(count), max(1, math.ceil(int(wait_ms) / 1000)) if not int(allowed) else 0


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
        allowed, _, retry_after = await hit(f"{self.bucket}:{identity}", limit)
        if not allowed:
            raise RateLimitedError(retry_after=retry_after)
