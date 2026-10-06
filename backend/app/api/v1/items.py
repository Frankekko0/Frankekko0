"""Archive and tracking of every listing: search, filters, CSV export, per-item history."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core.cache import NS_FEED, cache
from app.core.errors import NotFoundError
from app.core.rate_limit import RateLimit
from app.db.models import AcquisitionAttempt, Listing, ListingSnapshot, Opportunity
from app.media.archive import schedule_archive
from app.schemas.common import Page
from app.schemas.items import (
    AttemptOut,
    ItemDetailOut,
    ItemImageOut,
    ItemOut,
    SnapshotOut,
    TrackingOut,
)
from app.tracking.actions import set_tracked
from app.tracking.queries import ItemFilters, ItemQueries, build_query, export_csv, row_dict
from app.tracking.summary import analysis_summary

router = APIRouter(prefix="/items", tags=["items"])

StatusParam = Query(None, pattern="^(active|reserved|sold|removed|unknown)$")


def filters(
    q: str | None = Query(None, max_length=120),
    brand: str | None = Query(None, max_length=120),
    status: str | None = StatusParam,
    mode: str | None = Query(None, max_length=24),
    capture_level: str | None = Query(None, pattern="^(link|card|full)$"),
    data_quality: str | None = Query(None, pattern="^(ok|limited|insufficient)$"),
    tracked: bool | None = None,
    analyzed: bool | None = None,
    date_field: Literal["first_seen", "analyzed", "last_checked", "published"] = "first_seen",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_score: int | None = Query(None, ge=0, le=100),
    max_score: int | None = Query(None, ge=0, le=100),
    sort: Literal["recent", "score", "price_asc", "price_desc", "profit", "last_checked"] = "recent",
) -> ItemFilters:
    return ItemFilters(
        q=q,
        brand=brand,
        status=status,
        mode=mode,
        capture_level=capture_level,
        data_quality=data_quality,
        tracked=tracked,
        analyzed=analyzed,
        date_field=date_field,
        date_from=date_from,
        date_to=date_to,
        min_score=min_score,
        max_score=max_score,
        sort=sort,
    )


@router.get("", response_model=Page[ItemOut])
async def list_items(
    user: CurrentUser,
    db: DB,
    q: str | None = Query(None, max_length=120),
    brand: str | None = Query(None, max_length=120),
    status: str | None = StatusParam,
    mode: str | None = Query(None, max_length=24),
    capture_level: str | None = Query(None, pattern="^(link|card|full)$"),
    data_quality: str | None = Query(None, pattern="^(ok|limited|insufficient)$"),
    tracked: bool | None = None,
    analyzed: bool | None = None,
    date_field: Literal["first_seen", "analyzed", "last_checked", "published"] = "first_seen",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_score: int | None = Query(None, ge=0, le=100),
    max_score: int | None = Query(None, ge=0, le=100),
    sort: Literal["recent", "score", "price_asc", "price_desc", "profit", "last_checked"] = "recent",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> Page[ItemOut]:
    """Every listing seen or analysed, with its source, latest analysis and tracking state."""
    f = filters(
        q, brand, status, mode, capture_level, data_quality, tracked, analyzed,
        date_field, date_from, date_to, min_score, max_score, sort,
    )  # fmt: skip
    rows, total = await ItemQueries(db).page(f, page, page_size)
    return Page[ItemOut](
        items=[ItemOut(**r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


@router.get("/export.csv")
async def export_items(
    user: CurrentUser,
    db: DB,
    q: str | None = Query(None, max_length=120),
    brand: str | None = Query(None, max_length=120),
    status: str | None = StatusParam,
    mode: str | None = Query(None, max_length=24),
    capture_level: str | None = Query(None, pattern="^(link|card|full)$"),
    data_quality: str | None = Query(None, pattern="^(ok|limited|insufficient)$"),
    tracked: bool | None = None,
    analyzed: bool | None = None,
    date_field: Literal["first_seen", "analyzed", "last_checked", "published"] = "first_seen",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_score: int | None = Query(None, ge=0, le=100),
    max_score: int | None = Query(None, ge=0, le=100),
    sort: Literal["recent", "score", "price_asc", "price_desc", "profit", "last_checked"] = "recent",
    delimiter: Literal["comma", "semicolon"] = "comma",
) -> StreamingResponse:
    """CSV of the same selection as the list (up to 50,000 rows). ``semicolon`` suits Excel set to
    Italian (or other comma-decimal) locales."""
    f = filters(
        q, brand, status, mode, capture_level, data_quality, tracked, analyzed,
        date_field, date_from, date_to, min_score, max_score, sort,
    )  # fmt: skip
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        export_csv(db, f, ";" if delimiter == "semicolon" else ","),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="flipfinder-articoli-{stamp}.csv"'},
    )


async def _listing(db: DB, ref: str) -> Listing:
    listing_id = await ItemQueries(db).resolve(ref)
    listing = await db.get(Listing, listing_id) if listing_id else None
    if listing is None:
        raise NotFoundError("Articolo non trovato.")
    return listing


@router.get("/{ref}", response_model=ItemDetailOut)
async def item_detail(ref: str, user: CurrentUser, db: DB) -> ItemDetailOut:
    """Tracking page data. ``ref`` is the internal id or the Vinted ID."""
    listing = await _listing(db, ref)
    row = (await db.execute(build_query(ItemFilters()).where(Listing.id == listing.id))).one()
    item = ItemOut(**row_dict(row))
    snapshots = (
        (
            await db.execute(
                select(ListingSnapshot)
                .where(ListingSnapshot.listing_id == listing.id)
                .order_by(ListingSnapshot.observed_at)
            )
        )
        .scalars()
        .all()
    )
    attempts = (
        (
            await db.execute(
                select(AcquisitionAttempt)
                .where(AcquisitionAttempt.listing_id == listing.id)
                .order_by(AcquisitionAttempt.started_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    opp = (
        await db.execute(select(Opportunity).where(Opportunity.listing_id == listing.id))
    ).scalar_one_or_none()
    seller = listing.seller
    from app.tracking.refresh import refresh_modes  # local: avoids an import cycle at startup

    return ItemDetailOut(
        item=item,
        description=listing.description,
        category=listing.category.name_it if listing.category else listing.category_raw,
        color=listing.color or listing.color_raw,
        material=listing.material or listing.material_raw,
        view_count=listing.view_count,
        shipping_fee=listing.shipping_fee,
        buyer_protection_fee=listing.buyer_protection_fee,
        published_at=listing.published_at,
        seller={
            "rating": float(seller.rating) if seller.rating is not None else None,
            "review_count": seller.review_count,
        }
        if seller
        else None,
        images=[ItemImageOut(**image_out(img)) for img in listing.images],
        tracking=TrackingOut(
            tracked=listing.tracked_at is not None,
            tracked_at=listing.tracked_at,
            last_checked_at=listing.last_checked_at,
            next_check_at=listing.next_check_at,
            check_failures=listing.check_failures,
            status=listing.status,
            status_changed_at=listing.status_changed_at,
            sold_at=listing.sold_at,
            sold_detected_at=listing.sold_detected_at,
            last_active_at=listing.last_active_at,
            last_active_price=listing.last_active_price,
            days_to_sell=float(listing.days_to_sell) if listing.days_to_sell is not None else None,
            removed_at=listing.removed_at,
            refresh_modes=refresh_modes(listing),
        ),
        snapshots=[SnapshotOut.model_validate(s) for s in snapshots],
        attempts=[AttemptOut.model_validate(a) for a in attempts],
        analysis=analysis_summary(opp, listing) if opp else None,
    )


def image_out(img: Any) -> dict[str, Any]:
    return {
        "position": img.position,
        "url": img.url,
        "local_url": f"/api/v1/media/{img.id}" if img.local_path else None,
        "archive_status": img.archive_status,
    }


refresh_limit = RateLimit("item-refresh", per_minute=30)


@router.post("/{ref}/refresh", response_model=dict[str, Any], dependencies=[Depends(refresh_limit)])
async def refresh_now(ref: str, user: CurrentUser, db: DB) -> dict[str, Any]:
    """ "Update now": re-read the listing with the best available mode (provider, opt-in server
    read), or queue it for the browser extension. The result says which mode was used."""
    from app.acquisition.service import refresh_listing

    listing = await _listing(db, ref)
    result = await refresh_listing(db, listing)
    await db.commit()
    if result.outcome == "updated":
        await schedule_archive([listing.id])
    if result.outcome in ("updated", "not_found"):
        await cache.bump(NS_FEED)
    return result.as_dict()


@router.post("/{ref}/track", response_model=dict[str, Any])
async def track(ref: str, user: CurrentUser, db: DB) -> dict[str, Any]:
    """Track a listing: periodic status checks (adaptive) and a place in the user's watching list."""
    listing = await _listing(db, ref)
    next_check = await set_tracked(db, user.id, listing, True)
    await db.commit()
    await cache.bump(NS_FEED)
    await schedule_archive([listing.id])
    return {"listing_id": str(listing.id), "tracked": True, "next_check_at": next_check}


@router.delete("/{ref}/track", response_model=dict[str, Any])
async def untrack(ref: str, user: CurrentUser, db: DB) -> dict[str, Any]:
    """Stop periodic checks. The listing, its history and analyses stay in the archive."""
    listing = await _listing(db, ref)
    await set_tracked(db, user.id, listing, False)
    await db.commit()
    await cache.bump(NS_FEED)
    return {"listing_id": str(listing.id), "tracked": False}
