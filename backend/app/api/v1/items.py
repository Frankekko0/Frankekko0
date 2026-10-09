"""Archive and tracking of every listing: search, filters, CSV export, per-item history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core.cache import NS_FEED, cache
from app.core.errors import NotFoundError
from app.core.rate_limit import RateLimit
from app.db.models import AcquisitionAttempt, Analysis, Listing, ListingSnapshot, Opportunity
from app.schemas.common import Page
from app.schemas.items import (
    AnalysisRecordOut,
    AnalysisSummaryOut,
    AttemptOut,
    ItemDetailOut,
    ItemImageOut,
    ItemOut,
    SnapshotOut,
    TrackingOut,
)
from app.tracking.actions import set_tracked
from app.tracking.exports import export_analyses, export_observations
from app.tracking.queries import ItemFilters, ItemQueries, build_query, export_csv, row_dict
from app.tracking.summary import analysis_summary

router = APIRouter(prefix="/items", tags=["items"])

StatusParam = Query(None, pattern="^(active|reserved|sold|removed|unknown|to_verify)$")


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


def _csv_response(chunks: Any, name: str) -> StreamingResponse:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        chunks,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="flipfinder-{name}-{stamp}.csv"'},
    )


@router.get("/export-observations.csv")
async def export_item_observations(
    user: CurrentUser,
    db: DB,
    ref: str | None = Query(None, max_length=80, description="Internal id or Vinted ID of one item"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    delimiter: Literal["comma", "semicolon"] = "comma",
) -> StreamingResponse:
    """The append-only history of what was seen (price, status, favourites, photos), one row per
    recorded observation, each traceable to its listing by internal id, Vinted ID and URL."""
    listing_id = (await _listing(db, ref)).id if ref else None
    return _csv_response(
        export_observations(
            db,
            listing_id=listing_id,
            since=date_from,
            until=date_to,
            delimiter=";" if delimiter == "semicolon" else ",",
        ),
        "osservazioni",
    )


@router.get("/export-analyses.csv")
async def export_item_analyses(
    user: CurrentUser,
    db: DB,
    ref: str | None = Query(None, max_length=80, description="Internal id or Vinted ID of one item"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    delimiter: Literal["comma", "semicolon"] = "comma",
) -> StreamingResponse:
    """Every stored analysis with its headline numbers, the versions that produced it and why it ran."""
    listing_id = (await _listing(db, ref)).id if ref else None
    return _csv_response(
        export_analyses(
            db,
            listing_id=listing_id,
            since=date_from,
            until=date_to,
            delimiter=";" if delimiter == "semicolon" else ",",
        ),
        "analisi",
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
            last_verified_at=listing.last_verified_at,
            status_before_verify=listing.status_before_verify,
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
        analysis=(
            {**analysis_summary(opp, listing), "provenance": (opp.score_breakdown or {}).get("provenance")}
            if opp
            else None
        ),
    )


def image_out(img: Any) -> dict[str, Any]:
    return {
        "position": img.position,
        "url": img.url,
        "local_url": f"/api/v1/media/{img.id}" if img.local_path else None,
        "archive_status": img.archive_status,
    }


@router.get("/{ref}/analyses", response_model=list[AnalysisSummaryOut])
async def item_analyses(ref: str, user: CurrentUser, db: DB) -> list[AnalysisSummaryOut]:
    """Every analysis that produced a different result for this listing, newest first."""
    listing = await _listing(db, ref)
    current = await db.scalar(select(Opportunity.analysis_id).where(Opportunity.listing_id == listing.id))
    rows = (
        (
            await db.execute(
                select(Analysis)
                .where(Analysis.listing_id == listing.id)
                .order_by(Analysis.created_at.desc(), Analysis.id)
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    return [
        AnalysisSummaryOut(
            id=a.id,
            created_at=a.created_at,
            trigger=a.trigger,
            source=a.source,
            schema_version=a.schema_version,
            algorithm_version=a.algorithm_version,
            price=(a.economic or {}).get("listing_price"),
            flip_score=(a.decision or {}).get("flip_score"),
            verdict=(a.decision or {}).get("verdict"),
            expected_profit=((a.economic or {}).get("scenarios") or {}).get("expected", {}).get("profit"),
            data_quality=(a.market or {}).get("data_quality"),
            is_current=a.id == current,
        )
        for a in rows
    ]


@router.get("/{ref}/analyses/{analysis_id}", response_model=AnalysisRecordOut)
async def item_analysis(ref: str, analysis_id: uuid.UUID, user: CurrentUser, db: DB) -> AnalysisRecordOut:
    """One stored analysis in full (the five blocks)."""
    listing = await _listing(db, ref)
    a = await db.get(Analysis, analysis_id)
    if a is None or a.listing_id != listing.id:
        raise NotFoundError("Analisi non trovata.")
    current = await db.scalar(select(Opportunity.analysis_id).where(Opportunity.listing_id == listing.id))
    return AnalysisRecordOut.model_validate(
        {
            **{c: getattr(a, c) for c in AnalysisRecordOut.model_fields if c != "is_current"},
            "is_current": a.id == current,
        }
    )


refresh_limit = RateLimit("item-refresh", per_minute=30)


@router.post("/{ref}/refresh", response_model=dict[str, Any], dependencies=[Depends(refresh_limit)])
async def refresh_now(ref: str, user: CurrentUser, db: DB) -> dict[str, Any]:
    """ "Update now": re-read the listing with the best available mode (provider, opt-in server
    read), or queue it for the browser extension. The result says which mode was used."""
    from app.acquisition.service import refresh_listing

    listing = await _listing(db, ref)
    result = await refresh_listing(db, listing)
    await db.commit()
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
    return {"listing_id": str(listing.id), "tracked": True, "next_check_at": next_check}


@router.delete("/{ref}/track", response_model=dict[str, Any])
async def untrack(ref: str, user: CurrentUser, db: DB) -> dict[str, Any]:
    """Stop periodic checks. The listing, its history and analyses stay in the archive."""
    listing = await _listing(db, ref)
    await set_tracked(db, user.id, listing, False)
    await db.commit()
    await cache.bump(NS_FEED)
    return {"listing_id": str(listing.id), "tracked": False}
