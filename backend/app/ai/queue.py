"""The queue of opportunities waiting for the strong model's review, and the pace it may go at.

The database is the queue: an active opportunity whose ``ai_for_analysis_id`` is not its current analysis, and whose
flip score reaches ``AI_AUTO_ANALYZE_MIN_FLIP_SCORE``, is waiting. That survives a restart, a deploy, a day of
exhausted quota and a Redis flush. Redis only paces it (``app.ai.limiter``: requests per minute and per day of the
model, a cooldown after a 429).

* ``ai_sweep_task`` (cron, every minute) takes the best waiting rows, as many as the quota left after the reserves
  allows, and queues one ``ai_analyze_task`` for each; the sweep marks them (a lease in ``ai_next_attempt_at``) so
  the next one does not pick them again while the job is queued.
* ``ai_analyze_task`` makes the call. A call that did not happen or did not give a usable answer writes nothing
  but the reason and the time of the next try (never the rules' text over a record, never a sleep holding a
  connection). Quota and outages do not count as attempts; an unusable answer does, and after ``AI_MAX_ATTEMPTS``
  the row stops being picked until its analysis changes (it stays visible in ``/ai/usage`` as exhausted).
* ``analyze_batch`` kicks the best new rows of a batch (``kick``) so a fresh find does not wait for the minute.
  The sweep and the kicks share one per-minute pace (``take_pace``, a Redis counter): together they queue at most the
  room of that minute, which is ``AI_SWEEP_BATCH`` per minute when the provider has no limits of its own.
"""

from __future__ import annotations

import contextlib
import math
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from redis.exceptions import RedisError
from sqlalchemy import ColumnElement, and_, func, or_, select, update

from app.ai.limiter import get_limiter
from app.ai.llm import get_llm
from app.ai.verdicts import LLM_PROVIDERS
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.models import Opportunity
from app.db.session import session_scope
from app.workers.queue import enqueue

log = get_logger(__name__)

# Reasons that count as a failed attempt: the model answered but not usably, the request itself is wrong, or the
# review hung or was cancelled (a provider that never answers must end up exhausted, not retried for ever).
# Everything else (quota, cooldown, breaker, outage, budget) is waiting, not failing.
COUNTED_REASONS = frozenset(
    {"bad_answer", "truncated", "refused", "rejected", "error", "timeout", "cancelled"}
)
LEASE_SECONDS = 300  # a queued job that never ran is picked again after this
MAX_QUOTA_WAIT = 3600  # a quota wait is re-checked at least hourly (limits can change, Redis can blink)
PACE_TTL = 120  # seconds a minute's pace counter is kept

# How long one review may take. The Anthropic client retries a failed request twice (``max_retries`` in
# ``app.ai.llm``), each attempt up to ``AI_TIMEOUT_SECONDS``; Gemini makes a single attempt.
SDK_RETRIES = 2
REVIEW_MARGIN_SECONDS = 30  # database steps and the client's backoff between its attempts
JOB_GRACE_SECONDS = 30  # the job's own timeout is only a backstop behind the review's


def review_budget_seconds(settings: Settings) -> float:
    """The longest a review may legitimately take (the client's worst case plus a margin): the task stops waiting after
    this and records a ``timeout``, so a hung provider moves the row's attempts and backoff."""
    attempts = 1 if settings.ai_provider == "gemini" else SDK_RETRIES + 1
    return settings.ai_timeout_seconds * attempts + REVIEW_MARGIN_SECONDS


def review_job_timeout(settings: Settings) -> float:
    """The worker's timeout for ``ai_analyze_task``: past the review's own budget, so that it is the budget that ends a
    slow review (and writes the reason down) and not a cancellation from outside."""
    return review_budget_seconds(settings) + JOB_GRACE_SECONDS


def qualifies_for_strong_ai(
    settings: Settings, *, flip_score: int, data_quality: str | None, is_active: bool = True
) -> bool:
    """The one gate for the strong model's review: a key, an active listing, a usable estimate and a flip score at
    the threshold. (The decision verdict is not a gate: every listing at the threshold gets its review.)"""
    return bool(
        settings.ai_api_key
        and is_active
        and data_quality != "insufficient"
        and flip_score >= settings.ai_auto_analyze_min_flip_score
    )


def pending_clause(settings: Settings) -> ColumnElement[bool]:
    """SQL form of the gate plus "no valid review of the current analysis": the rows that wait."""
    return and_(
        Opportunity.is_active.is_(True),
        Opportunity.analysis_id.is_not(None),
        Opportunity.data_quality != "insufficient",
        Opportunity.flip_score >= math.ceil(settings.ai_auto_analyze_min_flip_score),
        Opportunity.ai_for_analysis_id.is_distinct_from(Opportunity.analysis_id),
    )


