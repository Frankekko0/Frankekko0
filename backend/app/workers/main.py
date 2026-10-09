"""Worker process: two arq workers (high/default queues) plus the cron schedule.

Run with ``python -m app.workers.main``. One process hosts both workers on the same event loop
so promising listings are never stuck behind the bulk queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import os
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
from app.db.session import dispose_engine, session_scope, set_pool_limits
from app.external.jobs import refresh_external_prices_task
from app.market.jobs import sync_price_evidence_task
from app.marketplace.registry import close_provider
from app.workers import tasks
from app.workers.queue import QUEUE_DEFAULT, QUEUE_HIGH, close_queue, redis_settings

log = get_logger("app.worker")


def _functions() -> list[Any]:
    return [
        func(tasks.analyze_listing, keep_result=0, max_tries=4, timeout=120),
        func(tasks.analyze_batch, keep_result=0, max_tries=4, timeout=300),
        func(tasks.vision_task, keep_result=0, max_tries=2, timeout=180),
        func(tasks.ai_analyze_task, keep_result=0, max_tries=2, timeout=180),
        func(tasks.review_candidates_task, keep_result=0, max_tries=1, timeout=300),
        func(tasks.autonomy_cycle_task, keep_result=0, max_tries=1, timeout=300),
        func(tasks.business_daily_task, keep_result=0, max_tries=1, timeout=600),
        func(tasks.deliver_alert_task, keep_result=0, max_tries=5, timeout=60),
        func(tasks.scan_new_listings, keep_result=0, max_tries=3, timeout=600),
        func(tasks.recompute_market_statistics_task, keep_result=0, max_tries=2, timeout=600),
        func(tasks.refresh_listings, keep_result=0, max_tries=2, timeout=600),
        func(tasks.mark_stale_listings_task, keep_result=0, max_tries=2, timeout=120),
        func(tasks.recompute_learning, keep_result=0, max_tries=2, timeout=300),
        func(tasks.prune, keep_result=0, max_tries=1, timeout=300),
        func(tasks.poll_email, keep_result=0, max_tries=1, timeout=300),
        func(tasks.clean_foreign_data_task, keep_result=0, max_tries=2, timeout=600),
        func(tasks.fit_price_calibration_task, keep_result=0, max_tries=2, timeout=900),
        func(sync_price_evidence_task, keep_result=0, max_tries=2, timeout=900),
        func(tasks.sync_price_evidence_full_task, keep_result=0, max_tries=2, timeout=900),
        func(refresh_external_prices_task, keep_result=0, max_tries=1, timeout=900),
    ]


def _scan_seconds(interval: int) -> set[int]:
    interval = max(5, min(60, interval))
    return set(range(0, 60, interval))


def _cron_jobs() -> list[Any]:
    settings = get_settings()
    jobs = [
        cron(tasks.recompute_market_statistics_task, minute={0, 15, 30, 45}, second=5, timeout=600),
        cron(tasks.refresh_listings, minute={2, 12, 22, 32, 42, 52}, second=10, timeout=600),
        cron(tasks.mark_stale_listings_task, minute={5, 20, 35, 50}, second=30, timeout=120, unique=True),
        cron(tasks.recompute_learning, minute={7, 37}, second=20, timeout=300),
        cron(tasks.review_candidates_task, minute={9, 39}, second=40, timeout=300, unique=True),
        cron(tasks.autonomy_cycle_task, minute={12, 42}, second=10, timeout=300, unique=True),
        cron(tasks.business_daily_task, hour=6, minute=11, second=0, timeout=600, unique=True),
        cron(tasks.prune, hour=3, minute=17, second=0, timeout=300),
        cron(tasks.clean_foreign_data_task, hour=4, minute=41, second=0, run_at_startup=True, timeout=600),
        cron(tasks.fit_price_calibration_task, hour=5, minute=23, second=0, run_at_startup=True, timeout=900),
        # Price evidence: incremental every 30 minutes and once at start (queued, the worker does
        # not wait for it), in full every night. One run at a time (lock in the task).
        cron(
            sync_price_evidence_task, minute={4, 34}, second=30, run_at_startup=True, timeout=900, unique=True
        ),
        cron(tasks.sync_price_evidence_full_task, hour=2, minute=43, second=0, timeout=900, unique=True),
        # External price references: hourly; the job itself keeps within the query budget.
        cron(refresh_external_prices_task, minute=51, second=15, timeout=900, unique=True),
    ]
    if settings.marketplace_provider == "feed":  # no source configured: nothing to scan
        jobs.append(
            cron(
                tasks.scan_new_listings,
                second=_scan_seconds(settings.scan_interval_seconds),
                run_at_startup=True,
                timeout=600,
                unique=True,
            )
        )
    if settings.email_import_enabled:
        every = settings.imap_poll_minutes
        jobs.append(
            cron(
                tasks.poll_email,
                minute=set(range(3, 60, every)) if every < 60 else {3},
                second=50,
                timeout=300,
                unique=True,
            )
        )
    return jobs


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


def process_count() -> int:
    configured = get_settings().worker_processes
    return configured if configured > 0 else max(1, min(os.cpu_count() or 1, 4))


async def run(with_cron: bool = True, index: int = 0) -> None:
    """One worker process: both queues; only the first process also runs the scheduler."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json, settings.error_log_dir, "worker")
    settings.validate_for_production()
    set_pool_limits(settings.worker_db_pool_size, settings.worker_db_max_overflow)
    await _wait_for_catalog()
    # Each job is a batch of listings, so a few concurrent jobs keep the CPU busy.
    high = Worker(
        _functions(),
        queue_name=QUEUE_HIGH,
        redis_settings=redis_settings(),
        handle_signals=False,
        max_jobs=4,
        on_startup=_startup("high"),
        health_check_key=f"ff:health:high:{index}",
    )
    default = Worker(
        _functions(),
        queue_name=QUEUE_DEFAULT,
        cron_jobs=_cron_jobs() if with_cron else None,
        redis_settings=redis_settings(),
        handle_signals=False,
        max_jobs=4,
        on_startup=_startup("default"),
        health_check_key=f"ff:health:default:{index}",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    log.info(
        "worker.started",
        process=index,
        scheduler=with_cron,
        queues=[QUEUE_HIGH, QUEUE_DEFAULT],
        provider=settings.marketplace_provider,
    )
    runners = [asyncio.create_task(high.async_run()), asyncio.create_task(default.async_run())]
    stopper = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait([*runners, stopper], return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if task is not stopper and task.exception():
            log.error("worker.crashed", process=index, error=repr(task.exception()))
    for task in runners:
        task.cancel()
    for w in (high, default):
        with contextlib.suppress(Exception):
            await w.close()
    await close_provider()
    await close_queue()
    await close_redis()
    await dispose_engine()
    log.info("worker.stopped", process=index)


def _child(index: int) -> None:
    asyncio.run(run(with_cron=False, index=index))


def main() -> None:
    """Analysis is CPU-bound Python: one process per core multiplies throughput."""
    n = process_count()
    ctx = multiprocessing.get_context("spawn")
    children = [ctx.Process(target=_child, args=(i,), name=f"flipfinder-worker-{i}") for i in range(1, n)]
    for child in children:
        child.start()
    try:
        asyncio.run(run(with_cron=True, index=0))
    finally:
        for child in children:
            child.terminate()  # SIGTERM: each child finishes its current jobs and exits
        for child in children:
            child.join(timeout=30)


if __name__ == "__main__":
    main()
