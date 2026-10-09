"""What one observation of a listing records, and when it is worth a new row (pure).

The listing row always says when it was last seen and verified. The *history* (``listing_snapshots``,
append-only) grows only when something changed - price, status, favourites, photos, a richer
capture - or after a heartbeat, so scrolling past the same card ten times is not ten rows.

Every value in a snapshot payload carries its type:

* ``observed`` - read from the page or card as shown (price, status, favourites, fees, photos);
* ``declared`` - written or chosen by the seller (title, description, brand, size, condition...);
* ``inferred`` - computed from something else (a date from "3 days ago").

``c`` is how the value was obtained: ``read`` directly, ``approx`` when derived. ``s`` is the
capture level it came from (card / full). Finer origins (which part of the page) arrive with the
extension's field-level sources in phase 3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.domain.enums import CaptureLevel
from app.marketplace.base import ProviderListing
from app.media.keys import image_keys

PAYLOAD_SCHEMA = 1
DEFAULT_HEARTBEAT = timedelta(hours=6)

OBSERVED_FIELDS = (
    "price",
    "currency",
    "status",
    "favourite_count",
    "view_count",
    "shipping_fee",
    "buyer_protection_fee",
)
DECLARED_FIELDS = (
    "title",
    "description",
    "brand",
    "category",
    "subcategory",
    "size",
    "condition",
    "color",
    "material",
    "country",
)


@dataclass(frozen=True)
class LastSnapshot:
    """The newest stored snapshot of a listing, as far as change detection needs."""

    observed_at: datetime
    status: str | None
    price: Any
    favourite_count: int | None
    capture_level: str | None
    image_keys: tuple[str, ...]
    images_complete: bool


@dataclass(frozen=True)
class Current:
    """The same facts taken from the observation being stored."""

    status: str | None
    price: Any
    favourite_count: int | None
    capture_level: str
    image_keys: tuple[str, ...]
    images_complete: bool


def current_from(pl: ProviderListing, status: str | None) -> Current:
    link_only = pl.capture_level == CaptureLevel.LINK
    return Current(
        status=None if link_only else status,
        price=None if link_only else pl.price,
        favourite_count=pl.favourite_count,
        capture_level=CaptureLevel(pl.capture_level).value,
        image_keys=tuple(image_keys([i.url for i in pl.images])),
        images_complete=bool(pl.images_authoritative and pl.images),
    )


def photos_changed(prev: LastSnapshot, cur: Current) -> bool:
    """Compare what both observations could see. A card shows only the cover: a longer or shorter
    set is a change only when the newer one is the item's complete gallery."""
    if not prev.image_keys or not cur.image_keys:
        return False
    n = min(len(prev.image_keys), len(cur.image_keys))
    if prev.image_keys[:n] != cur.image_keys[:n]:
        return True
    if len(cur.image_keys) > len(prev.image_keys):
        return cur.images_complete or prev.images_complete
    if len(cur.image_keys) < len(prev.image_keys):
        return cur.images_complete and prev.images_complete
    return False


def snapshot_reasons(
    prev: LastSnapshot | None,
    cur: Current,
    now: datetime,
    heartbeat: timedelta = DEFAULT_HEARTBEAT,
) -> list[str]:
    """Why this observation deserves a new history row; empty when it adds nothing."""
    from app.domain.enums import CAPTURE_RANK

    if prev is None:
        return ["first"]
    reasons: list[str] = []
    if cur.price is not None and prev.price is not None and cur.price != prev.price:
        reasons.append("price")
    if cur.status is not None and cur.status != prev.status:
        reasons.append("status")
    if cur.favourite_count is not None and cur.favourite_count != prev.favourite_count:
        reasons.append("favourites")
    if photos_changed(prev, cur):
        reasons.append("photos")
    if prev.capture_level and CAPTURE_RANK.get(cur.capture_level, 0) > CAPTURE_RANK.get(
        prev.capture_level, 0
    ):
        reasons.append("richer")
    if not reasons and now - prev.observed_at >= heartbeat:
        reasons.append("heartbeat")
    return reasons


def observation_payload(pl: ProviderListing, status: str | None) -> dict[str, Any]:
    """The fields this observation actually contained, each with its type. Absent fields are
    absent: "not visible in this capture" is never recorded as "empty"."""
    level = CaptureLevel(pl.capture_level).value
    link_only = level == CaptureLevel.LINK.value
    values: dict[str, Any] = {
        "price": None if link_only else str(pl.price),
        "currency": None if link_only else pl.currency,
        "status": None if link_only else status,
        "favourite_count": pl.favourite_count,
        "view_count": pl.view_count,
        "shipping_fee": None if pl.shipping_fee is None else str(pl.shipping_fee),
        "buyer_protection_fee": None if pl.buyer_protection_fee is None else str(pl.buyer_protection_fee),
    }
    fields: dict[str, dict[str, Any]] = {
        k: {"v": v, "t": "observed", "c": "read", "s": level} for k, v in values.items() if v is not None
    }
    declared = {
        "title": pl.title,
        "description": pl.description or None,
        "brand": pl.brand,
        "category": pl.category,
        "subcategory": pl.subcategory,
        "size": pl.size,
        "condition": pl.condition,
        "color": pl.color,
        "material": pl.material,
        "country": pl.country,
    }
    for k, v in declared.items():
        if v:
            fields[k] = {"v": v, "t": "declared", "c": "read", "s": level}
    if pl.published_at is not None:
        kind = pl.published_at_kind or "reported"
        fields["published_at"] = {
            "v": pl.published_at.isoformat(),
            "t": "inferred" if kind == "relative" else "observed",
            "c": "approx" if kind == "relative" else "read",
            "s": level,
        }
    if pl.images:
        fields["photos"] = {
            "v": len(pl.images),
            "t": "observed",
            "c": "read",
            "s": level,
            "complete": bool(pl.images_authoritative),
        }
    return {"schema": PAYLOAD_SCHEMA, "fields": fields}


def snapshot_row(
    listing_id: uuid.UUID,
    pl: ProviderListing,
    status: str | None,
    now: datetime,
    mode: str,
    reasons: list[str],
    *,
    extension_version: str | None = None,
    parser_version: str | None = None,
) -> dict[str, Any]:
    cur = current_from(pl, status)
    return {
        "listing_id": listing_id,
        "observed_at": now,
        "acquisition_mode": mode,
        "capture_level": cur.capture_level,
        "status": cur.status,
        "price": cur.price,
        "currency": pl.currency,
        "favourite_count": pl.favourite_count,
        "view_count": pl.view_count,
        "photo_count": len(pl.images) if pl.images else None,
        "note": None,
        "reason": ",".join(reasons)[:40],
        "payload": observation_payload(pl, status),
        "image_set": list(cur.image_keys) or None,
        "image_set_complete": cur.images_complete,
        "extension_version": extension_version,
        "parser_version": parser_version,
    }
