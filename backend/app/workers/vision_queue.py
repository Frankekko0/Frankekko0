"""Photo (vision) checks queued best candidates first.

arq has no priorities inside a queue: jobs run in the order of their score (the time they are
due). So the candidates of a batch are sorted by risk-adjusted profit, then flip score; the best
``VISION_HIGH_TOP`` go to the high queue (served by its own worker next to the bulk analyses),
the rest to the default queue, and each job is due one millisecond after the previous one, so
both queues run them in that order. Enqueueing never blocks or fails a capture: the API queues
them after the response is sent and a Redis outage only costs the photo checks.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.agent.stages import needs_photo_check
from app.core.logging import get_logger
from app.workers.queue import enqueue

log = get_logger(__name__)

VISION_MIN_FLIP = 60
VISION_HIGH_TOP = 3
_STEP_SECONDS = 0.001


def worth_vision(outcome: Any, after_vision: bool = False) -> bool:
    """A listing whose photos are worth a check: promising, all photos uploaded, never checked or changed."""
    listing, r = outcome.listing, outcome.result
    if listing is None or after_vision:
        return False
    # The check waits for the whole gallery: a half-uploaded one would be analysed (and paid for) twice.
    # A gallery the browser could not read in full is queued by its "complete" call instead.
    gallery = [i for i in listing.images if getattr(i, "removed_at", None) is None]
    has_local_photos = bool(gallery) and all(getattr(i, "local_path", None) for i in gallery)
    vision_done = bool((listing.identification or {}).get("vision"))
    worth_checking = r.flip.score >= VISION_MIN_FLIP or (r.risk_adjusted_profit or 0) > 0
    trigger = getattr(outcome, "trigger", None)
    # Photos are checked once; checked again only when the change touches them (a price change
    # does not: see ``app.agent.stages``).
    return worth_checking and has_local_photos and needs_photo_check(trigger, vision_done=vision_done)


def vision_order(outcomes: Iterable[Any], after_vision: bool = False) -> list[str]:
    """Listing ids worth a photo check, best first (risk-adjusted profit, then flip score)."""
    picked = [o for o in outcomes if worth_vision(o, after_vision)]
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


async def queue_vision_safely(listing_ids: list[str], top: int = VISION_HIGH_TOP) -> int:
    """``queue_vision`` that logs instead of raising (used after a response is sent)."""
    if not listing_ids:
        return 0
    try:
        return await queue_vision(listing_ids, top)
    except Exception as exc:  # Redis down: the worker's own analyses queue them again later
        log.warning("vision.not_queued", error=type(exc).__name__, listings=len(listing_ids))
        return 0
