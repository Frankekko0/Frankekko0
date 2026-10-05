"""Analysis pipeline: load a listing, gather market data, run the engine, persist the opportunity."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import Listing, MarketComparable, MarketStatistic, Opportunity, OpportunityScore, Seller
from app.domain.enums import ListingStatus
from app.ingestion.catalog import Catalog, load_catalog
from app.opportunities.engine import (
    AnalysisResult,
    EconomicTargets,
    SubjectContext,
    recommended_action,
    run_analysis,
)
from app.pricing.comparables import ItemProfile
from app.pricing.market_value import SegmentPrior
from app.profit.calculator import CostProfile
from app.scoring.seller import SellerProfile

log = get_logger(__name__)
CANDIDATE_LIMIT = 400


def default_cost_profile(settings: Settings | None = None) -> CostProfile:
    s = settings or get_settings()
    return CostProfile(
        buyer_protection_fixed=s.default_buyer_protection_fixed,
        buyer_protection_pct=s.default_buyer_protection_pct,
        shipping_in=s.default_shipping_in,
    )


def default_targets(settings: Settings | None = None) -> EconomicTargets:
    s = settings or get_settings()
    return EconomicTargets(min_profit=s.default_min_profit, min_roi=s.default_min_roi)


def segment_key(brand_id: int | None, category_id: int | None, model: str | None, size: str | None) -> str:
    return f"{brand_id or '*'}:{category_id or '*'}:{(model or '*').lower()}:{size or '*'}"


@dataclass
class AnalysisOutcome:
    opportunity_id: uuid.UUID
    listing_id: uuid.UUID
    is_new: bool
    previous_flip_score: int | None
    previous_price: Decimal | None
    result: AnalysisResult


def profile_from_row(row: Any, catalog: Catalog) -> ItemProfile:
    category_slug = catalog.category_slug(row.category_id)
    return ItemProfile(
        id=row.id,
        title=row.title,
        price=row.price,
        brand=catalog.brand_slug(row.brand_id),
        category=category_slug,
        parent_category=catalog.parent_slug(category_slug),
        model=row.model_name,
        condition=row.condition,
        size=row.size_normalized,
        gender=row.gender,
        color=row.color,
        material=row.material,
        country=row.country,
        is_vintage=row.is_vintage,
        status=row.status,
        published_at=row.published_at,
        sold_at=row.sold_at,
        last_seen_at=row.last_seen_at,
        url=row.url,
        favourite_count=row.favourite_count,
    )


def seller_profile(seller: Seller | None, anomalies: tuple[str, ...] = ()) -> SellerProfile | None:
    if seller is None:
        return None
    return SellerProfile(
        rating=seller.rating,
        review_count=seller.review_count,
        account_created_at=seller.account_created_at,
        item_count=seller.item_count,
        sold_count=seller.sold_count,
        anomalies=anomalies,
    )


class AnalysisPipeline:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # ---------------------------------------------------------------- data access
    async def candidates(
        self, subject: ItemProfile, catalog: Catalog, brand_id: int | None, now: datetime
    ) -> list[ItemProfile]:
        """Comparable candidates: recent SOLD items and recent ACTIVE items, fetched separately.

        Two bounded queries guarantee that realized prices are always represented, however many
        active listings the segment has (ordering a single query by "last seen" would let fresh
        active listings crowd out the sold ones).
        """
        since = now - timedelta(days=self.settings.comparables_window_days)
        cols = (
            Listing.id,
            Listing.title,
            Listing.price,
            Listing.brand_id,
            Listing.category_id,
            Listing.model_name,
            Listing.condition,
            Listing.size_normalized,
            Listing.gender,
            Listing.color,
            Listing.material,
            Listing.country,
            Listing.is_vintage,
            Listing.status,
            Listing.published_at,
            Listing.sold_at,
            Listing.last_seen_at,
            Listing.url,
            Listing.favourite_count,
        )
        category_ids = catalog.sibling_category_ids(subject.category)

        def base():  # type: ignore[no-untyped-def]
            stmt = select(*cols).where(Listing.duplicate_of_id.is_(None))
            if subject.id is not None:
                stmt = stmt.where(Listing.id != subject.id)
            if category_ids:
                stmt = stmt.where(Listing.category_id.in_(category_ids))
            if brand_id is not None:
                return stmt.where(Listing.brand_id == brand_id)
            # Unknown brand: only compare with other unidentified items with a similar title.
            return stmt.where(
                Listing.brand_id.is_(None), func.similarity(Listing.title, subject.title) > 0.25
            )

        sold_states = [ListingStatus.SOLD.value, ListingStatus.POSSIBLY_SOLD.value]
        event_time = func.coalesce(Listing.sold_at, Listing.status_changed_at, Listing.last_seen_at)
        sold_stmt = (
            base()
            .where(Listing.status.in_(sold_states), event_time >= since)
            .order_by(event_time.desc())
            .limit(CANDIDATE_LIMIT // 2)
        )
        active_stmt = (
            base()
            .where(Listing.status == ListingStatus.ACTIVE.value, Listing.published_at >= since)
            .order_by(Listing.published_at.desc())
            .limit(CANDIDATE_LIMIT // 2)
        )
        rows = [
            *(await self.session.execute(sold_stmt)).all(),
            *(await self.session.execute(active_stmt)).all(),
        ]
        return [profile_from_row(r, catalog) for r in rows]

    async def segment_prior(
        self, brand_id: int | None, category_id: int | None, model: str | None
    ) -> SegmentPrior | None:
        if brand_id is None or category_id is None:
            return None
        keys = [segment_key(brand_id, category_id, model, None)] if model else []
        keys.append(segment_key(brand_id, category_id, None, None))
        rows = (
            (await self.session.execute(select(MarketStatistic).where(MarketStatistic.segment_key.in_(keys))))
            .scalars()
            .all()
        )
        by_key = {r.segment_key: r for r in rows}
        for key in keys:
            if (st := by_key.get(key)) is not None:
                return SegmentPrior(
                    median_price=float(st.median_price),
                    p25_price=float(st.p25_price),
                    p75_price=float(st.p75_price),
                    sample_size=st.sample_size,
                    ask_to_sale_ratio=float(st.ask_to_sale_ratio) if st.ask_to_sale_ratio else None,
                    sell_through_rate=float(st.sell_through_rate),
                    avg_days_to_sale=float(st.avg_days_to_sale) if st.avg_days_to_sale else None,
                )
        return None

    async def seller_anomalies(
        self, seller_id: uuid.UUID | None, catalog: Catalog, now: datetime
    ) -> tuple[str, ...]:
        if seller_id is None:
            return ()
        rows = (
            await self.session.execute(
                select(Listing.title_fingerprint, Listing.brand_id).where(
                    Listing.seller_id == seller_id,
                    Listing.status == ListingStatus.ACTIVE,
                    Listing.first_seen_at >= now - timedelta(days=30),
                )
            )
        ).all()
        anomalies: list[str] = []
        fps: dict[str | None, int] = {}
        for r in rows:
            fps[r.title_fingerprint] = fps.get(r.title_fingerprint, 0) + 1
        if any(n >= 3 for fp, n in fps.items() if fp):
            anomalies.append("Molti annunci identici dello stesso venditore")
        risky = sum(
            1
            for r in rows
            if r.brand_id in catalog.brands_by_id and catalog.brands_by_id[r.brand_id].counterfeit_risk >= 0.3
        )
        if risky >= 4:
            anomalies.append("Molti annunci di brand spesso contraffatti in poco tempo")
        return tuple(anomalies)

    def subject_context(
        self, listing: Listing, catalog: Catalog, now: datetime, anomalies: tuple[str, ...]
    ) -> SubjectContext:
        profile = profile_from_row(listing, catalog)
        brand = catalog.brands_by_id.get(listing.brand_id) if listing.brand_id else None
        category = catalog.categories_by_id.get(listing.category_id) if listing.category_id else None
        published = listing.published_at or listing.first_seen_at
        seller = listing.seller
        ident = dict(listing.identification or {})
        return SubjectContext(
            profile=profile,
            description_length=len(listing.description or ""),
            photo_count=listing.photo_count,
            favourite_count=listing.favourite_count,
            listing_age_hours=max(0.0, (now - published).total_seconds() / 3600) if published else None,
            shipping_fee=listing.shipping_fee,
            brand_name=brand.name if brand else None,
            brand_counterfeit_risk=brand.counterfeit_risk if brand else 0.1,
            category_baseline_days=category.baseline_days if category else 14,
            identification=ident,
            identification_confidence=listing.identification_confidence or 0,
            seller=seller_profile(seller, anomalies),
            seller_account_age_days=(now - seller.account_created_at).days
            if seller and seller.account_created_at
            else None,
            is_repost=listing.duplicate_of_id is not None,
            vision=ident.get("vision"),
        )

    # ---------------------------------------------------------------- analysis
    async def analyze_listing(
        self, listing_id: uuid.UUID, now: datetime | None = None
    ) -> AnalysisOutcome | None:
        now = now or datetime.now(UTC)
        listing = (
            await self.session.execute(
                select(Listing).options(selectinload(Listing.images)).where(Listing.id == listing_id)
            )
        ).scalar_one_or_none()
        if listing is None:
            return None
        if listing.status != ListingStatus.ACTIVE:
            await self.deactivate([listing.id])
            return None
        catalog = await load_catalog(self.session)
        anomalies = await self.seller_anomalies(listing.seller_id, catalog, now)
        subject = self.subject_context(listing, catalog, now, anomalies)
        candidates = await self.candidates(subject.profile, catalog, listing.brand_id, now)
        prior = await self.segment_prior(listing.brand_id, listing.category_id, listing.model_name)
        result = run_analysis(
            subject,
            candidates,
            now,
            default_cost_profile(self.settings),
            default_targets(self.settings),
            prior,
        )
        return await self.persist(listing, result, now)

    async def persist(self, listing: Listing, result: AnalysisResult, now: datetime) -> AnalysisOutcome:
        previous = (
            await self.session.execute(
                select(Opportunity.id, Opportunity.flip_score, Opportunity.listing_price).where(
                    Opportunity.listing_id == listing.id
                )
            )
        ).one_or_none()
        values = opportunity_values(listing, result, self.settings.algorithm_version, now)
        stmt = pg_insert(Opportunity).values(id=uuid.uuid4(), created_at=now, **values)
        stmt = stmt.on_conflict_do_update(index_elements=["listing_id"], set_=values).returning(
            Opportunity.id
        )
        opportunity_id = (await self.session.execute(stmt)).scalar_one()

        await self.session.execute(
            pg_insert(OpportunityScore).values(
                opportunity_id=opportunity_id,
                algorithm_version=self.settings.algorithm_version,
                listing_price=listing.price,
                flip_score=result.flip.score,
                confidence_score=result.confidence.score,
                risk_score=result.risk.score,
                components=result.flip.components,
                penalties=result.flip.penalties,
                expected_roi=result.expected_roi,
                computed_at=now,
            )
        )
        await self.session.execute(delete(MarketComparable).where(MarketComparable.listing_id == listing.id))
        comp_rows = [
            {
                "listing_id": listing.id,
                "comparable_listing_id": c.item.id,
                "similarity": Decimal(str(round(c.similarity, 4))),
                "price": c.item.price,
                "adjusted_price": Decimal(str(round(c.adjusted_price, 2))),
                "weight": Decimal(str(round(min(c.weight, 999999), 4))),
                "is_sold": c.is_sold,
                "included": c.included,
                "exclusion_reason": c.exclusion_reason,
                "computed_at": now,
            }
            for c in result.comparables
            if c.item.id is not None
        ]
        if comp_rows:
            await self.session.execute(pg_insert(MarketComparable).on_conflict_do_nothing(), comp_rows)
        return AnalysisOutcome(
            opportunity_id=opportunity_id,
            listing_id=listing.id,
            is_new=previous is None,
            previous_flip_score=previous.flip_score if previous else None,
            previous_price=previous.listing_price if previous else None,
            result=result,
        )

    async def deactivate(self, listing_ids: list[uuid.UUID]) -> None:
        if listing_ids:
            await self.session.execute(
                update(Opportunity).where(Opportunity.listing_id.in_(listing_ids)).values(is_active=False)
            )


def opportunity_values(listing: Listing, r: AnalysisResult, version: str, now: datetime) -> dict[str, Any]:
    m = r.market
    st = m.stats
    cons = r.scenario("conservative")
    exp = r.scenario("expected")
    opt = r.scenario("optimistic")

    def dec(v: float | None) -> Decimal | None:
        return Decimal(str(round(v, 2))) if v is not None else None

    return {
        "listing_id": listing.id,
        "product_id": listing.product_id,
        "algorithm_version": version,
        "is_active": listing.status == ListingStatus.ACTIVE,
        "listing_price": listing.price,
        "currency": listing.currency,
        "fair_market_value": m.fair_market_value,
        "market_median": dec(st.median) if st else None,
        "market_mean": dec(st.mean) if st else None,
        "market_p25": dec(st.p25) if st else None,
        "market_p75": dec(st.p75) if st else None,
        "market_min": dec(st.min_reasonable) if st else None,
        "market_max": dec(st.max_reasonable) if st else None,
        "quick_sale_price": m.quick_sale_price,
        "expected_sale_price": m.expected_sale_price,
        "optimistic_sale_price": m.optimistic_sale_price,
        "discount_vs_market": r.discount_vs_market,
        "total_acquisition_cost": r.total_acquisition_cost,
        "expected_net_revenue": exp.result.sale.net if exp else None,
        "expected_profit": exp.result.net_profit if exp else None,
        "expected_roi": exp.result.roi if exp else None,
        "conservative_profit": cons.result.net_profit if cons else None,
        "conservative_roi": cons.result.roi if cons else None,
        "optimistic_profit": opt.result.net_profit if opt else None,
        "optimistic_roi": opt.result.roi if opt else None,
        "max_buy_price": r.max_buy_price,
        "good_buy_price": r.good_buy_price,
        "suggested_offer": r.offer.suggested_offer,
        "demand_level": r.demand.level.value,
        "demand_score": r.demand.score,
        "sell_through_rate": Decimal(str(r.demand.sell_through_rate)),
        "velocity_score": r.velocity.score,
        "estimated_days_to_sell": Decimal(str(r.velocity.estimated_days)),
        "velocity_bucket": r.velocity.bucket.value,
        "flip_score": r.flip.score,
        "confidence_score": r.confidence.score,
        "risk_score": r.risk.score,
        "risk_level": r.risk.level.value,
        "seller_score": r.seller.score,
        "deal_tier": r.tier.value,
        "is_ultra_deal": r.ultra,
        "verdict": r.analysis.verdict.value,
        "recommended_action": recommended_action(r).value,
        "comparables_count": m.n_used,
        "sold_comparables_count": m.n_sold,
        "identification_confidence": r.subject.identification_confidence,
        "score_breakdown": {
            "components": r.flip.components,
            "penalties": r.flip.penalties,
            "cap": r.flip.cap,
            "base": r.flip.base,
            "confidence_components": r.confidence.components,
            "seller": r.seller.as_dict(),
            "demand": {
                "observations": r.demand.observations,
                "raw_sell_through_rate": r.demand.raw_sell_through_rate,
                "favourites_signal": r.demand.favourites_signal,
                "pool_size": r.pool_size,
            },
            "velocity": {
                "sample_size": r.velocity.sample_size,
                "quick_sale_days": r.velocity.quick_sale_days,
                "optimistic_sale_days": r.velocity.optimistic_sale_days,
            },
            "offer": {"action": r.offer.action.value, "rationale": r.offer.rationale},
        },
        "explanation": r.explanation,
        "risk_factors": r.risk.as_list(),
        "market_snapshot": m.snapshot(),
        "ai_analysis": r.analysis.model_dump(mode="json"),
        "ai_provider": r.analysis.provider,
        "ai_analyzed_at": now,
        "analyzed_at": now,
        "updated_at": now,
    }
