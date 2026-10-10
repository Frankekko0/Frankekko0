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
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, OperationalError

from app.agent.model import AnthropicAgentModel
from app.agent.review import review_candidates
from app.ai.llm import AiDeferred, get_llm
from app.ai.queue import claim_pending, defer_opportunity, qualifies_for_strong_ai, quota_room
from app.ai.queue import kick as kick_ai_review
from app.ai.service import review_opportunity, run_vision
from app.alerts.channels.base import ChannelError
from app.alerts.service import deliver_alert, evaluate_alerts
from app.analytics.accuracy import fit_price_calibration
from app.analytics.learning import recompute_all_affinities
from app.analytics.market_stats import recompute_market_statistics
from app.core.cache import NS_FEED, cache
from app.core.config import get_settings
from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.core.redis import redis_lock
from app.db.models import (
    AcquisitionAttempt,
    AnalysisJob,
    Listing,
    MarketStatistic,
    Opportunity,
    SystemState,
)
from app.db.session import session_scope
from app.domain.enums import AcquisitionMode, JobStatus, ListingStatus, StatusEvidence
from app.ingestion.catalog import load_catalog
from app.ingestion.service import IngestionService, IngestResult
from app.market.jobs import sync_price_evidence_task
from app.marketplace.base import SearchQuery
from app.marketplace.registry import get_provider
from app.media.cleanup import clean_foreign_data
from app.opportunities.pipeline import AnalysisPipeline
from app.tracking.service import Attempt, TrackingService, record_attempts
from app.tracking.status import Observation
from app.vision.analyzer import VisionDeferred
from app.vision.cache import VisionCacheStore
from app.workers.queue import backoff_seconds, enqueue
from app.workers.vision_queue import queue_vision, retry_vision, vision_order

log = get_logger(__name__)
ANALYSIS_BATCH_HIGH = 8  # small: likely deals should surface within seconds
ANALYSIS_BATCH_DEFAULT = 40
FEED_BUMP_EVERY_SECONDS = 15
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


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _batch_job_id(ids: list[str]) -> str:
    # Same set of listings -> same job id, so arq drops duplicate batches.
    return f"analyze-batch:{uuid.uuid5(uuid.NAMESPACE_OID, ','.join(sorted(ids)))}"


async def _enqueue_analyses(result: IngestResult, medians: dict[tuple[int, int], Decimal]) -> tuple[int, int]:
    """Enqueue analyses in batches; likely deals go to the high-priority queue first.

    High-priority batches are small and newest-first (latency matters); bulk batches are
    grouped by brand and category so each batch reuses the same comparables pool.
    """
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
    high: list[Any] = []
    default: list[Any] = []
    for r in rows:
        if r.status != ListingStatus.ACTIVE:
            continue
        median = medians.get((r.brand_id, r.category_id))
        prescore = float(1 - r.price / median) if median and median > 0 else 0.0
        is_high = r.id in dropped or prescore >= settings.analysis_high_priority_prescore
        (high if is_high else default).append(r)
    default.sort(key=lambda r: (r.brand_id or 0, r.category_id or 0))
    for queue_rows, size, is_high in (
        (high, ANALYSIS_BATCH_HIGH, True),
        (default, ANALYSIS_BATCH_DEFAULT, False),
    ):
        for chunk in _chunks([str(r.id) for r in queue_rows], size):
            await enqueue("analyze_batch", chunk, high=is_high, job_id=_batch_job_id(chunk))
    return len(high), len(default)


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
    if provider is None:
        return {"skipped": "no_source_configured"}  # only your own captures: nothing to scan
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
    """Single-listing analysis (re-analysis after vision, API triggers): a batch of one."""
    res = await analyze_batch(ctx, [listing_id], after_vision=after_vision)
    return res["listings"].get(listing_id)


