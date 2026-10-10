"""AI orchestration: deal analysis and photo analysis for stored listings/opportunities."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.claude_analyst import ClaudeDealAnalyst
from app.ai.deal_analyst import DealAnalysis, DealContext, RuleBasedDealAnalyst, ScenarioSummary
from app.ai.llm import get_llm
from app.ai.queue import qualifies_for_strong_ai
from app.ai.verdicts import has_valid_review, is_llm_provider, reviewed_fields
from app.authenticity.assess import brand_rules
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import Listing, Opportunity
from app.db.session import session_scope
from app.identification.engine import ListingText
from app.ingestion.catalog import load_catalog
from app.ingestion.service import get_engine
from app.media.archive import read_copy
from app.vision.analyzer import (
    PhotoInput,
    VisionDeferred,
    get_image_analyzer,
    photos_fingerprint,
    stored_measures,
)
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
        decision_verdict=opp.decision_verdict,
    )


async def deal_context(db: AsyncSession, opp: Opportunity) -> DealContext:
    """Read phase of a deal analysis: everything the analyst may use, from the stored opportunity."""
    listing = (await db.execute(select(Listing).where(Listing.id == opp.listing_id))).scalar_one()
    return context_from_opportunity(opp, listing, listing.brand.name if listing.brand else None)


def store_analysis(opp: Opportunity, analysis: DealAnalysis) -> dict[str, Any]:
    """Write phase: the analysis on the opportunity, and the analyst's caution on the decision.

    The analyst may be more cautious than the decision engine, never less: when it is, the decision is lowered with
    the reason on record, so there is still one verdict, not two (``reviewed_fields`` can only lower). A model
    review is marked as written for the opportunity's current analysis, which takes it out of the queue, and
    remembers the flip score it was written for (the baseline of "has the deal moved enough for a new review")."""
    opp.ai_analysis = analysis.model_dump(mode="json")
    opp.ai_provider = analysis.provider
    opp.ai_analyzed_at = datetime.now(UTC)
    if is_llm_provider(analysis.provider):
        opp.ai_for_analysis_id = opp.analysis_id
        opp.ai_reviewed_flip = opp.flip_score
        opp.ai_attempts = 0
        opp.ai_next_attempt_at = None
        opp.ai_last_error = None
    for column, value in reviewed_fields(opp.decision, opp.verdict, analysis.verdict).items():
        setattr(opp, column, value)
    return opp.ai_analysis


class AnalysisChanged(Exception):
    """The opportunity was re-analysed (or removed) while the model answered: the answer was written for numbers
    that are no longer the current ones, so it is discarded."""


async def _analyse(
    opportunity_id: uuid.UUID, settings: Settings, *, queued: bool
) -> tuple[str, dict[str, Any] | None]:
    """The one way a model review is made, whoever asks (the queue or the manual request), in three steps so that no
    connection or transaction is held while the model answers: read the stored numbers, call the model (strict:
    ``AiDeferred`` when there is no usable answer, the rules' text never takes its place), then write under a row
    lock only if the opportunity is still on the analysis that was read.

    Returns ``(status, analysis)``: ``done`` (the stored analysis), ``skipped`` (queued only: nothing to do),
    ``gone`` (no such opportunity) or ``stale`` (re-analysed meanwhile). Without a model the rule-based analyst
    answers (manual request only), but never over a valid model review."""
    llm = get_llm()
    if queued and not llm.enabled:
        return "skipped", None
    async with session_scope() as s:
        opp = await s.get(Opportunity, opportunity_id)
        if opp is None:
            return "gone", None
        if queued and (
            opp.analysis_id is None
            or has_valid_review(opp)
            or not qualifies_for_strong_ai(
                settings, flip_score=opp.flip_score, data_quality=opp.data_quality, is_active=opp.is_active
            )
        ):
            return "skipped", None
        if not llm.enabled and has_valid_review(opp) and opp.ai_analysis:
            return "done", opp.ai_analysis
        analysis_id = opp.analysis_id
        ctx = await deal_context(s, opp)
    if llm.enabled:
        analysis = await ClaudeDealAnalyst(llm).analyze(ctx, ref=str(opportunity_id), strict=True)
    else:
        analysis = RuleBasedDealAnalyst().analyze_sync(ctx)
    async with session_scope() as s:
        row = (
            await s.execute(
                select(Opportunity).where(Opportunity.id == opportunity_id).with_for_update(of=Opportunity)
            )
        ).scalar_one_or_none()
        if row is None or row.analysis_id != analysis_id or (queued and not row.is_active):
            return "stale", None
        return "done", store_analysis(row, analysis)


async def review_opportunity(opportunity_id: uuid.UUID, settings: Settings | None = None) -> str:
    """The queued review (see ``_analyse``). Returns ``done``, ``skipped`` (nothing to do: gone, closed, no longer at
    the threshold, already reviewed) or ``stale`` (re-analysed meanwhile: the answer was for other numbers and is
    discarded; the new analysis is in the queue)."""
    status, _ = await _analyse(opportunity_id, settings or get_settings(), queued=True)
    return "skipped" if status == "gone" else status


async def run_ai_analysis(opportunity_id: uuid.UUID, settings: Settings | None = None) -> dict[str, Any]:
    """The manual request: the same read, call and locked write as the queued review, on any opportunity and again
    over an earlier review. A call that is refused by the quota, fails or gives no usable answer raises ``AiDeferred``
    and nothing is written. ``AnalysisChanged`` when the opportunity was re-analysed meanwhile (nothing is written
    either: the caller asks again)."""
    status, analysis = await _analyse(opportunity_id, settings or get_settings(), queued=False)
    if status != "done" or analysis is None:
        raise AnalysisChanged(status)
    return analysis


async def run_vision(db: AsyncSession, listing_id: Any) -> bool:
    """Analyze listing photos and refresh identification. Returns True if anything changed.

    When a model is meant to read the photos and cannot (quota, outage, refusal, bad answer) this raises
    ``VisionDeferred`` instead of returning: the local measures are stored when the listing has no model analysis
    yet (they are not one: ``analyzer`` stays "heuristic", so the photos still count as unchecked) and are never
    written over an existing model analysis. The caller commits, then retries later."""
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
    if not any(i.local_path for i in images):
        return False  # the browser has not uploaded any photo yet: nothing to read, nothing invented
    brand_slug = catalog.brand_slug(listing.brand_id)
    photos = [
        PhotoInput(i.position, i.image_key, await read_copy(i.local_path), i.sha256, i.content_type)
        for i in images
    ]
    photos_key = photos_fingerprint(photos)
    old_vision = (listing.identification or {}).get("vision") or {}
    keep_model = old_vision.get("analyzer") not in (None, "heuristic")
    deferred: VisionDeferred | None = None
    try:
        vision = await analyzer.analyze(
            photos,
            {
                "category_slugs": sorted(catalog.categories_by_slug),
                "brand_rules": brand_rules(brand_slug) if brand_slug else None,
                # What an earlier run measured on these very photos: a run held back by a cap wakes many times and
                # must not decode and read them again each time.
                "local": stored_measures(old_vision, photos_key),
                # A stored model analysis is never overwritten by local measures: nothing to measure when held.
                "store_local": not keep_model,
            },
        )
        if vision.analyzer == "heuristic" and analyzer.name != "heuristic":
            # A model analyzer must not end here: it is a failure all the same.
            deferred = VisionDeferred("no_model_analysis", 300.0, vision)
    except VisionDeferred as exc:
        vision, deferred = exc.partial, exc
    if deferred is not None and (keep_model or not deferred.store):
        # A good model analysis stays as it is (the local measures never replace it), and measures that are
        # already stored need no second write.
        (log.warning if keep_model else log.info)(
            "vision.deferred",
            listing_id=str(listing.id),
            reason=deferred.reason,
            kept="model" if keep_model else "measures",
        )
        raise deferred
    vision.photos_key = photos_key
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
    changed = (
        vision.analyzer != "heuristic"
        or ident_before.get("confidence") != result.confidence
        or old_vision.get("photo_checks") != new_ident["vision"].get("photo_checks")
        or old_vision.get("provenance") != new_ident["vision"].get("provenance")
    )
    log.info(
        "vision.analyzed",
        listing_id=str(listing.id),
        analyzer=vision.analyzer,
        changed=changed,
        deferred=deferred.reason if deferred else None,
    )
    if deferred is not None:
        deferred.changed = changed
        raise deferred
    return changed
