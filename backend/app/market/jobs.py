"""Worker job for the price evidence: concluded sales, negotiation discount, outliers, statistics.

Run every 30 minutes (incremental) and nightly in full; a run that finds the last full sync older
than ``FULL_EVERY`` does a full one by itself, however the job is scheduled. One run at a time
(Redis lock). Never searches anything outside the database.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import redis_lock
from app.db.session import session_scope
from app.market.cleaning import flag_external_outliers, flag_sold_outliers
from app.market.model_stats import recompute_model_stats
from app.market.negotiation import compute_negotiation_discount
from app.market.sold_sales import SYNC_KEY, sold_sales_summary, sync_sold_sales
from app.market.state import get_state, parse_time

log = get_logger(__name__)
FULL_EVERY = timedelta(hours=20)
LOCK_TTL_SECONDS = 900


async def sync_price_evidence(session: AsyncSession, *, full: bool | None = None) -> dict[str, Any]:
    """External outliers -> concluded sales -> negotiation discount -> sales outliers -> stats."""
    start = time.perf_counter()
    if full is None:
        last_full = parse_time((await get_state(session, SYNC_KEY) or {}).get("last_full"))
        full = last_full is None or datetime.now(UTC) - last_full >= FULL_EVERY
    before = (await sold_sales_summary(session))["total"]
    ext_outliers = await flag_external_outliers(session)
    synced = await sync_sold_sales(session, full=full)
    negotiation = await compute_negotiation_discount(session)
    sold_outliers = await flag_sold_outliers(session)
    stats = await recompute_model_stats(session)
    summary = await sold_sales_summary(session)
    result = {
        "full": full,
        "sold_sales_before": before,
        "sold_sales_after": summary["total"],
        "synced": synced,
        "by_source": summary["by_source"],
        "models_with_5_sales": summary["models_with_5_sales"],
        "outliers": {"sold_sales": sold_outliers, "external_prices": ext_outliers},
        "negotiation_discount": negotiation["discount"],
        "negotiation_n": negotiation["n"],
        "stats_rows": stats,
        "duration_ms": round((time.perf_counter() - start) * 1000),
    }
    log.info("price_evidence.synced", **{k: v for k, v in result.items() if not isinstance(v, dict)})
    return result


async def sync_price_evidence_task(ctx: dict[str, Any], full: bool | None = None) -> dict[str, Any]:
    """Sync concluded sales, negotiation discount, outliers, then ``recompute_model_stats``."""
    async with redis_lock("price-evidence-sync", LOCK_TTL_SECONDS) as acquired:
        if not acquired:
            return {"skipped": "already running"}
        async with session_scope() as s:
            return await sync_price_evidence(s, full=full)