async def analyze_batch(
    ctx: dict[str, Any], listing_ids: list[str], after_vision: bool = False
) -> dict[str, Any]:
    """Analyse a batch of listings in one transaction, then fan out alerts and follow-ups."""
    settings = get_settings()
    job_try = ctx.get("job_try", 1)
    ids = [uuid.UUID(x) for x in listing_ids]
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    pending: list[tuple[uuid.UUID, str]] = []
    ai_kicks: list[tuple[int, uuid.UUID]] = []  # (flip, opportunity) newly waiting for the strong model
    summary: dict[str, dict[str, int]] = {}
    try:
        async with session_scope() as s:
            outcomes = await AnalysisPipeline(s, settings).analyze_many(ids)
            catalog = await load_catalog(s) if outcomes else None
            elapsed_ms = round((time.perf_counter() - t0) * 1000)
            per_item_ms = round(elapsed_ms / max(1, len(outcomes)))
            for outcome in outcomes:
                listing = outcome.listing
                assert listing is not None and catalog is not None
                alerts = await evaluate_alerts(s, outcome, listing, catalog)
                pending.extend(alerts)
                r = outcome.result
                if outcome.ai_pending and qualifies_for_strong_ai(
                    settings, flip_score=r.flip.score, data_quality=r.data_quality
                ):
                    ai_kicks.append((r.flip.score, outcome.opportunity_id))
                summary[str(listing.id)] = {
                    "flip": r.flip.score,
                    "confidence": r.confidence.score,
                    "risk": r.risk.score,
                    "alerts": len(alerts),
                }
            s.add_all(
                AnalysisJob(
                    listing_id=o.listing_id,
                    job_type="analyze",
                    status=JobStatus.SUCCEEDED.value,
                    priority=ctx.get("queue", "default"),
                    attempts=job_try,
                    queued_at=started,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    duration_ms=per_item_ms,
                )
                for o in outcomes
            )
            vision = vision_order(outcomes, after_vision)
    except (OperationalError, DBAPIError) as exc:
        log.warning("analysis.db_error", listings=len(ids), attempt=job_try, error=type(exc).__name__)
        raise Retry(defer=backoff_seconds(job_try)) from exc
    except Exception:
        log.exception("analysis.failed", listings=len(ids))
        async with session_scope() as s:
            s.add_all(
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
                for lid in ids
            )
        return {"analyzed": 0, "listings": {}}

    for alert_id, channel in pending:
        await enqueue(
            "deliver_alert_task", str(alert_id), channel, high=True, job_id=f"deliver:{alert_id}:{channel}"
        )
    # Photo checks best first (risk-adjusted profit, then flip): the top few on the high queue.
    await queue_vision(vision)
    # The strong model's review: the best new rows now (best effort, within the quota room); the minute sweep
    # (``ai_sweep_task``) picks up everything else in order, so nothing depends on this call.
    await kick_ai_review(ai_kicks, settings)
    if summary:
        # Continuous analysis would otherwise empty the feed cache every few seconds; feed TTLs
        # (20-30 s) bound staleness, user actions still invalidate immediately.
        await cache.bump_throttled(NS_FEED, FEED_BUMP_EVERY_SECONDS)
    return {"analyzed": len(summary), "listings": summary}


async def vision_task(ctx: dict[str, Any], listing_id: str) -> None:
    deferred: VisionDeferred | None = None
    async with session_scope() as s:
        try:
            changed = await run_vision(s, uuid.UUID(listing_id))
        except VisionDeferred as exc:
            # The model did not read the photos. What was measured is stored (and committed below).
            changed, deferred = exc.changed, exc
    if changed:
        await enqueue("analyze_listing", listing_id, True, job_id=f"analyze:{listing_id}")
    if deferred is not None:
        await retry_vision(ctx, listing_id, deferred)  # raises Retry, or gives up after VISION_MAX_TRIES


async def ai_analyze_task(ctx: dict[str, Any], opportunity_id: str) -> str:
    """The strong model's review of one opportunity (see ``app.ai.queue``). It never waits: with no quota room, an
    outage or an unusable answer it records the reason and the time of the next try on the row and returns, and the
    sweep queues it again then. The rules' text is never written in its place. Returns what happened."""
    settings = get_settings()
    llm = get_llm()
    if not llm.enabled:
        return "disabled"
    oid = uuid.UUID(opportunity_id)
    room = await quota_room(settings, llm.model_for("strong"))  # before any connection is opened
    if room.n <= 0:
        await defer_opportunity(oid, room.reason, room.retry_after, settings=settings)
        return "deferred"
    try:
        status = await review_opportunity(oid, settings)
    except AiDeferred as exc:
        await defer_opportunity(oid, exc.reason, exc.retry_after, settings=settings)
        return "deferred"
    except Exception:
        log.exception("ai.review_failed", opportunity_id=opportunity_id)
        await defer_opportunity(oid, "error", 0.0, settings=settings)  # counts: it must end, not loop
        return "failed"
    if status == "done":
        await cache.bump_throttled(NS_FEED, FEED_BUMP_EVERY_SECONDS)
    return status


