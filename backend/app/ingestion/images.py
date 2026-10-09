"""A listing's photos over time: never deleted and re-inserted, only added, moved or retired.

Each photo has a stable ``image_key`` (see ``app.media.keys``). A capture that carries the item's
complete gallery (``images_authoritative``) retires the photos it no longer lists
(``removed_at``); any other capture only adds or re-orders. The local copy, hashes and archive
state of a photo survive every re-capture, and a retired photo keeps its copy as history.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing, ListingImage
from app.marketplace.base import ProviderImage
from app.media.keys import image_key

MAX_IMAGES = 20


@dataclass
class ImageSyncReport:
    added: int = 0
    moved: int = 0
    retired: int = 0
    revived: int = 0


def incoming_rows(
    listing_id: uuid.UUID, images: list[ProviderImage], source: str | None, now: datetime
) -> list[dict[str, Any]]:
    """Rows for a capture's photos, original order, the same photo listed twice once."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for img in images:
        key = image_key(img.url)
        if key in seen:
            continue
        seen.add(key)
        if len(rows) >= MAX_IMAGES:
            break
        rows.append(
            {
                "listing_id": listing_id,
                "image_key": key,
                "position": len(rows),
                "url": img.url,
                "phash": img.phash,
                "width": img.width,
                "height": img.height,
                "source": source,
                "first_seen_at": now,
                "last_seen_at": now,
            }
        )
    return rows


async def sync_images(
    session: AsyncSession, plans: dict[uuid.UUID, tuple[list[dict[str, Any]], bool]], now: datetime
) -> ImageSyncReport:
    """Apply ``{listing_id: (incoming rows, is_complete_gallery)}`` and refresh ``photo_count``."""
    report = ImageSyncReport()
    if not plans:
        return report
    existing = (
        await session.execute(
            select(
                ListingImage.id,
                ListingImage.listing_id,
                ListingImage.image_key,
                ListingImage.position,
                ListingImage.removed_at,
            ).where(ListingImage.listing_id.in_(list(plans)))
        )
    ).all()
    by_listing: dict[uuid.UUID, dict[str, Any]] = defaultdict(dict)
    for r in existing:
        # A key can exist as a retired row and as a current one; the current one wins.
        if r.image_key not in by_listing[r.listing_id] or r.removed_at is None:
            by_listing[r.listing_id][r.image_key] = r
    inserts: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    retire_ids: list[int] = []
    for listing_id, (rows, complete) in plans.items():
        known = by_listing.get(listing_id, {})
        for row in rows:
            ex = known.get(row["image_key"])
            if ex is None:
                inserts.append(row)
                report.added += 1
                continue
            upd: dict[str, Any] = {
                "id": ex.id,
                "position": row["position"],
                "url": row["url"],
                "last_seen_at": now,
            }
            if ex.removed_at is not None:
                upd["removed_at"] = None
                report.revived += 1
            elif ex.position != row["position"]:
                report.moved += 1
            for col in ("phash", "width", "height"):
                if row[col] is not None:
                    upd[col] = row[col]
            updates.append(upd)
        if complete:
            incoming_keys = {r["image_key"] for r in rows}
            for key, ex in known.items():
                if key not in incoming_keys and ex.removed_at is None:
                    retire_ids.append(ex.id)
    if inserts:
        await session.execute(pg_insert(ListingImage.__table__).on_conflict_do_nothing(), inserts)
    if updates:
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for u in updates:
            groups[tuple(sorted(u))].append(u)
        for group in groups.values():
            await session.execute(update(ListingImage), group)
    if retire_ids:
        await session.execute(
            update(ListingImage).where(ListingImage.id.in_(retire_ids)).values(removed_at=now)
        )
        report.retired = len(retire_ids)
    current = (
        select(func.count())
        .where(ListingImage.listing_id == Listing.id, ListingImage.removed_at.is_(None))
        .scalar_subquery()
    )
    await session.execute(update(Listing).where(Listing.id.in_(list(plans))).values(photo_count=current))
    return report
