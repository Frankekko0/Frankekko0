"""Visual repost detection from stored photo hashes.

Once a photo has been copied, its perceptual hash (dHash) is known. The same seller listing the
same pictures again (a repost, usually with a new price) is recorded as a duplicate of the older
listing, with the evidence that decided it. Another seller using the same picture is *not* a
repost: that is the photo-reuse signal read by ``app.vision.provenance``.

A single near-identical photo is enough (distance <= ``MAX_DISTANCE`` of 64 bits); the listing
that was seen first stays the original.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing

MAX_DISTANCE = 4

_FIND = text(
    """
    SELECT img.image_key AS key, img.phash AS phash, other.id AS other_id,
           coalesce(other.duplicate_of_id, other.id) AS root,
           bit_count(('x' || img.phash)::bit(64) # ('x' || mine.phash)::bit(64)) AS distance
      FROM listing_images mine
      JOIN listings me ON me.id = mine.listing_id
      JOIN listings other ON other.seller_id = me.seller_id
                         AND other.id <> me.id
                         AND other.first_seen_at < me.first_seen_at
      JOIN listing_images img ON img.listing_id = other.id AND img.phash IS NOT NULL
                             AND img.removed_at IS NULL
     WHERE mine.listing_id = :listing_id AND mine.phash IS NOT NULL AND mine.removed_at IS NULL
       AND bit_count(('x' || img.phash)::bit(64) # ('x' || mine.phash)::bit(64)) <= :d
     ORDER BY other.first_seen_at, distance
     LIMIT 1
    """
)


async def link_visual_reposts(
    session: AsyncSession, listing_ids: list[uuid.UUID], now: datetime | None = None
) -> dict[uuid.UUID, dict[str, Any]]:
    """Mark listings that repeat an older listing of the same seller. Returns the evidence per
    listing newly marked (listings already marked as duplicates are left as they are)."""
    marked: dict[uuid.UUID, dict[str, Any]] = {}
    if not listing_ids:
        return marked
    pending = (
        (
            await session.execute(
                text(
                    "SELECT id FROM listings WHERE id = ANY(:ids) AND duplicate_of_id IS NULL "
                    "AND seller_id IS NOT NULL"
                ),
                {"ids": list(listing_ids)},
            )
        )
        .scalars()
        .all()
    )
    for lid in pending:
        hit = (await session.execute(_FIND, {"listing_id": lid, "d": MAX_DISTANCE})).first()
        if hit is None:
            continue
        evidence = {
            "rule": "photo_hash",
            "of": str(hit.other_id),
            "phash": hit.phash,
            "distance": int(hit.distance),
            "image_key": hit.key,
        }
        await session.execute(
            update(Listing)
            .where(Listing.id == lid)
            .values(duplicate_of_id=hit.root, duplicate_evidence=evidence)
        )
        marked[lid] = evidence
    return marked
