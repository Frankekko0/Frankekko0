"""Where else a listing's photos appear: recycled photos and catalogue/stock photos.

Each photo's perceptual hash (dHash, 64 bits) is compared with every archived photo of *other*
listings. A re-uploaded copy of the same picture differs by a few bits after resizing and
recompression; two different photos of similar items differ by far more. Matches are counted only
across different sellers (a seller reposting their own item is not suspicious):

* one or two other sellers -> the photo was taken from someone else's listing ("foto riciclata");
* three or more sellers -> a catalogue or stock picture, not the item in hand.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_DISTANCE = 4  # bits out of 64: same picture, recompressed or resized
CATALOG_SELLERS = 3

_MATCHES = text(
    """
    SELECT img.listing_id, l.seller_id, coalesce(l.duplicate_of_id, l.id) AS root
    FROM listing_images img
    JOIN listings l ON l.id = img.listing_id
    WHERE img.phash IS NOT NULL
      AND img.listing_id <> :listing_id
      AND bit_count(('x' || img.phash)::bit(64) # ('x' || :h)::bit(64)) <= :d
    LIMIT 200
    """
)


@dataclass
class PhotoProvenance:
    reused_photos: list[int] = field(default_factory=list)
    catalog_photos: list[int] = field(default_factory=list)
    other_listings: dict[int, list[uuid.UUID]] = field(default_factory=dict)


async def photo_provenance(
    session: AsyncSession,
    listing_id: uuid.UUID,
    seller_id: uuid.UUID | None,
    root_id: uuid.UUID,
    hashes: list[str | None],
) -> PhotoProvenance:
    out = PhotoProvenance()
    for i, h in enumerate(hashes):
        if not h or len(h) != 16:
            continue
        rows = (await session.execute(_MATCHES, {"listing_id": listing_id, "h": h, "d": MAX_DISTANCE})).all()
        sellers: dict[uuid.UUID | None, set[uuid.UUID]] = defaultdict(set)
        for r in rows:
            if r.root == root_id or (seller_id is not None and r.seller_id == seller_id):
                continue  # the same item or the same seller's repost
            sellers[r.seller_id].add(r.listing_id)
        if not sellers:
            continue
        out.other_listings[i] = sorted({lid for ids in sellers.values() for lid in ids})[:10]
        if len(sellers) >= CATALOG_SELLERS:
            out.catalog_photos.append(i)
        else:
            out.reused_photos.append(i)
    return out
