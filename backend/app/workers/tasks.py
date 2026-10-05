"""Background jobs: scanner, analysis, alerts delivery, lifecycle, statistics, learning, AI.

Every task opens its own unit of work, logs failures with context (never secrets) and uses arq's
``Retry`` with exponential backoff for transient errors.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from arq import Retry
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, OperationalError

from app.ai.service import run_ai_analysis, run_vision
from app.alerts.channels.base import ChannelError
from app.alerts.service import deliver_alert, evaluate_alerts
from app.analytics.learning import recompute_all_affinities
from app.analytics.market_stats import recompute_market_statistics
from app.core.cache import NS_FEED, cache
from app.core.config import get_settings
from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.core.redis import redis_lock
from app.db.models import AnalysisJob, Listing, MarketStatistic, Opportunity, OpportunityScore, SystemState
from app.db.session import session_scope
from app.domain.enums import JobStatus, ListingStatus
from app.ingestion.catalog import load_catalog
from app.ingestion.service import IngestionService, IngestResult
from app.marketplace.base import SearchQuery
from app.marketplace.registry import get_provider
from app.opportunities.pipeline import AnalysisPipeline
from app.workers.queue import backoff_seconds, enqueue

log = get_logger(__name__)
VISION_MIN_FLIP = 60
MAX_PAGES_PER_SCAN = 200
LIFECYCLE_BATCH = 1500
SCAN_LOCK_TTL_SECONDS = 900  # > the scan job timeout (600 s)


async def _set_state(key: str, value: dict[str, Any]) -> None:
    async with session_scope() as s:
        stmt = pg_insert(SystemState).values(key=key, value=value, updated_at=datetime.now(UTC))
        await s.execute(
            stmt.on_conflict_do_update(
                index_elements=["key"], set_={"value": value, "updated_at": datetime.now(UTC)}
            )
        )


async def _get_state(key: str) -> dict[str, Any] | None:
    async with session_scope() as s:
        row = await s.get(SystemState, key)
        return dict(row.value) if row else None


async def _segment_medians() -> dict[tuple[int, int], Decimal]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(
                    MarketStatistic.brand_id, MarketStatistic.category_id, MarketStatistic.median_price
                ).where(MarketStatistic.model_name.is_(None), MarketStatistic.size_normalized.is_(None))
            )
        ).all()
    return {(r.brand_id, r.category_id): r.median_price for r in rows if r.brand_id and r.category_id}


async def _enqueue_analyses(result: IngestResult, medians: dict[tuple[int, int], Decimal]) -> tuple[int, int]:
    """Enqueue analyses, newest first; likely deals go to the high-priority queue."""
    settings = get_settings()
    ids = result.to_analyze + [pc.listing_id for pc in result.price_changes]
    if not ids:
        return 0, 0
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(
                    Listing.id,
                    Listing.price,
                    Listing.brand_id,
                    Listing.category_id,
                    Listing.published_at,
                    Listing.status,
                )
                .where(Listing.id.in_(ids))
                .order_by(Listing.published_at.desc())
            )
        ).all()
    dropped = {pc.listing_id for pc in result.price_changes if pc.new_price < pc.old_price}
    high = default = 0
    for r in rows:
        if r.status != ListingStatus.ACTIVE:
            continue
        median = medians.get((r.brand_id, r.category_id))
        prescore = float(1 - r.price / median) if median and median > 0 else 0.0
        is_high = r.id in dropped or prescore >= settings.analysis_high_priority_prescore
        if await enqueue("analyze_listing", str(r.id), high=is_high, job_id=f"analyze:{r.id}"):
            high += is_high
            default += not is_high
    return high, default


async def _apply_status_changes(result: IngestResult) -> None:
    gone = [lid for lid, _old, new in result.status_changes if new != ListingStatus.ACTIVE]
    if gone:
        async with session_scope() as s:
            await AnalysisPipeline(s).deactivate(gone)


async def scan_new_listings(ctx: dict[str, Any]) -> dict[str, Any]:
    """Poll the provider for listings published since the last cursor and ingest them.

    Only one scan runs at a time (Redis lock): a long first backfill must not overlap with the
    next scheduled tick, which would ingest the same pages twice.
    """
    async with redis_lock("scan_new_listings", SCAN_LOCK_TTL_SECONDS) as acquired:
        if not acquired:
            log.info("scanner.skipped", reason="previous scan still running")
            return {"skipped": "already_running"}
        return await _scan_new_listings(ctx)


async def _scan_new_listings(ctx: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    started = time.perf_counter()
    async with session_scope() as s:
        provider = await get_provider(s)
    state = await _get_state(f"scanner_cursor:{provider.name}") or {}
    since = datetime.fromisoformat(state["since"]) if state.get("since") else None
    cursor: str | None = None
    newest = since
    totals = {
        "received": 0,
        "new": 0,
        "updated": 0,
        "price_changes": 0,
        "duplicates": 0,
        "high": 0,
        "default": 0,
    }
    first_run = since is None
    medians = await _segment_medians()
    all_results: list[IngestResult] = []
    try:
        for _page in range(MAX_PAGES_PER_SCAN):
            page = await provider.search_listings(
                SearchQuery(since=since, cursor=cursor, page_size=settings.scan_batch_size)
            )
            if page.listings:
                async with session_scope() as s:
                    result = await IngestionService(s, provider.name).ingest(page.listings)
                all_results.append(result)
                totals["received"] += result.received
                totals["new"] += len(result.new_ids)
                totals["updated"] += len(result.updated_ids)
                totals["price_changes"] += len(result.price_changes)
                totals["duplicates"] += len(result.duplicates)
                await _apply_status_changes(result)
                page_newest = max((pl.published_at for pl in page.listings if pl.published_at), default=None)
                if page_newest and (newest is None or page_newest > newest):
                    newest = page_newest
                if not first_run:
                    h, d = await _enqueue_analyses(result, medians)
                    totals["high"] += h
                    totals["default"] += d
            if not page.has_more:
                break
            cursor = page.next_cursor
    except ProviderUnavailableError as exc:
        log.warning("scanner.provider_unavailable", details=str(exc.details))
        raise Retry(defer=60) from exc

    if first_run and all_results:
        # Backfill: build the market database before analysing so thin segments get priors.
        await recompute_market_statistics_task(ctx)
        medians = await _segment_medians()
        for result in reversed(all_results):
            h, d = await _enqueue_analyses(result, medians)
            totals["high"] += h
            totals["default"] += d
    if newest is not None:
        await _set_state(f"scanner_cursor:{provider.name}", {"since": newest.isoformat()})
    totals["duration_ms"] = round((time.perf_counter() - started) * 1000)
    await _set_state("scanner_last_run", {"at": datetime.now(UTC).isoformat(), **totals})
    if totals["new"]:
        log.info("scanner.completed", **totals)
    return totals


async def analyze_listing(
    ctx: dict[str, Any], listing_id: str, after_vision: bool = False
) -> dict[str, Any] | None:
    settings = get_settings()
    job_try = ctx.get("job_try", 1)
    lid = uuid.UUID(listing_id)
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    try:
        async with session_scope() as s:
            outcome = await AnalysisPipeline(s, settings).analyze_listing(lid)
            if outcome is None:
                return None
            listing = await s.get(Listing, lid)
            assert listing is not None
            catalog = await load_catalog(s)
            pending = await evaluate_alerts(s, outcome, listing, catalog)
            s.add(
                AnalysisJob(
                    listing_id=lid,
                    job_type="analyze",
                    status=JobStatus.SUCCEEDED.value,
                    priority=ctx.get("queue", "default"),
                    attempts=job_try,
                    queued_at=started,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    duration_ms=round((time.perf_counter() - t0) * 1000),
                )
            )
            has_remote_photos = any(i.url.startswith("https://") for i in listing.images)
            vision_done = bool((listing.identification or {}).get("vision"))
    except (OperationalError, DBAPIError) as exc:
        log.warning("analysis.db_error", listing_id=listing_id, attempt=job_try, error=type(exc).__name__)
        raise Retry(defer=backoff_seconds(job_try)) from exc
    except Exception:
        log.exception("analysis.failed", listing_id=listing_id)
        async with session_scope() as s:
            s.add(
                AnalysisJob(
                    listing_id=lid,
                    job_type="analyze",
                    status=JobStatus.FAILED.value,
                    attempts=job_try,
                    queued_at=started,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    error="analysis failed (see logs)",
                )
            )
        return None

    for alert_id, channel in pending:
        await enqueue(
            "deliver_alert_task", str(alert_id), channel, high=True, job_id=f"deliver:{alert_id}:{channel}"
        )
    r = outcome.result
    if r.flip.score >= VISION_MIN_FLIP and has_remote_photos and not vision_done and not after_vision:
        await enqueue("vision_task", listing_id, job_id=f"vision:{listing_id}")
    if settings.ai_api_key and r.flip.score >= settings.ai_auto_analyze_min_flip_score:
        await enqueue("ai_analyze_task", str(outcome.opportunity_id), job_id=f"ai:{outcome.opportunity_id}")
    await cache.bump(NS_FEED)
    return {
        "flip": r.flip.score,
        "confidence": r.confidence.score,
        "risk": r.risk.score,
        "alerts": len(pending),
    }


async def vision_task(ctx: dict[str, Any], listing_id: str) -> None:
    async with session_scope() as s:
        changed = await run_vision(s, uuid.UUID(listing_id))
    if changed:
        await enqueue("analyze_listing", listing_id, True, job_id=f"analyze:{listing_id}")


async def ai_analyze_task(ctx: dict[str, Any], opportunity_id: str) -> None:
    async with session_scope() as s:
        opp = await s.get(Opportunity, uuid.UUID(opportunity_id))
        if opp is None or not opp.is_active or opp.ai_provider == "claude":
            return
        await run_ai_analysis(s, opp)
    await cache.bump(NS_FEED)


async def deliver_alert_task(ctx: dict[str, Any], alert_id: str, channel: str) -> None:
    job_try = ctx.get("job_try", 1)
    try:
        async with session_scope() as s:
            await deliver_alert(s, uuid.UUID(alert_id), channel)
    except ChannelError as exc:
        if exc.transient and job_try < 5:
            raise Retry(defer=backoff_seconds(job_try, base=3)) from exc
        log.error("alerts.delivery_gave_up", channel=channel, alert_id=alert_id, attempts=job_try)


async def recompute_market_statistics_task(ctx: dict[str, Any]) -> int:
    async with session_scope() as s:
        n = await recompute_market_statistics(s, get_settings().market_stats_window_days)
    log.info("market_stats.recomputed", segments=n)
    return n


async def refresh_listings(ctx: dict[str, Any]) -> dict[str, int]:
    """Lifecycle: re-check active listings (best opportunities first), detect sales/removals and
    price changes. Disappeared listings are kept and marked, never deleted."""
    settings = get_settings()
    stale_before = datetime.now(UTC) - timedelta(minutes=10)
    async with session_scope() as s:
        provider = await get_provider(s)
        rows = (
            await s.execute(
                select(Listing.id, Listing.external_id)
                .outerjoin(Opportunity, Opportunity.listing_id == Listing.id)
                .where(
                    Listing.provider == provider.name,
                    Listing.status == ListingStatus.ACTIVE,
                    Listing.last_seen_at < stale_before,
                )
                .order_by(func.coalesce(Opportunity.flip_score, 0).desc(), Listing.last_seen_at)
                .limit(LIFECYCLE_BATCH)
            )
        ).all()
    if not rows:
        return {"checked": 0}
    found = []
    missing = []
    for r in rows:
        try:
            pl = await provider.get_listing(r.external_id)
        except ProviderUnavailableError:
            break
        if pl is None:
            missing.append(r.id)
        else:
            found.append(pl)
    stats = {"checked": len(found) + len(missing), "missing": len(missing)}
    if found:
        async with session_scope() as s:
            result = await IngestionService(s, provider.name).ingest(found)
        await _apply_status_changes(result)
        h, d = await _enqueue_analyses(result, await _segment_medians())
        stats.update(
            price_changes=len(result.price_changes),
            status_changes=len(result.status_changes),
            reanalyze=h + d,
        )
    if missing:
        new_status = ListingStatus.REMOVED if provider.capabilities.sold_data else ListingStatus.POSSIBLY_SOLD
        now = datetime.now(UTC)
        async with session_scope() as s:
            await s.execute(
                update(Listing)
                .where(Listing.id.in_(missing))
                .values(status=new_status.value, status_changed_at=now, removed_at=now)
            )
            await AnalysisPipeline(s).deactivate(missing)
    if stats.get("status_changes") or missing:
        await cache.bump(NS_FEED)
    log.info("lifecycle.completed", stale_hours=settings.lifecycle_stale_hours, **stats)
    return stats


async def recompute_learning(ctx: dict[str, Any]) -> int:
    async with session_scope() as s:
        n = await recompute_all_affinities(s)
    await cache.bump(NS_FEED)
    return n


async def prune(ctx: dict[str, Any]) -> None:
    """Retention: job logs 7 days, score history 30 days (current opportunity rows are kept)."""
    now = datetime.now(UTC)
    async with session_scope() as s:
        await s.execute(delete(AnalysisJob).where(AnalysisJob.queued_at < now - timedelta(days=7)))
        await s.execute(
            delete(OpportunityScore).where(OpportunityScore.computed_at < now - timedelta(days=30))
        )