async def ai_sweep_task(ctx: dict[str, Any]) -> dict[str, Any]:
    """Every minute: queue the strong model's review of the best waiting opportunities, as many as the quota left
    after the reserves allows (highest flip score first), each due a little after the previous so they spread over
    the minute instead of arriving together."""
    settings = get_settings()
    llm = get_llm()
    if not llm.enabled or settings.ai_auto_analyze_min_flip_score > 100:
        return {"queued": 0, "reason": "off"}
    room = await quota_room(settings, llm.model_for("strong"))
    if room.n <= 0:
        return {"queued": 0, "reason": room.reason}
    ids = await claim_pending(settings, room.n)
    rpm, _ = settings.ai_limits("strong")
    step = 60.0 / rpm if rpm > 0 else 60.0 / max(1, len(ids))
    queued = 0
    for i, oid in enumerate(ids):
        queued += await enqueue(
            "ai_analyze_task", str(oid), high=True, job_id=f"ai:{oid}", defer_seconds=i * step if i else None
        )
    if queued:
        log.info("ai.sweep", queued=queued, room=room.n)
    return {"queued": queued, "claimed": len(ids), "room": room.n}


async def review_candidates_task(ctx: dict[str, Any]) -> dict[str, Any]:
    """Review the best opportunities with the agent (rules when there is no model or no budget).

    Skipped when no candidate changed since the last review: nothing to redo."""
    settings = get_settings()
    llm = get_llm()
    model = AnthropicAgentModel(llm) if llm.enabled else None
    async with session_scope() as s:
        run = await review_candidates(s, model=model, settings=settings)
        summary = (
            {"skipped": True}
            if run is None
            else {
                "run_id": str(run.id),
                "provider": run.provider,
                "picks": len((run.result or {}).get("picks", [])),
                "fallback": (run.result or {}).get("fallback"),
            }
        )
    if run is not None:
        await cache.bump(NS_FEED)
    return summary


async def agent_propose_task(ctx: dict[str, Any]) -> dict[str, int]:
    """For every user with autonomy on, the agent prepares a few purchase and markdown proposals. It never
    executes: they land as blocked records, dry runs or tasks for the user, through the cycle's own policy. Off
    unless AGENT_PROPOSE_ENABLED; a run needs model quota to spare and is skipped when nothing changed. One
    transaction per user. Scheduled just before the autonomy cycle, whose rules then fill what the limits leave."""
    from app.agent.propose import propose_for_user
    from app.db.models import AutonomySettings

    settings = get_settings()
    llm = get_llm()
    if not settings.agent_propose_enabled or not llm.enabled:
        return {"users": 0, "ran": 0, "proposed": 0, "failed": 0}
    async with session_scope() as s:
        users = (
            (await s.execute(select(AutonomySettings.user_id).where(AutonomySettings.enabled.is_(True))))
            .scalars()
            .all()
        )
    ran = proposed = failed = 0
    for uid in users:
        try:
            async with session_scope() as s:
                model = AnthropicAgentModel(llm)
                run = await propose_for_user(s, uid, model=model, settings=settings, llm=llm)
                if run is not None:
                    ran += 1
                    proposed += len((run.result or {}).get("proposed", []))
        except Exception:  # one user's failure must not stop the others, it is logged
            failed += 1
            log.exception("agent.propose_failed", user_id=str(uid))
    return {"users": len(users), "ran": ran, "proposed": proposed, "failed": failed}


async def autonomy_cycle_task(ctx: dict[str, Any]) -> dict[str, int]:
    """One autonomy cycle for every user who switched it on (limits, kill switch and suspension are
    checked inside; each user has its own transaction so one failure does not stop the others)."""
    from app.autonomy.engine import run_cycle
    from app.db.models import AutonomySettings

    llm = get_llm()
    async with session_scope() as s:
        users = (
            (await s.execute(select(AutonomySettings.user_id).where(AutonomySettings.enabled.is_(True))))
            .scalars()
            .all()
        )
    ran = proposed = failed = 0
    for uid in users:
        try:
            async with session_scope() as s:
                res = await run_cycle(s, uid, llm=llm if llm.enabled else None)
            ran += res.state == "ran"
            proposed += res.proposed
        except Exception:  # one user's failure must not stop the others, it is logged
            failed += 1
            log.exception("autonomy.cycle_failed", user_id=str(uid))
    return {"users": len(users), "ran": ran, "proposed": proposed, "failed": failed}