def due_clause(settings: Settings, now: datetime) -> ColumnElement[bool]:
    return and_(
        Opportunity.ai_attempts < settings.ai_max_attempts,
        or_(Opportunity.ai_next_attempt_at.is_(None), Opportunity.ai_next_attempt_at <= now),
    )


def seconds_to_quota_reset(settings: Settings, now: datetime | None = None) -> float:
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(settings.ai_quota_tz))
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1.0, (midnight - local).total_seconds())


def retry_delay(settings: Settings, reason: str, retry_after: float, attempts: int) -> float:
    """Seconds until the next try. A failure that counts backs off exponentially from ``AI_BACKOFF_BASE_SECONDS``
    up to ``AI_BACKOFF_MAX_SECONDS``; waiting for quota or an outage waits what the provider (or the limiter) said,
    at least the base, at most an hour before looking again. Never less than ``retry_after`` for a failure."""
    base = float(settings.ai_backoff_base_seconds)
    if reason in COUNTED_REASONS:
        own = min(float(settings.ai_backoff_max_seconds), base * 2 ** max(0, attempts - 1))
        return max(float(retry_after), own)
    return min(max(float(retry_after), base), float(MAX_QUOTA_WAIT))


@dataclass(frozen=True)
class Room:
    """How many reviews may start now (0 with the reason and when to look again)."""

    n: int
    reason: str = ""
    retry_after: float = 0.0


def _reserve(reserve: int, limit: int) -> int:
    return min(reserve, max(0, limit - 1))  # a reserve as large as the limit would switch the review off


async def quota_room(settings: Settings, model: str) -> Room:
    """The requests left on ``model`` this minute and today, less the reserves kept for photo checks and the
    interactive calls, at most ``AI_SWEEP_BATCH``. Read-only: the call itself takes its place in the limiter."""
    snap = await get_limiter().snapshot(model, "strong")
    if snap["cooldown_s"] > 0:
        return Room(0, "cooldown", float(snap["cooldown_s"]))
    n = settings.ai_sweep_batch
    if snap["rpm_left"] is not None:
        spare = int(snap["rpm_left"]) - _reserve(settings.ai_reserve_rpm, int(snap["rpm_limit"]))
        if spare <= 0:
            return Room(0, "rpm", 30.0)
        n = min(n, spare)
    if snap["rpd_left"] is not None:
        spare = int(snap["rpd_left"]) - _reserve(settings.ai_reserve_rpd, int(snap["rpd_limit"]))
        if spare <= 0:
            return Room(0, "rpd", seconds_to_quota_reset(settings))
        n = min(n, spare)
    return Room(n)


def _pace_minute() -> int:
    return int(time.time() // 60)


async def take_pace(model: str, wanted: int, cap: int) -> tuple[int, str]:
    """Reserve up to ``wanted`` review jobs in this minute's pace, at most ``cap`` altogether for the sweep and the
    kicks. Without a window of the provider's own (limits of 0) nothing else bounds the rate, and ``quota_room`` is
    stateless, so each kick would see the whole room again. Returns ``(granted, key)``; ``key`` is for
    ``give_back_pace``. Fails closed: with Redis down nothing is reserved and the sweep tries again next minute.
    Calendar minutes, so the rate is held on average, not in every sliding 60 seconds."""
    key = f"ff:ai:pace:{model}:{_pace_minute()}"
    if wanted <= 0 or cap <= 0:
        return 0, key
    try:
        redis = get_redis()
        total = int(await redis.incrby(key, wanted))
        await redis.expire(key, PACE_TTL)
        over = min(wanted, max(0, total - cap))
        if over:  # give back what was refused: concurrent callers may be refused too, but never over-granted
            await redis.decrby(key, over)
        return wanted - over, key
    except RedisError as exc:
        log.warning("ai.pace_redis_unavailable", error=str(exc))
        return 0, key


async def give_back_pace(key: str, n: int) -> None:
    """Return the part of a reservation that was not used (the sweep found fewer rows than the room)."""
    if n > 0:
        with contextlib.suppress(RedisError):
            await get_redis().decrby(key, n)


async def defer_opportunity(
    opportunity_id: uuid.UUID, reason: str, retry_after: float = 0.0, *, settings: Settings | None = None
) -> None:
    """Record why the review did not happen and when to try again (a short transaction; nothing else is touched).
    A failure that counts adds an attempt; at ``AI_MAX_ATTEMPTS`` the row is exhausted until its analysis changes."""
    settings = settings or get_settings()
    counted = reason in COUNTED_REASONS
    async with session_scope() as s:
        row = (
            await s.execute(
                select(
                    Opportunity.ai_attempts,
                    Opportunity.is_active,
                    Opportunity.analysis_id,
                    Opportunity.ai_for_analysis_id,
                )
                .where(Opportunity.id == opportunity_id)
                .with_for_update()
            )
        ).one_or_none()
        if row is None or not row.is_active or row.ai_for_analysis_id == row.analysis_id:
            return  # gone, closed or already reviewed: nothing waits
        attempts = row.ai_attempts + (1 if counted else 0)
        delay = retry_delay(settings, reason, retry_after, attempts)
        await s.execute(
            update(Opportunity)
            .where(Opportunity.id == opportunity_id)
            .values(
                ai_attempts=attempts,
                ai_last_error=reason[:48],
                ai_next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay),
            )
        )
    log.info("ai.deferred", opportunity_id=str(opportunity_id), reason=reason, retry_in=round(delay))
    if counted and attempts >= settings.ai_max_attempts:
        log.warning("ai.exhausted", opportunity_id=str(opportunity_id), reason=reason, attempts=attempts)


