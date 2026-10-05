"""Redis JSON cache with namespaces, TTLs and cheap bulk invalidation.

Invalidation uses *namespace versions*: keys embed ``ns:v{n}``; bumping the version makes
every old key unreachable (they expire naturally) without scanning Redis. This is how the
opportunity feed cache is invalidated each time new analyses land.

The cache is best-effort: any Redis failure degrades to a cache miss, never to an error.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.core.redis import get_redis

log = get_logger(__name__)
T = TypeVar("T")

NS_FEED = "feed"
NS_MARKET = "market"
NS_ANALYTICS = "analytics"
NS_OPPORTUNITY = "opp"


def _default(o: Any) -> Any:
    from datetime import date, datetime
    from decimal import Decimal
    from enum import Enum
    from uuid import UUID

    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime | date):
        return o.isoformat()
    if isinstance(o, UUID):
        return str(o)
    if isinstance(o, Enum):
        return o.value
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    raise TypeError(f"not JSON serializable: {type(o)}")


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=_default, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class Cache:
    def __init__(self, redis: Redis | None = None, prefix: str = "ff:cache") -> None:
        self._redis = redis
        self.prefix = prefix

    @property
    def redis(self) -> Redis:
        return self._redis or get_redis()

    async def _version(self, namespace: str) -> str:
        try:
            v = await self.redis.get(f"{self.prefix}:ver:{namespace}")
        except RedisError:
            return "0"
        return v or "0"

    async def key(self, namespace: str, *parts: Any) -> str:
        version = await self._version(namespace)
        return f"{self.prefix}:{namespace}:v{version}:{stable_hash(parts)}"

    async def get_json(self, key: str) -> Any | None:
        try:
            raw = await self.redis.get(key)
        except RedisError as exc:
            log.warning("cache.get_failed", error=str(exc))
            return None
        return json.loads(raw) if raw else None

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        try:
            await self.redis.set(key, json.dumps(value, default=_default), ex=ttl_seconds)
        except RedisError as exc:
            log.warning("cache.set_failed", error=str(exc))

    async def bump(self, namespace: str) -> None:
        try:
            await self.redis.incr(f"{self.prefix}:ver:{namespace}")
        except RedisError as exc:
            log.warning("cache.bump_failed", namespace=namespace, error=str(exc))

    async def get_or_set(
        self, namespace: str, parts: tuple[Any, ...], ttl_seconds: int, loader: Callable[[], Awaitable[T]]
    ) -> T | Any:
        key = await self.key(namespace, *parts)
        cached = await self.get_json(key)
        if cached is not None:
            return cached
        value = await loader()
        serializable = json.loads(json.dumps(value, default=_default))
        await self.set_json(key, serializable, ttl_seconds)
        return serializable

    async def record_search(self, query: str) -> None:
        """Track popular searches (sorted set, decays by trimming)."""
        q = " ".join(query.lower().split())[:120]
        if not q:
            return
        try:
            await self.redis.zincrby(f"{self.prefix}:popular_searches", 1, q)
            await self.redis.zremrangebyrank(f"{self.prefix}:popular_searches", 0, -501)
        except RedisError:
            pass

    async def popular_searches(self, limit: int = 10) -> list[dict[str, Any]]:
        try:
            rows = await self.redis.zrevrange(
                f"{self.prefix}:popular_searches", 0, limit - 1, withscores=True
            )
        except RedisError:
            return []
        return [{"query": q, "count": int(score)} for q, score in rows]


cache = Cache()
