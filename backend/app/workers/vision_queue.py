"""Photo (vision) checks queued best candidates first.

arq has no priorities inside a queue: jobs run in the order of their score (the time they are
due). So the candidates of a batch are sorted by risk-adjusted profit, then flip score; the best
``VISION_HIGH_TOP`` go to the high queue (served by its own worker next to the bulk analyses),
the rest to the default queue, and each job is due one millisecond after the previous one, so
both queues run them in that order. Enqueueing never blocks or fails a capture: the API queues
them after the response is sent and a Redis outage only costs the photo checks.

Which listings: those whose value of information says the photos can change the decision, or every one with a
complete gallery when ``AI_VISION_ALWAYS`` is on (an explicit choice: it sends every such gallery to the
provider). "Checked" means a model analysed the photos; the local measures alone do not count.
"""

from __future__ import annotations

import contextlib
import random
import uuid
from collections.abc import Iterable
from typing import Any

from arq import Retry
from arq.constants import retry_key_prefix
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.stages import needs_photo_check
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import Listing, ListingImage, Opportunity
from app.db.session import session_scope
from app.domain.enums import ListingStatus
from app.vision.analyzer import (
    BAD_ANSWER_REASONS,
    GALLERY_REASONS,
    GAVE_UP_NOTES,
    MAX_BAD_ANSWER_TRIES,
    MAX_TRUNCATED_TRIES,
    VisionDeferred,
)
from app.workers.queue import enqueue

log = get_logger(__name__)

VISION_MIN_FLIP = 60
VISION_HIGH_TOP = 3
_STEP_SECONDS = 0.001
VISION_MAX_TRIES = 5  # times the model is asked for one gallery (``max_tries`` of ``vision_task``)
_MAX_DEFER_SECONDS = 6 * 3600  # an arq job expires a day after it was due: wake up often enough to keep it
_HELD_SPREAD = 15.0  # seconds: least spread added to the wait of a job a cap held back (see ``retry_vision``)


PHOTO_ONLY = {"photos_checked", "label"}


def voi_says(result: Any) -> bool | None:
    """The value-of-information verdict of the analysis (None when the analysis carries none).

    A photo analysis is paid for only where it can change the decision: never on a clear PASS, and on the
    cases at the boundary. A Buy whose only unmet Strong-buy requirements are the photo ones is also worth
    it: the check is what could make it a Strong buy."""
    decision = getattr(result, "decision", None)
    block = getattr(decision, "intelligence", None) or {}
    voi = block.get("voi")
    if not voi:
        return None
    if voi.get("worth_it"):
        return True
    unmet = {r.code for r in getattr(decision, "strong_buy_requirements", []) if not r.met}
    verdict = getattr(getattr(decision, "verdict", None), "value", None)
    return verdict == "BUY" and bool(unmet) and unmet <= PHOTO_ONLY


def voi_says_stored(decision: dict[str, Any] | None, flip_score: int | None, rap: Any) -> bool:
    """The same policy as ``voi_says`` / ``worth_vision``, read from the stored opportunity (the upload path has no
    fresh analysis in hand): the value-of-information verdict of the stored decision, else the flip score or a positive
    risk-adjusted profit."""
    block = ((decision or {}).get("intelligence") or {}).get("voi")
    if block:
        if block.get("worth_it"):
            return True
        unmet = {
            r.get("code") for r in (decision or {}).get("strong_buy_requirements") or [] if not r.get("met")
        }
        return (decision or {}).get("verdict") == "BUY" and bool(unmet) and unmet <= PHOTO_ONLY
    return (flip_score or 0) >= VISION_MIN_FLIP or (rap or 0) > 0


def model_vision_on(settings: Settings) -> bool:
    """A model reads the photos (a key is set and ``AI_VISION_ENABLED``): only then is the local analysis alone
    not a photo check."""
    return settings.ai_vision_enabled and bool(settings.ai_api_key and settings.ai_api_key.get_secret_value())