async def claim_pending(settings: Settings, n: int, now: datetime | None = None) -> list[uuid.UUID]:
    """The ``n`` best waiting opportunities (highest flip score, then risk-adjusted profit), each leased for
    ``LEASE_SECONDS`` so that a concurrent or later sweep does not take it while its job is queued
    (``FOR UPDATE SKIP LOCKED``: two sweeps never take the same row)."""
    now = now or datetime.now(UTC)
    async with session_scope() as s:
        ids = list(
            (
                await s.execute(
                    select(Opportunity.id)
                    .where(pending_clause(settings), due_clause(settings, now))
                    .order_by(
                        Opportunity.flip_score.desc(), Opportunity.risk_adjusted_profit.desc().nulls_last()
                    )
                    .limit(n)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        if ids:
            await s.execute(
                update(Opportunity)
                .where(Opportunity.id.in_(ids))
                .values(ai_next_attempt_at=now + timedelta(seconds=LEASE_SECONDS))
            )
    return ids


async def queue_stats(settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    """Counts for ``/ai/usage``: waiting, ready now, waiting for their next try, exhausted, already reviewed."""
    now = now or datetime.now(UTC)
    pending = pending_clause(settings)
    below_cap = Opportunity.ai_attempts < settings.ai_max_attempts
    async with session_scope() as s:
        row = (
            await s.execute(
                select(
                    func.count().filter(pending),
                    func.count().filter(pending & due_clause(settings, now)),
                    func.count().filter(pending & below_cap & (Opportunity.ai_next_attempt_at > now)),
                    func.count().filter(pending & ~below_cap),
                ).where(Opportunity.is_active.is_(True))
            )
        ).one()
        reviewed = (
            await s.execute(
                select(func.count()).where(
                    Opportunity.is_active.is_(True),
                    Opportunity.ai_provider.in_(sorted(LLM_PROVIDERS)),
                    Opportunity.ai_for_analysis_id == Opportunity.analysis_id,
                )
            )
        ).scalar_one()
        reasons = (
            await s.execute(
                select(Opportunity.ai_last_error, func.count())
                .where(pending, Opportunity.ai_last_error.is_not(None))
                .group_by(Opportunity.ai_last_error)
            )
        ).all()
    return {
        "min_flip_score": settings.ai_auto_analyze_min_flip_score,
        "pending": row[0],
        "ready": row[1],
        "deferred": row[2],
        "exhausted": row[3],
        "reviewed": reviewed,
        "last_errors": {r[0]: r[1] for r in reasons},
    }


async def kick(candidates: list[tuple[int, uuid.UUID]], settings: Settings | None = None) -> int:
    """Best effort, after an analysis batch: queue the review of the best new rows without waiting for the next
    sweep. Within the quota room of this minute (shared with the sweep through ``take_pace``: together they never
    queue more than the room), and never ahead of a better row already waiting (the sweep keeps the order). A failure
    here only costs the minute: the sweep finds the rows anyway."""
    settings = settings or get_settings()
    llm = get_llm()
    if not candidates or not llm.enabled:
        return 0
    try:
        room = await quota_room(settings, llm.model_for("strong"))
        if room.n <= 0:
            return 0
        async with session_scope() as s:
            best = (
                await s.execute(
                    select(func.max(Opportunity.flip_score)).where(
                        pending_clause(settings),
                        due_clause(settings, datetime.now(UTC)),
                        Opportunity.id.not_in([oid for _, oid in candidates]),
                    )
                )
            ).scalar()
        picks = [c for c in sorted(candidates, key=lambda c: -c[0]) if best is None or c[0] >= best][: room.n]
        if not picks:
            return 0
        granted, _ = await take_pace(llm.model_for("strong"), len(picks), room.n)  # shared with the sweep
        picks = picks[:granted]
        for _, oid in picks:
            await enqueue("ai_analyze_task", str(oid), high=True, job_id=f"ai:{oid}")
        return len(picks)
    except Exception as exc:
        log.warning("ai.kick_failed", error=type(exc).__name__)
        return 0
