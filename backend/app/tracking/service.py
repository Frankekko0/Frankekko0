"""Applying status observations that do not carry a full listing (page gone, unreachable, email),
and logging acquisition attempts."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import AcquisitionAttempt, Listing, ListingSnapshot, Opportunity
from app.domain.enums import AcquisitionMode, ListingStatus
from app.tracking.policy import schedule
from app.tracking.status import Observation, StatusState, StatusUpdate, apply_observation

log = get_logger(__name__)


class TrackingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def observe(
        self, observations: dict[uuid.UUID, Observation], mode: AcquisitionMode | str
    ) -> dict[uuid.UUID, StatusUpdate]:
        """Fold observations into stored listings: lifecycle columns, next check, a snapshot each."""
        if not observations:
            return {}
        mode = AcquisitionMode(mode)
        rows = (
            await self.session.execute(
                select(
                    Listing.id,
                    Listing.status,
                    Listing.published_at,
                    Listing.last_active_at,
                    Listing.last_active_price,
                    Listing.sold_at,
                    Listing.sold_detected_at,
                    Listing.removed_at,
                    Listing.check_failures,
                    Listing.unchanged_checks,
                    Listing.tracked_at,
                    Listing.acquisition_mode,
                    Listing.favourite_count,
                    Listing.price,
                    Listing.currency,
                    Opportunity.flip_score,
                )
                .outerjoin(Opportunity, Opportunity.listing_id == Listing.id)
                .where(Listing.id.in_(list(observations)))
            )
        ).all()
        out: dict[uuid.UUID, StatusUpdate] = {}
        snapshots: list[dict[str, Any]] = []
        for r in rows:
            obs = observations[r.id]
            upd = apply_observation(
                StatusState(
                    status=ListingStatus(r.status),
                    published_at=r.published_at,
                    last_active_at=r.last_active_at,
                    last_active_price=r.last_active_price,
                    sold_at=r.sold_at,
                    sold_detected_at=r.sold_detected_at,
                    removed_at=r.removed_at,
                    check_failures=r.check_failures,
                    unchanged_checks=r.unchanged_checks,
                ),
                obs,
            )
            out[r.id] = upd
            values: dict[str, Any] = {
                "status": upd.status.value,
                "last_active_at": upd.last_active_at,
                "last_active_price": upd.last_active_price,
                "sold_at": upd.sold_at,
                "sold_detected_at": upd.sold_detected_at,
                "removed_at": upd.removed_at,
                "days_to_sell": upd.days_to_sell,
                "check_failures": upd.check_failures,
                "unchanged_checks": upd.unchanged_checks,
                "last_checked_at": obs.observed_at,
                "next_check_at": schedule(
                    status=upd.status,
                    tracked_at=r.tracked_at,
                    acquisition_mode=r.acquisition_mode,
                    now=obs.observed_at,
                    published_at=r.published_at,
                    favourite_count=r.favourite_count,
                    flip_score=r.flip_score,
                    unchanged_checks=upd.unchanged_checks,
                    check_failures=upd.check_failures,
                ),
            }
            if upd.reachable:
                values["last_seen_at"] = obs.observed_at
            if upd.changed:
                values["status_changed_at"] = obs.observed_at
            if obs.price is not None and upd.reachable:
                values["price"] = obs.price
            await self.session.execute(update(Listing).where(Listing.id == r.id).values(**values))
            snapshots.append(
                {
                    "listing_id": r.id,
                    "observed_at": obs.observed_at,
                    "acquisition_mode": mode.value,
                    "capture_level": None,
                    "status": upd.status.value if upd.reachable else None,
                    "price": obs.price,
                    "currency": r.currency,
                    "favourite_count": None,
                    "view_count": None,
                    "photo_count": None,
                    "note": (obs.note or upd.note)[:200],
                }
            )
        if snapshots:
            await self.session.execute(insert(ListingSnapshot.__table__), snapshots)
        if any(u.changed and u.status != ListingStatus.ACTIVE for u in out.values()):
            closed = [lid for lid, u in out.items() if u.changed and u.status != ListingStatus.ACTIVE]
            await self.session.execute(
                update(Opportunity).where(Opportunity.listing_id.in_(closed)).values(is_active=False)
            )
        return out


@dataclass
class Attempt:
    mode: str
    action: str
    listing_id: uuid.UUID | None = None
    vinted_id: str | None = None
    outcome: str = "ok"
    http_status: int | None = None
    message: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int | None = None

    def fail(self, outcome: str, message: str, **detail: Any) -> None:
        self.outcome = outcome
        self.message = message
        self.detail.update(detail)


@contextmanager
def timed(attempt: Attempt) -> Iterator[Attempt]:
    start = time.perf_counter()
    try:
        yield attempt
    finally:
        attempt.duration_ms = round((time.perf_counter() - start) * 1000)


async def record_attempts(session: AsyncSession, attempts: list[Attempt]) -> None:
    """Persist attempts and log failures in one readable line each."""
    if not attempts:
        return
    for a in attempts:
        if a.outcome not in ("ok", "unchanged", "skipped"):
            log.warning(
                "acquisition.failed",
                mode=a.mode,
                action=a.action,
                outcome=a.outcome,
                vinted_id=a.vinted_id,
                http_status=a.http_status,
                reason=a.message,
            )
    await session.execute(
        insert(AcquisitionAttempt.__table__),
        [
            {
                "listing_id": a.listing_id,
                "vinted_id": a.vinted_id,
                "mode": a.mode,
                "action": a.action,
                "outcome": a.outcome,
                "http_status": a.http_status,
                "message": (a.message or None) and a.message[:1000],
                "detail": a.detail or None,
                "started_at": a.started_at,
                "duration_ms": a.duration_ms,
            }
            for a in attempts
        ],
    )
