"""Shared async Redis client."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis

from app.core.config import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        _client = Redis.from_url(get_settings().redis_url, decode_responses=True, health_check_interval=30)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


@asynccontextmanager
async def redis_lock(name: str, ttl_seconds: int) -> AsyncIterator[bool]:
    """Best-effort distributed lock (SET NX EX). Yields False when someone else holds it.

    The TTL bounds how long a crashed holder can block others; release only deletes the
    key if this holder still owns it.
    """
    key, token = f"ff:lock:{name}", secrets.token_hex(16)
    redis = get_redis()
    acquired = bool(await redis.set(key, token, nx=True, ex=ttl_seconds))
    try:
        yield acquired
    finally:
        if acquired:
            await redis.eval(_RELEASE, 1, key, token)  # type: ignore[misc]