def vision_done(listing: Any, model: bool) -> bool:
    """The photos were checked. With a model on, the local measures do not count: a listing analysed before the
    model was switched on, or whose model call failed, is checked again (``analyzer`` is "heuristic" then). Unless the
    model gave up on this very gallery (blocked it, or never fit its answer): asking again would cost a request each
    time and get the same answer, so it waits for the photos to change (``trigger == "photos"``)."""
    vision = (listing.identification or {}).get("vision")
    if not vision:
        return False
    if vision.get("model_gave_up") in GALLERY_REASONS:
        return True
    return not model or vision.get("analyzer") not in (None, "heuristic")


def worth_vision(outcome: Any, after_vision: bool = False, *, always: bool | None = None) -> bool:
    """A listing whose photos are worth a check: all photos uploaded, never checked or changed, and either
    promising (value of information) or ``AI_VISION_ALWAYS`` (default: from the settings)."""
    listing, r = outcome.listing, outcome.result
    if listing is None or after_vision:
        return False
    settings = get_settings()
    model = model_vision_on(settings)
    if always is None:
        always = settings.ai_vision_always and model
    # The check waits for the whole gallery: a half-uploaded one would be analysed (and paid for) twice.
    # A gallery the browser could not read in full is queued by its "complete" call instead.
    gallery = [i for i in listing.images if getattr(i, "removed_at", None) is None]
    has_local_photos = bool(gallery) and all(getattr(i, "local_path", None) for i in gallery)
    done = vision_done(listing, model or always)
    voi = voi_says(r)
    worth_checking = always or (
        voi if voi is not None else (r.flip.score >= VISION_MIN_FLIP or (r.risk_adjusted_profit or 0) > 0)
    )
    trigger = getattr(outcome, "trigger", None)
    # Photos are checked once; checked again only when the change touches them (a price change
    # does not: see ``app.agent.stages``).
    return worth_checking and has_local_photos and needs_photo_check(trigger, vision_done=done)


def vision_order(
    outcomes: Iterable[Any], after_vision: bool = False, *, always: bool | None = None
) -> list[str]:
    """Listing ids worth a photo check, best first (risk-adjusted profit, then flip score). The order is the order
    the jobs first run in, ``always`` mode too. Under a request cap the jobs that are held back come back in no
    particular order (each is spread at random over its wait), so "best candidates first" holds for the first pass
    and not for what is left when a daily quota runs out."""
    picked = [o for o in outcomes if worth_vision(o, after_vision, always=always)]
    picked.sort(
        key=lambda o: (
            o.result.risk_adjusted_profit if o.result.risk_adjusted_profit is not None else float("-inf"),
            o.result.flip.score,
        ),
        reverse=True,
    )
    return [str(o.listing_id) for o in picked]


async def queue_vision(listing_ids: list[str], top: int = VISION_HIGH_TOP) -> int:
    """Enqueue the photo checks in this order (see the module doc); returns how many were new."""
    queued = 0
    for i, lid in enumerate(listing_ids):
        if await enqueue(
            "vision_task",
            lid,
            high=i < top,
            job_id=f"vision:{lid}",
            defer_seconds=i * _STEP_SECONDS if i else None,
        ):
            queued += 1
    return queued


async def listings_awaiting_vision(db: AsyncSession, limit: int) -> list[str]:
    """Active listings whose whole gallery is stored here and that no model has analysed yet (never checked, or
    only measured locally), newest first. For the one-off catch-up of what was there before ``AI_VISION_ALWAYS``
    (``python -m app.tools.vision_backfill``); the normal path is ``vision_order`` after an analysis. A gallery the
    model gave up on (blocked, or an answer that never fits) is left out: it would be asked for nothing."""
    photo = (ListingImage.listing_id == Listing.id) & ListingImage.removed_at.is_(None)
    analyzer = Listing.identification["vision"]["analyzer"].astext
    gave_up = Listing.identification["vision"]["model_gave_up"].astext
    rows = await db.execute(
        select(Listing.id)
        .where(
            Listing.status == ListingStatus.ACTIVE,
            Listing.duplicate_of_id.is_(None),
            exists().where(photo),
            ~exists().where(photo, ListingImage.local_path.is_(None)),
            or_(analyzer.is_(None), analyzer == "heuristic"),
            or_(gave_up.is_(None), gave_up.notin_(GALLERY_REASONS)),
        )
        .order_by(Listing.first_seen_at.desc())
        .limit(limit)
    )
    return [str(i) for i in rows.scalars()]