async def business_daily_task(ctx: dict[str, Any]) -> dict[str, int]:
    """Once a day: fiscal-threshold notices for users who configured thresholds, and the learning report (written
    to the experiment registry) for users with closed sales. One user's failure does not stop the others."""
    from app.business.service import raise_tax_alerts
    from app.db.models import BusinessGoals, PredictionOutcome
    from app.intelligence.learning_report import run_and_register

    async with session_scope() as s:
        tax_users = (
            (
                await s.execute(
                    select(BusinessGoals.user_id).where(
                        func.jsonb_array_length(BusinessGoals.tax_thresholds) > 0
                    )
                )
            )
            .scalars()
            .all()
        )
        learn_users = (await s.execute(select(PredictionOutcome.user_id).distinct())).scalars().all()
    alerts = reports = failed = 0
    for uid in tax_users:
        try:
            async with session_scope() as s:
                alerts += await raise_tax_alerts(s, uid)
        except Exception:
            failed += 1
            log.exception("business.tax_failed", user_id=str(uid))
    settings = get_settings()
    for uid in learn_users:
        try:
            async with session_scope() as s:
                await run_and_register(
                    s, uid, float(settings.default_min_profit), float(settings.default_min_roi)
                )
            reports += 1
        except Exception:
            failed += 1
            log.exception("business.learning_failed", user_id=str(uid))
    return {"tax_users": len(tax_users), "alerts": alerts, "learning_reports": reports, "failed": failed}


async def deliver_alert_task(ctx: dict[str, Any], alert_id: str, channel: str) -> None:
    job_try = ctx.get("job_try", 1)
    try:
        async with session_scope() as s:
            await deliver_alert(s, uuid.UUID(alert_id), channel)
    except ChannelError as exc:
        if exc.transient and job_try < 5:
            raise Retry(defer=backoff_seconds(job_try, base=3)) from exc
        log.error("alerts.delivery_gave_up", channel=channel, alert_id=alert_id, attempts=job_try)


async def mark_stale_listings_task(ctx: dict[str, Any]) -> int:
    """Listings not confirmed for too long become "da verificare" (never shown as buyable)."""
    from app.tracking.verification import mark_stale_listings

    async with session_scope() as s:
        ids = await mark_stale_listings(s)
    if ids:
        log.info("lifecycle.marked_to_verify", count=len(ids))
        await cache.bump(NS_FEED)
    return len(ids)


async def recompute_market_statistics_task(ctx: dict[str, Any]) -> int:
    async with session_scope() as s:
        n = await recompute_market_statistics(s, get_settings().market_stats_window_days)
    log.info("market_stats.recomputed", segments=n)
    return n


async def refresh_listings(ctx: dict[str, Any]) -> dict[str, int]:
    """Periodic status checks of the listings the configured provider can re-read.

    Picks the listings whose adaptive ``next_check_at`` is due (recent, popular, reserved and
    promising listings first come due more often; closed ones never). A listing the provider no
    longer returns becomes ``removed`` - a sale is recorded only when the provider says so.
    Checks of tracked Vinted items that no server-side source can read are left to the other
    acquisition modes (browser extension, email, opt-in public fetch).
    """
    now = datetime.now(UTC)
    async with session_scope() as s:
        provider = await get_provider(s)
        if provider is None:
            return {"checked": 0}
        rows = (
            await s.execute(
                select(Listing.id, Listing.external_id)
                .where(Listing.provider == provider.name, Listing.next_check_at <= now)
                .order_by(Listing.next_check_at)
                .limit(LIFECYCLE_BATCH)
            )
        ).all()
    if not rows:
        return {"checked": 0}
    found = []
    missing: list[uuid.UUID] = []
    unreachable: list[Any] = []
    for i, r in enumerate(rows):
        try:
            pl = await provider.get_listing(r.external_id)
        except ProviderUnavailableError as exc:
            unreachable = list(rows[i:])
            log.warning("lifecycle.provider_unavailable", provider=provider.name, error=str(exc))
            break
        if pl is None:
            missing.append(r.id)
        else:
            found.append(pl)
    stats = {"checked": len(found) + len(missing), "missing": len(missing), "unreachable": len(unreachable)}
    if found:
        async with session_scope() as s:
            result = await IngestionService(s, provider.name, AcquisitionMode.PROVIDER_SCAN).ingest(
                found, now=now
            )
        await _apply_status_changes(result)
        h, d = await _enqueue_analyses(result, await _segment_medians())
        stats.update(
            price_changes=len(result.price_changes),
            status_changes=len(result.status_changes),
            reanalyze=h + d,
        )
    if missing or unreachable:
        observations = {lid: Observation(now, StatusEvidence.NOT_FOUND) for lid in missing}
        observations |= {r.id: Observation(now, StatusEvidence.UNREACHABLE) for r in unreachable}
        async with session_scope() as s:
            await TrackingService(s).observe(observations, AcquisitionMode.PROVIDER_SCAN)
            await record_attempts(
                s,
                [
                    Attempt(
                        AcquisitionMode.PROVIDER_SCAN,
                        "refresh",
                        listing_id=lid,
                        outcome="not_found",
                        message="L'annuncio non è più disponibile presso la fonte: segnato come rimosso.",
                    )
                    for lid in missing
                ]
                + [
                    Attempt(
                        AcquisitionMode.PROVIDER_SCAN,
                        "refresh",
                        listing_id=r.id,
                        vinted_id=r.external_id,
                        outcome="error",
                        message="Fonte dati non raggiungibile: controllo rimandato.",
                    )
                    for r in unreachable
                ],
            )
    if stats.get("status_changes") or missing:
        await cache.bump(NS_FEED)
    log.info("lifecycle.completed", **stats)
    return stats


