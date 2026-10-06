"""Removal of images and seller data that never belonged to the listing.

Earlier parser versions read every ``"full_size_url"`` and ``"feedback_*"`` in an item page's
scripts. Those scripts also describe the signed-in user (their profile photo and reviews), the
seller's profile and suggested items, so listings could carry the user's own avatar among their
photos and the user's own rating as the seller's. Both distort the analysis (photo-based checks,
"photos reused" signals, seller reliability).

What gives them away, without guessing:

* an image URL - or the same archived file - attached to two or more *different* real listings
  (a listing and its own reposts excepted): an item photo belongs to one listing; the user's
  avatar appeared in every item page they opened, a seller's avatar in all their listings;
* one exact (rating, review count) pair on many different sellers, far more than any other
  pair: the signed-in user's own profile copied onto each seller. Those sellers go back to
  "unknown" until their listings are seen again (no value is invented).

Affected listings lose the stored photo analysis and are re-analysed.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Listing, ListingImage, Seller

log = get_logger(__name__)

PROVIDER = "vinted"
SELLER_PAIR_MIN_SELLERS = 5
SELLER_PAIR_MIN_REVIEWS = 5
SELLER_PAIR_DOMINANCE = 3  # times more sellers than the next most frequent pair


@dataclass
class CleanupReport:
    images_removed: int = 0
    sellers_reset: int = 0
    listing_ids: list[uuid.UUID] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "images_removed": self.images_removed,
            "sellers_reset": self.sellers_reset,
            "listings_affected": len(self.listing_ids),
        }


async def _foreign_image_ids(session: AsyncSession) -> tuple[list[int], set[uuid.UUID]]:
    rows = (
        await session.execute(
            select(
                ListingImage.id,
                ListingImage.listing_id,
                ListingImage.url,
                ListingImage.sha256,
                Listing.duplicate_of_id,
            )
            .join(Listing, Listing.id == ListingImage.listing_id)
            .where(Listing.provider == PROVIDER)
        )
    ).all()
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for r in rows:
        groups[("url", r.url)].append(r)
        if r.sha256:
            groups[("sha", r.sha256)].append(r)
    image_ids: set[int] = set()
    listings: set[uuid.UUID] = set()
    for members in groups.values():
        roots = {m.duplicate_of_id or m.listing_id for m in members}
        if len({m.listing_id for m in members}) < 2 or len(roots) < 2:
            continue  # one listing, or a listing and its own reposts
        for m in members:
            image_ids.add(m.id)
            listings.add(m.listing_id)
    return sorted(image_ids), listings


async def _copied_seller_ids(session: AsyncSession) -> list[uuid.UUID]:
    pairs = (
        await session.execute(
            select(Seller.rating, Seller.review_count, func.count(Seller.id).label("n"))
            .where(
                Seller.provider == PROVIDER,
                Seller.rating.is_not(None),
                Seller.review_count >= SELLER_PAIR_MIN_REVIEWS,
            )
            .group_by(Seller.rating, Seller.review_count)
            .order_by(func.count(Seller.id).desc())
            .limit(2)
        )
    ).all()
    if not pairs or pairs[0].n < SELLER_PAIR_MIN_SELLERS:
        return []
    runner_up = pairs[1].n if len(pairs) > 1 else 0
    if pairs[0].n < SELLER_PAIR_DOMINANCE * max(1, runner_up):
        return []
    top = pairs[0]
    return list(
        (
            await session.execute(
                select(Seller.id).where(
                    Seller.provider == PROVIDER,
                    Seller.rating == top.rating,
                    Seller.review_count == top.review_count,
                )
            )
        )
        .scalars()
        .all()
    )


async def clean_foreign_data(session: AsyncSession, dry_run: bool = False) -> CleanupReport:
    """Remove foreign images and copied seller ratings; returns the listings to re-analyse."""
    report = CleanupReport()
    image_ids, listings = await _foreign_image_ids(session)
    seller_ids = await _copied_seller_ids(session)
    if seller_ids:
        listings |= set(
            (
                await session.execute(
                    select(Listing.id).where(Listing.seller_id.in_(seller_ids), Listing.provider == PROVIDER)
                )
            )
            .scalars()
            .all()
        )
    report.images_removed = len(image_ids)
    report.sellers_reset = len(seller_ids)
    report.listing_ids = sorted(listings)
    if dry_run or not listings:
        return report

    if image_ids:
        await session.execute(delete(ListingImage).where(ListingImage.id.in_(image_ids)))
        # Close the gaps in the gallery order (two steps: (listing, position) is unique).
        await session.execute(
            update(ListingImage)
            .where(ListingImage.listing_id.in_(listings))
            .values(position=ListingImage.position + 1000)
        )
        remaining = (
            await session.execute(
                select(ListingImage.id, ListingImage.listing_id)
                .where(ListingImage.listing_id.in_(listings))
                .order_by(ListingImage.listing_id, ListingImage.position)
            )
        ).all()
        counter: dict[uuid.UUID, int] = defaultdict(int)
        for r in remaining:
            await session.execute(
                update(ListingImage).where(ListingImage.id == r.id).values(position=counter[r.listing_id])
            )
            counter[r.listing_id] += 1
    if seller_ids:
        await session.execute(
            update(Seller)
            .where(Seller.id.in_(seller_ids))
            .values(rating=None, review_count=0, reliability_score=None, reliability_details=None)
        )
    # Photo-based results computed on the wrong photos are dropped (recomputed on the next analysis).
    rows = (
        await session.execute(select(Listing.id, Listing.identification).where(Listing.id.in_(listings)))
    ).all()
    counts = dict(
        (
            await session.execute(
                select(ListingImage.listing_id, func.count())
                .where(ListingImage.listing_id.in_(listings))
                .group_by(ListingImage.listing_id)
            )
        ).all()
    )
    for r in rows:
        ident = dict(r.identification or {})
        ident.pop("vision", None)
        ident.pop("photos_reused_by_other_seller", None)
        await session.execute(
            update(Listing)
            .where(Listing.id == r.id)
            .values(identification=ident, photo_count=counts.get(r.id, 0))
        )
    log.warning("cleanup.foreign_data_removed", **report.as_dict())
    return report
