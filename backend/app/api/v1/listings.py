"""Listings, manual import, ad-hoc analysis, catalog and natural-language search."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.ai.nl_search import NaturalLanguageParser
from app.api.deps import DB, CurrentUser, Economics
from app.core.cache import NS_FEED, cache
from app.core.errors import InsufficientDataError, NotFoundError
from app.core.serialization import jsonable
from app.db.models import Brand, Category, Listing, ListingImage, Opportunity
from app.identification.engine import ListingText
from app.ingestion.catalog import load_catalog
from app.ingestion.normalizer import normalize_condition, title_fingerprint
from app.ingestion.service import IngestionService, get_engine, listing_columns
from app.marketplace.base import ManualListingInput, ProviderImage, ProviderListing, ProviderSeller
from app.opportunities.engine import SubjectContext, run_analysis
from app.opportunities.pipeline import AnalysisPipeline, profile_from_row
from app.opportunities.queries import OpportunityQueries
from app.schemas.common import Page
from app.schemas.opportunity import OpportunityCard, OpportunityFilters
from app.scoring.seller import SellerProfile

router = APIRouter(tags=["listings"])
VINTED_ID = re.compile(r"/items/(\d+)")


class ListingOut(BaseModel):
    id: uuid.UUID
    provider: str
    external_id: str
    url: str
    title: str
    price: float
    currency: str
    brand: str | None
    category: str | None
    size: str | None
    condition: str
    country: str | None
    status: str
    published_at: datetime | None
    last_seen_at: datetime
    image_url: str | None
    opportunity_id: uuid.UUID | None
    flip_score: int | None


@router.get("/listings", response_model=Page[ListingOut])
async def list_listings(
    user: CurrentUser,
    db: DB,
    q: str | None = Query(None, max_length=120),
    brand: str | None = None,
    category: str | None = None,
    status: str | None = Query(None, pattern="^(active|reserved|sold|possibly_sold|removed|unknown)$"),
    min_price: float | None = Query(None, ge=0),
    max_price: float | None = Query(None, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> Page[ListingOut]:
    image = (
        select(ListingImage.url)
        .where(ListingImage.listing_id == Listing.id)
        .order_by(ListingImage.position)
        .limit(1)
        .scalar_subquery()
    )
    stmt = (
        select(
            Listing,
            Brand.name,
            Category.name_it,
            image.label("image_url"),
            Opportunity.id,
            Opportunity.flip_score,
        )
        .outerjoin(Brand, Brand.id == Listing.brand_id)
        .outerjoin(Category, Category.id == Listing.category_id)
        .outerjoin(Opportunity, Opportunity.listing_id == Listing.id)
    )
    if q:
        stmt = stmt.where(Listing.title.ilike(f"%{q.replace('%', '')}%"))
    if brand:
        stmt = stmt.where(Brand.slug == brand)
    if category:
        stmt = stmt.where(Category.slug == category)
    if status:
        stmt = stmt.where(Listing.status == status)
    if min_price is not None:
        stmt = stmt.where(Listing.price >= Decimal(str(min_price)))
    if max_price is not None:
        stmt = stmt.where(Listing.price <= Decimal(str(max_price)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(Listing.published_at.desc().nulls_last())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        ListingOut(
            id=li.id,
            provider=li.provider,
            external_id=li.external_id,
            url=li.url,
            title=li.title,
            price=float(li.price),
            currency=li.currency,
            brand=bn,
            category=cn,
            size=li.size_normalized,
            condition=li.condition,
            country=li.country,
            status=li.status,
            published_at=li.published_at,
            last_seen_at=li.last_seen_at,
            image_url=img,
            opportunity_id=oid,
            flip_score=fs,
        )
        for li, bn, cn, img, oid, fs in rows
    ]
    return Page[ListingOut](
        items=items, total=total, page=page, page_size=page_size, has_more=page * page_size < total
    )


@router.get("/listings/{listing_id}", response_model=dict[str, Any])
async def get_listing(listing_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    li = await db.get(Listing, listing_id)
    if li is None:
        raise NotFoundError("Annuncio non trovato.")
    opp_id = (
        await db.execute(select(Opportunity.id).where(Opportunity.listing_id == li.id))
    ).scalar_one_or_none()
    return {
        "id": str(li.id),
        "title": li.title,
        "url": li.url,
        "price": float(li.price),
        "status": li.status,
        "identification": li.identification,
        "opportunity_id": str(opp_id) if opp_id else None,
    }


def manual_to_provider(body: ManualListingInput) -> ProviderListing:
    url = str(body.url)
    m = VINTED_ID.search(url)
    external_id = m.group(1) if m else title_fingerprint(url)[:24]
    seller = None
    if body.seller_username:
        seller = ProviderSeller(
            external_id=f"manual:{body.seller_username.lower()}"[:64],
            username=body.seller_username,
            rating=body.seller_rating,
            review_count=body.seller_review_count or 0,
        )
    return ProviderListing(
        external_id=external_id,
        url=url,
        title=body.title,
        description=body.description,
        price=body.price,
        brand=body.brand,
        category=body.category,
        size=body.size,
        condition=body.condition,
        color=body.color,
        images=[ProviderImage(url=str(u)) for u in body.image_urls],
        seller=seller,
        country=body.country,
        published_at=datetime.now(UTC),
        shipping_fee=body.shipping_fee,
        buyer_protection_fee=(Decimal("0.70") + body.price * Decimal("0.05")).quantize(Decimal("0.01")),
        raw={"source": "manual_import"},
    )


@router.post("/listings/import", response_model=dict[str, Any], status_code=201)
async def import_listing(body: ManualListingInput, user: CurrentUser, db: DB) -> dict[str, Any]:
    """Manual import of a listing found by the user (URL + key data), analysed immediately."""
    pl = manual_to_provider(body)
    result = await IngestionService(db, "manual").ingest([pl])
    listing_id = (result.new_ids or result.updated_ids)[0]
    outcome = await AnalysisPipeline(db).analyze_listing(listing_id)
    await db.commit()
    await cache.bump(NS_FEED)
    if outcome is None:
        raise InsufficientDataError("Annuncio importato ma non analizzabile.")
    return {
        "listing_id": str(listing_id),
        "opportunity_id": str(outcome.opportunity_id),
        "flip_score": outcome.result.flip.score,
        "is_new": outcome.is_new,
    }


@router.post("/analyze/{listing_id}", response_model=dict[str, Any])
async def reanalyze(listing_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    """Re-run the full analysis for a stored listing now."""
    outcome = await AnalysisPipeline(db).analyze_listing(listing_id)
    if outcome is None:
        raise NotFoundError("Annuncio non trovato o non più attivo.")
    await db.commit()
    await cache.bump(NS_FEED)
    return {
        "opportunity_id": str(outcome.opportunity_id),
        "flip_score": outcome.result.flip.score,
        "confidence_score": outcome.result.confidence.score,
        "risk_score": outcome.result.risk.score,
    }


@router.post("/analyze", response_model=dict[str, Any])
async def analyze_adhoc(
    body: ManualListingInput, user: CurrentUser, econ: Economics, db: DB
) -> dict[str, Any]:
    """What-if analysis of listing data without saving anything (uses your costs and targets)."""
    pl = manual_to_provider(body)
    catalog = await load_catalog(db)
    ident = get_engine(catalog.taxonomy).identify(
        ListingText(
            title=pl.title,
            description=pl.description,
            brand_field=pl.brand,
            category_field=pl.category,
            size_field=pl.size,
            condition=pl.condition,
            color_field=pl.color,
        )
    )
    cols = listing_columns(pl, ident, catalog, "adhoc", datetime.now(UTC))
    profile = profile_from_row(SimpleNamespace(**cols, id=None), catalog)
    brand = catalog.brands_by_slug.get(ident.brand.value or "")
    category = catalog.categories_by_slug.get(ident.category.value or "")
    seller = SellerProfile(pl.seller.rating, pl.seller.review_count) if pl.seller else None
    subject = SubjectContext(
        profile=profile,
        description_length=len(pl.description),
        photo_count=len(pl.images),
        favourite_count=0,
        listing_age_hours=0.0,
        shipping_fee=pl.shipping_fee,
        brand_name=brand.name if brand else None,
        brand_counterfeit_risk=brand.counterfeit_risk if brand else 0.1,
        category_baseline_days=category.baseline_days if category else 14,
        identification=ident.as_dict(),
        identification_confidence=ident.confidence,
        seller=seller,
        seller_account_age_days=None,
    )
    pipeline = AnalysisPipeline(db)
    now = datetime.now(UTC)
    candidates = await pipeline.candidates(profile, catalog, catalog.brand_id(ident.brand.value), now)
    prior = await pipeline.segment_prior(
        catalog.brand_id(ident.brand.value), catalog.category_id(ident.category.value), ident.model.value
    )
    r = run_analysis(subject, candidates, now, econ.costs, econ.targets, prior)
    return jsonable(
        {
            "identification": ident.as_dict(),
            "condition": normalize_condition(pl.condition).value,
            "fair_market_value": r.market.fair_market_value,
            "market": r.market.snapshot(),
            "scenarios": [
                {
                    "name": s.name,
                    "sale_price": s.sale_price,
                    "net_profit": s.result.net_profit,
                    "roi": s.result.roi,
                    "total_acquisition_cost": s.result.acquisition.total,
                    "net_sale_revenue": s.result.sale.net,
                }
                for s in r.scenarios
            ],
            "max_buy_price": r.max_buy_price,
            "good_buy_price": r.good_buy_price,
            "offer": {
                "suggested_offer": r.offer.suggested_offer,
                "action": r.offer.action.value,
                "rationale": r.offer.rationale,
            },
            "flip_score": r.flip.score,
            "confidence_score": r.confidence.score,
            "risk": {"score": r.risk.score, "level": r.risk.level.value, "factors": r.risk.as_list()},
            "demand": {"level": r.demand.level.value, "sell_through_rate": r.demand.sell_through_rate},
            "velocity": {"days": r.velocity.estimated_days, "bucket": r.velocity.bucket.value},
            "explanation": r.explanation,
            "ai_analysis": r.analysis.model_dump(mode="json"),
            "comparables_used": r.market.n_used,
        }
    )


# ------------------------------------------------------------------------------ catalog
@router.get("/brands", response_model=list[dict[str, Any]], tags=["catalog"])
async def brands(db: DB, user: CurrentUser) -> Any:
    async def load() -> list[dict[str, Any]]:
        rows = (await db.execute(select(Brand).order_by(Brand.name))).scalars().all()
        return [
            {"slug": b.slug, "name": b.name, "tier": b.tier, "counterfeit_risk": float(b.counterfeit_risk)}
            for b in rows
        ]

    return await cache.get_or_set("catalog", ("brands",), 600, load)


@router.get("/categories", response_model=list[dict[str, Any]], tags=["catalog"])
async def categories(db: DB, user: CurrentUser) -> Any:
    async def load() -> list[dict[str, Any]]:
        rows = (await db.execute(select(Category).order_by(Category.id))).scalars().all()
        by_id = {c.id: c for c in rows}
        return [
            {
                "slug": c.slug,
                "name": c.name,
                "name_it": c.name_it,
                "parent": by_id[c.parent_id].slug if c.parent_id in by_id else None,
            }
            for c in rows
        ]

    return await cache.get_or_set("catalog", ("categories",), 600, load)


# ------------------------------------------------------------------------------- search
class SearchResponse(BaseModel):
    query: str
    parsed: dict[str, Any]
    results: Page[OpportunityCard]


_parser = NaturalLanguageParser()


@router.get("/search/parse", response_model=dict[str, Any], tags=["search"])
async def parse_search(
    user: CurrentUser, q: str = Query(..., min_length=1, max_length=200)
) -> dict[str, Any]:
    """Translate a natural-language query into structured filters (no search performed)."""
    return _parser.parse(q).as_dict()


@router.get("/search", response_model=SearchResponse, tags=["search"])
async def search(
    user: CurrentUser,
    econ: Economics,
    db: DB,
    q: str = Query(..., min_length=1, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
) -> SearchResponse:
    """Natural-language search, e.g. "felpe Ralph Lauren sotto 25 euro con almeno 50% ROI"."""
    parsed = _parser.parse(q)
    filters = OpportunityFilters.model_validate({**parsed.filters, "page": page, "page_size": page_size})
    cards, total = await OpportunityQueries(db, user.id, econ).feed(filters)
    if total and page == 1:
        await cache.record_search(q)
    return SearchResponse(
        query=q,
        parsed=parsed.as_dict(),
        results=Page[OpportunityCard](
            items=cards, total=total, page=page, page_size=page_size, has_more=page * page_size < total
        ),
    )


@router.get("/search/popular", response_model=list[dict[str, Any]], tags=["search"])
async def popular(user: CurrentUser) -> list[dict[str, Any]]:
    return await cache.popular_searches(10)
