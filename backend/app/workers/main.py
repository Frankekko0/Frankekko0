"""Worker process: two arq workers (high/default queues) plus the cron schedule.

Run with ``python -m app.workers.main``. One process hosts both workers on the same event loop
so promising listings are never stuck behind the bulk queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import Any

from arq import cron
from arq.worker import Worker, func
from sqlalchemy import func as sa_func
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis
from app.db.models import Brand
from app.db.session import dispose_engine, session_scope
from app.marketplace.registry import close_provider
from app.workers import tasks
from app.workers.queue import QUEUE_DEFAULT, QUEUE_HIGH, close_queue, redis_settings

log = get_logger("app.worker")


def _functions() -> list[Any]:
    return [
        func(tasks.analyze_listing, keep_result=0, max_tries=4, timeout=120),
        func(tasks.vision_task, keep_result=0, max_tries=2, timeout=180),
        func(tasks.ai_analyze_task, keep_result=0, max_tries=2, timeout=180),
        func(tasks.deliver_alert_task, keep_result=0, max_tries=5, timeout=60),
        func(tasks.scan_new_listings, keep_result=0, max_tries=3, timeout=600),
        func(tasks.recompute_market_statistics_task, keep_result=0, max_tries=2, timeout=600),
        func(tasks.refresh_listings, keep_result=0, max_tries=2, timeout=600),
        func(tasks.recompute_learning, keep_result=0, max_tries=2, timeout=300),
        func(tasks.prune, keep_result=0, max_tries=1, timeout=300),
    ]


def _scan_seconds(interval: int) -> set[int]:
    interval = max(5, min(60, interval))
    return set(range(0, 60, interval))


def _cron_jobs() -> list[Any]:
    settings = get_settings()
    return [
        cron(
            tasks.scan_new_listings,
            second=_scan_seconds(settings.scan_interval_seconds),
            run_at_startup=True,
            timeout=600,
            unique=True,
        ),
        cron(tasks.recompute_market_statistics_task, minute={0, 15, 30, 45}, second=5, timeout=600),
        cron(tasks.refresh_listings, minute={2, 12, 22, 32, 42, 52}, second=10, timeout=600),
        cron(tasks.recompute_learning, minute={7, 37}, second=20, timeout=300),
        cron(tasks.prune, hour=3, minute=17, second=0, timeout=300),
    ]


async def _wait_for_catalog(max_wait: float = 120) -> None:
    """The API container runs migrations + seed; wait until the catalog exists."""
    deadline = asyncio.get_running_loop().time() + max_wait
    while True:
        try:
            async with session_scope() as s:
                if (await s.execute(select(sa_func.count()).select_from(Brand))).scalar_one() > 0:
                    return
        except Exception as exc:
            log.info("worker.waiting_for_database", error=type(exc).__name__)
        if asyncio.get_running_loop().time() > deadline:
            raise RuntimeError("database/catalog not ready - did migrations and seed run?")
        await asyncio.sleep(3)


def _startup(queue: str) -> Any:
    async def on_startup(ctx: dict[str, Any]) -> None:
        ctx["queue"] = queue

    return on_startup


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    await _wait_for_catalog()
    high = Worker(
        _functions(),
        queue_name=QUEUE_HIGH,
        redis_settings=redis_settings(),
        handle_signals=False,
        max_jobs=10,
        on_startup=_startup("high"),
        health_check_key="ff:health:high",
    )
    default = Worker(
        _functions(),
        queue_name=QUEUE_DEFAULT,
        cron_jobs=_cron_jobs(),
        redis_settings=redis_settings(),
        handle_signals=False,
        max_jobs=12,
        on_startup=_startup("default"),
        health_check_key="ff:health:default",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    log.info("worker.started", queues=[QUEUE_HIGH, QUEUE_DEFAULT], provider=settings.marketplace_provider)
    runners = [asyncio.create_task(high.async_run()), asyncio.create_task(default.async_run())]
    stopper = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait([*runners, stopper], return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if task is not stopper and task.exception():
            log.error("worker.crashed", error=repr(task.exception()))
    for task in runners:
        task.cancel()
    for w in (high, default):
        with contextlib.suppress(Exception):
            await w.close()
    await close_provider()
    await close_queue()
    await close_redis()
    await dispose_engine()
    log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(run())