async def poll_email(ctx: dict[str, Any]) -> dict[str, int] | None:
    """Optional: read new Vinted notification emails (sold / price reduced) from the mailbox."""
    from app.acquisition.imap_poller import poll_mailbox

    async with session_scope() as s:
        summary = await poll_mailbox(s)
    if summary and (summary.sold or summary.price_drops):
        await cache.bump(NS_FEED)
    return summary.as_dict() if summary else None


async def clean_foreign_data_task(ctx: dict[str, Any]) -> dict[str, Any]:
    """Remove images and seller data that never belonged to a listing, then re-analyse it
    (at startup and daily; idempotent)."""
    async with session_scope() as s:
        report = await clean_foreign_data(s)
    ids = [str(i) for i in report.listing_ids]
    for chunk in _chunks(ids, ANALYSIS_BATCH_DEFAULT):
        await enqueue("analyze_batch", chunk, high=False, job_id=_batch_job_id(chunk))
    if ids:
        await _set_state("cleanup_foreign_data", {**report.as_dict(), "at": datetime.now(UTC).isoformat()})
        await cache.bump(NS_FEED)
    return report.as_dict()


async def fit_price_calibration_task(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily retroactive accuracy check + calibration of the resale ranges, then re-analysis of
    the active opportunities so every range and score uses it."""
    async with session_scope() as s:
        metrics = await fit_price_calibration(s)
        ids = [
            str(i)
            for i in (
                await s.execute(select(Opportunity.listing_id).where(Opportunity.is_active.is_(True)))
            ).scalars()
        ]
    for chunk in _chunks(ids, ANALYSIS_BATCH_DEFAULT):
        await enqueue("analyze_batch", chunk, high=False, job_id=_batch_job_id(chunk))
    await cache.bump(NS_FEED)
    return {k: metrics.get(k) for k in ("test_sales", "before", "after")}


async def sync_price_evidence_full_task(ctx: dict[str, Any]) -> dict[str, Any]:
    """Nightly full sync of the price evidence: every source re-read, statistics rebuilt (the
    30-minute runs are incremental)."""
    return await sync_price_evidence_task(ctx, full=True)


async def recompute_learning(ctx: dict[str, Any]) -> int:
    async with session_scope() as s:
        n = await recompute_all_affinities(s)
    await cache.bump(NS_FEED)
    return n


async def prune(ctx: dict[str, Any]) -> None:
    """Retention. Listings keep their whole history (snapshots, every analysis); technical job
    logs are kept 7 days and acquisition logs 180."""
    now = datetime.now(UTC)
    async with session_scope() as s:
        await s.execute(delete(AnalysisJob).where(AnalysisJob.queued_at < now - timedelta(days=7)))
        await s.execute(
            delete(AcquisitionAttempt).where(AcquisitionAttempt.started_at < now - timedelta(days=180))
        )
    await VisionCacheStore().prune()  # stored photo analyses nobody asked for in 90 days
