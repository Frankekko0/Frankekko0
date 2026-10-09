"""Archive of every listing FlipFinder has seen or analysed: search, filters, CSV export."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brand, Listing, ListingImage, Opportunity

DateField = Literal["first_seen", "analyzed", "last_checked", "published"]
SortKey = Literal["recent", "score", "price_asc", "price_desc", "profit", "last_checked"]
EXPORT_LIMIT = 50_000


@dataclass(frozen=True)
class ItemFilters:
    q: str | None = None
    brand: str | None = None
    status: str | None = None
    mode: str | None = None
    capture_level: str | None = None
    data_quality: str | None = None
    tracked: bool | None = None
    analyzed: bool | None = None
    date_field: DateField = "first_seen"
    date_from: datetime | None = None
    date_to: datetime | None = None
    min_score: int | None = None
    max_score: int | None = None
    sort: SortKey = "recent"


def _cover() -> Any:
    return (
        select(ListingImage.url)
        .where(ListingImage.listing_id == Listing.id, ListingImage.removed_at.is_(None))
        .order_by(ListingImage.position)
        .limit(1)
        .scalar_subquery()
    )


def build_query(f: ItemFilters) -> Select[Any]:
    stmt = (
        select(
            Listing,
            Brand.name.label("brand_name"),
            _cover().label("image_url"),
            Opportunity.id.label("opportunity_id"),
            Opportunity.flip_score,
            Opportunity.confidence_score,
            Opportunity.risk_level,
            Opportunity.data_quality,
            Opportunity.expected_profit,
            Opportunity.expected_roi,
            Opportunity.fair_market_value,
            Opportunity.analyzed_at,
            Opportunity.algorithm_version,
            Opportunity.analysis_depth,
        )
        .outerjoin(Brand, Brand.id == Listing.brand_id)
        .outerjoin(Opportunity, Opportunity.listing_id == Listing.id)
    )
    if f.q:
        term = f.q.strip().replace("%", "").replace("_", r"\_")[:120]
        if term.isdigit():
            stmt = stmt.where(or_(Listing.external_id == term, Listing.title.ilike(f"%{term}%")))
        else:
            stmt = stmt.where(Listing.title.ilike(f"%{term}%"))
    if f.brand:
        stmt = stmt.where(Brand.slug == f.brand)
    if f.status:
        stmt = stmt.where(Listing.status == f.status)
    if f.mode:
        stmt = stmt.where(Listing.acquisition_mode == f.mode)
    if f.capture_level:
        stmt = stmt.where(Listing.capture_level == f.capture_level)
    if f.data_quality:
        stmt = stmt.where(Opportunity.data_quality == f.data_quality)
    if f.tracked is not None:
        stmt = stmt.where(Listing.tracked_at.is_not(None) if f.tracked else Listing.tracked_at.is_(None))
    if f.analyzed is not None:
        stmt = stmt.where(Opportunity.id.is_not(None) if f.analyzed else Opportunity.id.is_(None))
    date_col = {
        "first_seen": Listing.first_seen_at,
        "analyzed": Opportunity.analyzed_at,
        "last_checked": Listing.last_checked_at,
        "published": Listing.published_at,
    }[f.date_field]
    if f.date_from:
        stmt = stmt.where(date_col >= f.date_from)
    if f.date_to:
        stmt = stmt.where(date_col <= f.date_to)
    if f.min_score is not None:
        stmt = stmt.where(Opportunity.flip_score >= f.min_score)
    if f.max_score is not None:
        stmt = stmt.where(Opportunity.flip_score <= f.max_score)
    order = {
        "recent": [Listing.first_seen_at.desc()],
        "score": [Opportunity.flip_score.desc().nulls_last()],
        "price_asc": [Listing.price.asc()],
        "price_desc": [Listing.price.desc()],
        "profit": [Opportunity.expected_profit.desc().nulls_last()],
        "last_checked": [Listing.last_checked_at.desc().nulls_last()],
    }[f.sort]
    return stmt.order_by(*order, Listing.id)


def record_level(analysis_depth: str | None) -> str:
    """The two levels of what the system holds about a listing: "visto" (a light record: seen on a
    card, a link, or analysed only from its card) and "analizzato" (the full item page was read and
    analysed). One rule, used by the API and the exports."""
    return "analizzato" if analysis_depth == "full" else "visto"


def row_dict(r: Any) -> dict[str, Any]:
    li: Listing = r.Listing
    return {
        "id": li.id,
        "vinted_id": li.external_id if li.provider == "vinted" else None,
        "provider": li.provider,
        "url": li.url,
        "title": li.title,
        "brand": r.brand_name or li.brand_raw,
        "size": li.size_normalized or li.size_raw,
        "condition": li.condition,
        "price": li.price,
        "currency": li.currency,
        "status": li.status,
        "favourite_count": li.favourite_count,
        "image_url": r.image_url,
        "acquisition_mode": li.acquisition_mode,
        "capture_level": li.capture_level,
        "tracked": li.tracked_at is not None,
        "first_seen_at": li.first_seen_at,
        "last_checked_at": li.last_checked_at,
        "last_verified_at": li.last_verified_at,
        "next_check_at": li.next_check_at,
        "sold_at": li.sold_at,
        "days_to_sell": li.days_to_sell,
        "last_active_price": li.last_active_price,
        "opportunity_id": r.opportunity_id,
        "flip_score": r.flip_score,
        "confidence_score": r.confidence_score,
        "risk_level": r.risk_level,
        "data_quality": r.data_quality,
        "expected_profit": r.expected_profit,
        "expected_roi": r.expected_roi,
        "fair_market_value": r.fair_market_value,
        "analyzed_at": r.analyzed_at,
        "algorithm_version": r.algorithm_version,
        "analysis_depth": r.analysis_depth,
        "record_level": record_level(r.analysis_depth),
    }


class ItemQueries:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(self, f: ItemFilters, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        stmt = build_query(f)
        total = (
            await self.session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
        ).scalar_one()
        rows = (await self.session.execute(stmt.offset((page - 1) * page_size).limit(page_size))).all()
        return [row_dict(r) for r in rows], total

    async def resolve(self, ref: str) -> uuid.UUID | None:
        """Internal UUID or Vinted ID -> internal id."""
        try:
            return uuid.UUID(ref)
        except ValueError:
            pass
        if not ref.isdigit():
            return None
        return (
            await self.session.execute(
                select(Listing.id).where(and_(Listing.provider == "vinted", Listing.external_id == ref))
            )
        ).scalar_one_or_none()


# ------------------------------------------------------------------------------ CSV
CSV_COLUMNS = [
    ("id", "ID interno"),
    ("vinted_id", "ID Vinted"),
    ("url", "URL"),
    ("title", "Titolo"),
    ("brand", "Brand"),
    ("size", "Taglia"),
    ("condition", "Condizioni"),
    ("price", "Prezzo"),
    ("currency", "Valuta"),
    ("status", "Stato"),
    ("favourite_count", "Preferiti"),
    ("tracked", "Tracciato"),
    ("acquisition_mode", "Modalità di acquisizione"),
    ("capture_level", "Livello dati"),
    ("record_level", "Livello record"),
    ("first_seen_at", "Visto la prima volta"),
    ("last_checked_at", "Ultimo controllo"),
    ("sold_at", "Venduto il (stima)"),
    ("days_to_sell", "Giorni per vendere"),
    ("last_active_price", "Ultimo prezzo visto"),
    ("flip_score", "Punteggio"),
    ("confidence_score", "Confidenza"),
    ("data_quality", "Qualità dati"),
    ("fair_market_value", "Valore di mercato"),
    ("expected_profit", "Margine netto atteso"),
    ("expected_roi", "ROI atteso"),
    ("risk_level", "Rischio"),
    ("analyzed_at", "Analizzato il"),
    ("analysis_depth", "Profondità analisi"),
    ("algorithm_version", "Versione algoritmo"),
]
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: Any) -> str:
    """Plain text for spreadsheets. Text fields come from third parties (titles written by sellers):
    a leading = + - @ would be run as a formula by Excel/Sheets, so it is neutralized."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "sì" if value else "no"
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    text = str(value)
    if isinstance(value, str) and text.startswith(_FORMULA_START):
        return "'" + text
    return text


async def export_csv(session: AsyncSession, f: ItemFilters, delimiter: str = ",") -> AsyncIterator[str]:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter)
    buf.write("﻿")  # BOM: spreadsheets detect UTF-8 (accents, €)
    writer.writerow([label for _, label in CSV_COLUMNS])
    yield buf.getvalue()
    stream = await session.stream(build_query(f).limit(EXPORT_LIMIT).execution_options(yield_per=1000))
    async for r in stream:
        buf.seek(0)
        buf.truncate()
        d = row_dict(r)
        writer.writerow([csv_cell(d[key]) for key, _ in CSV_COLUMNS])
        yield buf.getvalue()