async def worth_queueing(listing_ids: list[str], *, always: bool | None = None) -> list[str]:
    """The listings of ``listing_ids`` whose photo check is worth a paid call, judged by the stored opportunity: never a
    clear PASS (unless ``AI_VISION_ALWAYS`` says every gallery is wanted), and nothing that no longer sells. The photo
    upload path calls this because it has no fresh analysis; a listing with no analysis yet is left to the analysis
    that follows its capture (which queues the check itself, photos permitting)."""
    if not listing_ids:
        return []
    settings = get_settings()
    if always is None:
        always = settings.ai_vision_always and model_vision_on(settings)
    ids = [uuid.UUID(i) for i in listing_ids]
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(
                    Listing.id,
                    Listing.status,
                    Opportunity.id,
                    Opportunity.decision,
                    Opportunity.flip_score,
                    Opportunity.risk_adjusted_profit,
                )
                .outerjoin(Opportunity, Opportunity.listing_id == Listing.id)
                .where(Listing.id.in_(ids))
            )
        ).all()
    picked = []
    for lid, status, opp_id, decision, flip, rap in rows:
        if status not in (ListingStatus.ACTIVE, ListingStatus.RESERVED) or opp_id is None:
            continue
        if always or voi_says_stored(decision, flip, rap):
            picked.append(str(lid))
    return picked


async def queue_vision_if_worth(listing_ids: list[str], top: int = VISION_HIGH_TOP) -> int:
    """``queue_vision_safely`` behind the value-of-information gate (see ``worth_queueing``): for the photo uploads."""
    try:
        picked = await worth_queueing(listing_ids)
    except Exception as exc:  # the gate cannot be read: no paid call on a guess, the next analysis queues it
        log.warning("vision.gate_unreadable", error=type(exc).__name__, listings=len(listing_ids))
        return 0
    if len(picked) < len(listing_ids):
        log.info("vision.not_worth_it", listings=len(listing_ids) - len(picked))
    return await queue_vision_safely(picked, top)


# One model request takes at most the client's timeout times its tries (the Anthropic SDK retries twice, Gemini's client
# does not); a gallery the provider blocks is read in up to three requests (the whole, then each half); the local work
# (decoding, OCR) and the database steps come on top.
CLIENT_TRIES = {"gemini": 1}
VISION_REQUESTS_PER_JOB = 3
VISION_LOCAL_SECONDS = 60
JOB_GRACE_SECONDS = 30  # the job's own timeout is only a backstop behind the budget


def vision_budget_seconds(settings: Settings) -> float:
    """The longest a photo check may take: past it the task stops waiting, counts the try and records a ``timeout``,
    so a hung provider is a visible failure that moves the retry and not a silent cancellation from outside."""
    tries = CLIENT_TRIES.get(settings.ai_provider, 3)
    return settings.ai_timeout_seconds * tries * VISION_REQUESTS_PER_JOB + VISION_LOCAL_SECONDS


def vision_job_timeout(settings: Settings) -> float:
    """The worker's timeout for ``vision_task``: behind the budget, so that the budget is what ends a slow check."""
    return vision_budget_seconds(settings) + JOB_GRACE_SECONDS


