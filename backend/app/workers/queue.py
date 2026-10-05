"""Job queue helpers (arq on Redis) shared by the API and the workers.

Two queues give promising listings priority: ``high`` (pre-scored likely deals, price drops,
alert deliveries) and ``default`` (everything else). Job ids make enqueueing idempotent: the same
listing cannot be queued twice while a previous analysis is pending.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

QUEUE_HIGH = "ff:queue:high"
QUEUE_DEFAULT = "ff:queue:default"

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_queue() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


def set_queue(pool: ArqRedis | None) -> None:
    global _pool
    _pool = pool


async def close_queue() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


def backoff_seconds(job_try: int, base: float = 2.0, cap: float = 300.0) -> float:
    """Exponential backoff: 2s, 4s, 8s, ... capped."""
    return min(cap, base ** max(1, job_try))


async def enqueue(
    function: str,
    *args: Any,
    high: bool = False,
    job_id: str | None = None,
    defer_seconds: float | None = None,
    pool: ArqRedis | None = None,
) -> bool:
    queue = pool or await get_queue()
    job = await queue.enqueue_job(
        function,
        *args,
        _job_id=job_id,
        _queue_name=QUEUE_HIGH if high else QUEUE_DEFAULT,
        _defer_by=timedelta(seconds=defer_seconds) if defer_seconds else None,
    )
    return job is not None


async def queue_lengths() -> dict[str, int]:
    queue = await get_queue()
    return {"high": await queue.zcard(QUEUE_HIGH), "default": await queue.zcard(QUEUE_DEFAULT)}
