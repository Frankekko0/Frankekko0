"""AI orchestration: deal analysis and photo analysis for stored listings/opportunities."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.claude_analyst import ClaudeDealAnalyst
from app.ai.deal_analyst import DealAnalysis, DealContext, RuleBasedDealAnalyst, ScenarioSummary
from app.ai.llm import get_llm
from app.authenticity.assess import brand_rules
from app.core.logging import get_logger
from app.db.models import Listing, Opportunity
from app.identification.engine import ListingText
from app.ingestion.catalog import load_catalog
from app.ingestion.service import get_engine
from app.vision.analyzer import get_image_analyzer
from app.vision.provenance import photo_provenance

log = get_logger(__name__)


def context_from_opportunity(opp: Opportunity, listing: Listing, brand_name: str | None) -> DealContext:
    scenarios = []
    for name, price, profit, roi in (
        ("conservative", opp.quick_sale_price, opp.conservative_profit, opp.conservative_roi),
        ("expected", opp.expected_sale_price, opp.expected_profit, opp.expected_roi),
        ("optimistic", opp.optimistic_sale_price, opp.optimistic_profit, opp.optimistic_roi),
    ):
        if price is not None and profit is not None and roi is not None:
            scenarios.append(ScenarioSummary(name=name, sale_price=price, net_profit=profit, roi=roi))
    ident = listing.identification or {}
    seller = listing.seller
    seller_summary = None
    if seller is not None:
        seller_summary = (
            f"{float(seller.rating):.1f}★ su {seller.review_count} recensioni"
            if seller.rating is not None and seller.review_count
            else "venditore senza recensioni"
        )
    return DealContext(
        title=listing.title,
        brand=brand_name,
        category=listing.category.slug if listing.category else None,
        model=listing.model_name,
        condition=listing.condition,
        size=listing.size_normalized,
        listing_price=opp.listing_price,
        total_acquisition_cost=opp.total_acquisition_cost,
        fair_market_value=opp.fair_market_value,
        discount_vs_market=float(opp.discount_vs_market) if opp.discount_vs_market is not None else None,
        scenarios=scenarios,
        max_buy_price=opp.max_buy_price,
        good_buy_price=opp.good_buy_price,
        suggested_offer=opp.suggested_offer,
        demand_level=opp.demand_level or "medium",
        sell_through_rate=float(opp.sell_through_rate or Decimal("0")),
        estimated_days_to_sell=float(opp.estimated_days_to_sell or Decimal("14")),
        flip_score=opp.flip_score,
        confidence_score=opp.confidence_score,
        risk_score=opp.risk_score,
        risk_factors=[f.get("label", "") for f in opp.risk_factors or []],
        seller_summary=seller_summary,
        identification_confidence=opp.identification_confidence or 0,
        comparables_used=opp.comparables_count,
        sold_comparables=opp.sold_comparables_count,
        market_notes=list((opp.market_snapshot or {}).get("notes") or []),
        suspicious_terms=list(ident.get("suspicious_terms") or []),
        defect_terms=list(ident.get("defect_terms") or []),
        is_vintage=listing.is_vintage,
    )


async def run_ai_analysis(db: AsyncSession, opp: Opportunity) -> dict[str, Any]:
    listing = (await db.execute(select(Listing).where(Listing.id == opp.listing_id))).scalar_one()
    ctx = context_from_opportunity(opp, listing, listing.brand.name if listing.brand else None)
    llm = get_llm()
    analyst = ClaudeDealAnalyst(llm) if llm.enabled else RuleBasedDealAnalyst()
    analysis: DealAnalysis = await analyst.analyze(ctx)
    opp.ai_analysis = analysis.model_dump(mode="json")
    opp.ai_provider = analysis.provider
    opp.ai_analyzed_at = datetime.now(UTC)
    opp.verdict = analysis.verdict.value
    return opp.ai_analysis


async def run_vision(db: AsyncSession, listing_id: Any) -> bool:
    """Analyze listing photos and refresh identification. Returns True if anything changed."""
    listing = (
        await db.execute(
            select(Listing).options(selectinload(Listing.images)).where(Listing.id == listing_id)
        )
    ).scalar_one_or_none()
    if listing is None or not listing.images:
        return False
    llm = get_llm()
    analyzer = get_image_analyzer(llm)
    catalog = await load_catalog(db)
    images = sorted(listing.images, key=lambda i: i.position)
    brand_slug = catalog.brand_slug(listing.brand_id)
    vision = await analyzer.analyze(
        [i.url for i in images],
        [i.phash for i in images],
        {
            "title": listing.title,
            "brand": listing.brand_raw,
            "category_slugs": sorted(catalog.categories_by_slug),
            "brand_rules": brand_rules(brand_slug) if brand_slug else None,
        },
    )
    # Keep each photo's hash (later listings are compared with it) and look for the same photos
    # in other sellers' listings.
    for img, h in zip(images, vision.photo_hashes, strict=False):
        if h and img.phash != h:
            img.phash = h
    prov = await photo_provenance(
        db, listing.id, listing.seller_id, listing.duplicate_of_id or listing.id, vision.photo_hashes
    )
    vision.provenance.reused_photos = prov.reused_photos
    vision.provenance.catalog_photos = prov.catalog_photos
    vision.provenance.stock_or_catalog = max(vision.provenance.stock_or_catalog, len(prov.catalog_photos))
    ident_before = dict(listing.identification or {})
    engine = get_engine(catalog.taxonomy)
    result = engine.identify(
        ListingText(
            title=listing.title,
            description=listing.description,
            brand_field=listing.brand_raw,
            category_field=listing.category_raw,
            subcategory_field=listing.subcategory_raw,
            size_field=listing.size_raw,
            condition=listing.condition_raw,
            color_field=listing.color_raw,
            material_field=listing.material_raw,
        ),
        vision,
    )
    new_ident = result.as_dict()
    new_ident["vision"] = vision.model_dump(mode="json")
    if ident_before.get("photos_reused_by_other_seller") or prov.reused_photos:
        new_ident["photos_reused_by_other_seller"] = True
    listing.identification = new_ident
    listing.identification_confidence = result.confidence
    if result.brand.value and listing.brand_id is None:
        listing.brand_id = catalog.brand_id(result.brand.value)
    if result.model.value and not listing.model_name:
        listing.model_name = result.model.value[:120]
    if result.category.value and listing.category_id is None:
        listing.category_id = catalog.category_id(result.category.value)
    old_vision = ident_before.get("vision") or {}
    changed = (
        vision.analyzer != "heuristic"
        or ident_before.get("confidence") != result.confidence
        or old_vision.get("photo_checks") != new_ident["vision"].get("photo_checks")
        or old_vision.get("provenance") != new_ident["vision"].get("provenance")
    )
    log.info("vision.analyzed", listing_id=str(listing.id), analyzer=vision.analyzer, changed=changed)
    return changed