async def queue_vision_safely(listing_ids: list[str], top: int = VISION_HIGH_TOP) -> int:
    """``queue_vision`` that logs instead of raising (used after a response is sent)."""
    if not listing_ids:
        return 0
    try:
        return await queue_vision(listing_ids, top)
    except Exception as exc:  # Redis down: the worker's own analyses queue them again later
        log.warning("vision.not_queued", error=type(exc).__name__, listings=len(listing_ids))
        return 0


def gives_up(deferred: VisionDeferred, job_try: int) -> bool:
    """Asking again is pointless: the request itself cannot succeed (``refused`` is a block that comes back for the
    same photos, ``rejected`` a wrong key or model), the answer was cut off twice, or the tries are used up."""
    if deferred.unfixable:
        return True
    if deferred.reason == "truncated":
        return job_try >= MAX_TRUNCATED_TRIES
    if deferred.reason in BAD_ANSWER_REASONS:
        # An answer that is no analysis is asked once more, not five times.
        return job_try >= MAX_BAD_ANSWER_TRIES
    return deferred.asked and job_try >= VISION_MAX_TRIES


async def retry_vision(ctx: dict[str, Any], listing_id: str, deferred: VisionDeferred) -> bool:
    """Back to the queue after a model call that gave no analysis: ``Retry`` when the provider said it can be
    asked again (with a spread, so jobs held back by the same cap do not all return together). Returns True when
    it gave up instead (see ``gives_up``): the caller then records the reason (``mark_model_gave_up``).

    arq counts every run as a try. A run that never asked the model (a cap, a cooldown, an open breaker) gives
    its try back, so a burst of galleries waiting for a free-tier quota is not dropped; runs that asked and got
    no answer count, and after ``VISION_MAX_TRIES`` (at once when asking again cannot help; a cut-off answer is
    asked once more) the listing is left with its local measures only (it is queued again by the next analysis
    that touches its photos, or by ``python -m app.tools.vision_backfill``)."""
    job_try = int(ctx.get("job_try", 1))
    if gives_up(deferred, job_try):
        log.warning("vision.gave_up", listing_id=listing_id, reason=deferred.reason, tries=job_try)
        return True
    if not deferred.asked and ctx.get("redis") is not None and ctx.get("job_id"):
        with contextlib.suppress(Exception):  # at worst the try counts
            await ctx["redis"].decr(retry_key_prefix + str(ctx["job_id"]))
    wait = min(max(deferred.retry_after, 1.0), _MAX_DEFER_SECONDS)
    # Jobs held by the same cap are told the same time: spread them (at least 15 s when nobody was asked, because
    # a backlog of dozens would otherwise wake together every time a window slot frees).
    spread = min(30.0, wait / 4)
    wait += random.uniform(0, spread if deferred.asked else max(spread, _HELD_SPREAD))
    log.info(
        "vision.retry_later", listing_id=listing_id, reason=deferred.reason, wait_s=round(wait), tries=job_try
    )
    raise Retry(defer=wait)


async def mark_model_gave_up(listing_id: str, reason: str) -> None:
    """The model gave up on this gallery (it blocks it, or the answer never fits): record why on the stored local
    measures, which stay the listing's analysis ("heuristic": the photos still do not count as checked by a model).
    A listing carrying the reason is not queued again by the analyses that follow, only when its photos change.
    Never touches a model analysis; at worst nothing is recorded and the listing is asked again later."""
    reason = "bad_answer" if reason in BAD_ANSWER_REASONS else reason
    if reason not in GALLERY_REASONS:
        return
    with contextlib.suppress(Exception):
        async with session_scope() as s:
            listing = await s.get(Listing, uuid.UUID(listing_id))
            ident = dict(listing.identification or {}) if listing is not None else {}
            vision = dict(ident.get("vision") or {})
            if listing is None or not vision or vision.get("analyzer") not in (None, "heuristic"):
                return
            note = GAVE_UP_NOTES[reason]
            vision["model_gave_up"] = reason
            vision["notes"] = [*(n for n in vision.get("notes") or [] if n != note), note]
            listing.identification = {**ident, "vision": vision}
