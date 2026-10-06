"""User actions on a listing's tracking, shared by the web app and the browser extension."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Favorite, Listing, Opportunity
from app.domain.enums import FavoriteState
from app.tracking.policy import schedule


async def set_tracked(
    session: AsyncSession, user_id: uuid.UUID, listing: Listing, track: bool, now: datetime | None = None
) -> datetime | None:
    """Track (adaptive periodic checks + the user's watching list) or stop tracking a listing.

    Returns the next check time when tracking. Stopping keeps the listing, its history and its
    analyses: only the checks stop (market-scan listings keep their own schedule).
    """
    now = now or datetime.now(UTC)
    fav = (
        await session.execute(
            select(Favorite).where(Favorite.user_id == user_id, Favorite.listing_id == listing.id)
        )
    ).scalar_one_or_none()
    if not track:
        keep_schedule = listing.acquisition_mode == "provider_scan"
        await session.execute(
            update(Listing)
            .where(Listing.id == listing.id)
            .values(tracked_at=None, next_check_at=listing.next_check_at if keep_schedule else None)
        )
        if fav is not None and fav.state == FavoriteState.WATCHING:
            await session.delete(fav)
        return None
    tracked_at = listing.tracked_at or now
    next_check = schedule(
        status=listing.status,
        tracked_at=tracked_at,
        acquisition_mode=listing.acquisition_mode,
        now=now,
        published_at=listing.published_at,
        favourite_count=listing.favourite_count,
        unchanged_checks=listing.unchanged_checks,
        check_failures=listing.check_failures,
    )
    await session.execute(
        update(Listing)
        .where(Listing.id == listing.id)
        .values(tracked_at=tracked_at, next_check_at=listing.next_check_at or next_check)
    )
    if fav is None:
        opp_id = (
            await session.execute(select(Opportunity.id).where(Opportunity.listing_id == listing.id))
        ).scalar_one_or_none()
        session.add(
            Favorite(
                user_id=user_id, listing_id=listing.id, opportunity_id=opp_id, state=FavoriteState.WATCHING
            )
        )
    return next_check
