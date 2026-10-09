"""When a listing stops being trustworthy as "available": the "da verificare" state.

A listing last read as active (or reserved) a long time ago may have been sold or removed since.
Nothing is deduced from silence - it becomes ``to_verify``, which is never shown as buyable
(feed, alerts, badges), and goes back to what the page says the next time the user's browser
sees it. ``sold`` and ``removed`` need positive evidence and are never produced here.

Thresholds (settings, per status): active ``STALE_ACTIVE_HOURS`` (48), reserved
``STALE_RESERVED_HOURS`` (24) - a reservation usually resolves within a day.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import Listing, Opportunity
from app.domain.enums import ListingStatus


@dataclass(frozen=True)
class VerifyThresholds:
    active: timedelta = timedelta(hours=48)
    reserved: timedelta = timedelta(hours=24)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> VerifyThresholds:
        s = settings or get_settings()
        return cls(timedelta(hours=s.stale_active_hours), timedelta(hours=s.stale_reserved_hours))

    def for_status(self, status: ListingStatus | str) -> timedelta | None:
        """Only listings that claim to be available can go stale."""
        status = ListingStatus(status)
        if status == ListingStatus.ACTIVE:
            return self.active
        if status == ListingStatus.RESERVED:
            return self.reserved
        return None


def last_verification(last_verified_at: datetime | None, last_seen_at: datetime | None) -> datetime | None:
    """When the state was last confirmed (a record never verified counts from its last sighting)."""
    return last_verified_at or last_seen_at


def needs_verification(
    status: ListingStatus | str,
    last_verified_at: datetime | None,
    last_seen_at: datetime | None,
    now: datetime,
    thresholds: VerifyThresholds | None = None,
) -> bool:
    limit = (thresholds or VerifyThresholds()).for_status(status)
    if limit is None:
        return False
    reference = last_verification(last_verified_at, last_seen_at)
    return reference is None or now - reference >= limit


def is_buyable(status: ListingStatus | str) -> bool:
    """Only a listing currently read as active is offered as something to buy."""
    return ListingStatus(status) == ListingStatus.ACTIVE


async def mark_stale_listings(
    session: AsyncSession, now: datetime | None = None, thresholds: VerifyThresholds | None = None
) -> list[uuid.UUID]:
    """Turn the listings that are too old to trust into ``to_verify``; idempotent."""
    now = now or datetime.now(UTC)
    th = thresholds or VerifyThresholds.from_settings()
    reference = func.coalesce(Listing.last_verified_at, Listing.last_seen_at)
    stale = or_(
        (Listing.status == ListingStatus.ACTIVE.value) & (reference <= now - th.active),
        (Listing.status == ListingStatus.RESERVED.value) & (reference <= now - th.reserved),
    )
    rows = (
        (
            await session.execute(
                update(Listing)
                .where(stale)
                .values(
                    status_before_verify=Listing.status,
                    status=ListingStatus.TO_VERIFY.value,
                    status_changed_at=now,
                )
                .returning(Listing.id)
            )
        )
        .scalars()
        .all()
    )
    ids = list(rows)
    if ids:
        await session.execute(
            update(Opportunity).where(Opportunity.listing_id.in_(ids)).values(is_active=False)
        )
    return ids
