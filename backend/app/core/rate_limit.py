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
    # X-Forwarded-For is honoured only when the API sits behind our own proxy (Next.js / nginx);
    # disable TRUST_PROXY_HEADERS when the backend is exposed directly.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and get_settings().trust_proxy_headers:
        return forwarded.split(",")[0].strip()
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
