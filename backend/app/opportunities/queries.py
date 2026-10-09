"""Read side of opportunities: filtered/sorted feed, quick stats and the full deal detail.

Profit and ROI depend on each user's cost profile. Because every cost is linear in the
purchase and sale price, they are recomputed *in SQL* from the stored prices, so filtering and
sorting by "my profit" / "my ROI" is exact and index-assisted without re-analyzing anything.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, defer, noload, selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.api.deps import UserEconomics
from app.core.errors import NotFoundError
from app.db.models import (
    Brand,
    Category,
    Favorite,
    Listing,
    ListingImage,
    ListingPriceHistory,
    MarketComparable,
    Opportunity,
    SystemState,
    UserAffinity,
)
from app.domain.enums import FavoriteState
from app.opportunities import insights as ins
from app.opportunities.engine import EconomicTargets
from app.profit.calculator import AcquisitionCost, CostProfile, SaleRevenue, max_buy_price, profit_for
from app.profit.offers import build_offer_plan
from app.schemas.common import BrandRef, CategoryRef
from app.schemas.opportunity import (
    ComparableOut,
    CostLine,
    ImageOut,
    ListingDetailOut,
    OpportunityCard,
    OpportunityDetail,
    OpportunityFilters,
    PricePoint,
    Reason,
    ScenarioOut,
    SellerOut,
    SmartBuyOut,
)

PRICE_BANDS: tuple[tuple[float, float | None, str], ...] = (
    (0, 20, "<20"),
    (20, 40, "20-40"),
    (40, 80, "40-80"),
    (80, 150, "80-150"),
    (150, None, "150+"),
)

PRESETS: dict[str, dict[str, Any]] = {
    "best_deals": {"min_flip": 70, "sort": "flip"},
    "high_profit": {"min_profit": 20, "sort": "profit"},
    "high_roi": {"min_roi": 0.8, "min_profit": 5, "sort": "roi"},
    "fast_flip": {"min_velocity": 70, "min_profit": 5, "sort": "velocity"},
    "low_risk": {"max_risk": 25, "min_confidence": 60, "min_flip": 55, "sort": "flip"},
    "just_listed": {"published_within_hours": 6, "sort": "newest"},
    "hidden_gems": {"min_flip": 60, "sort": "flip", "_hidden_gems": True},
    "under_20": {"max_price": 20, "min_profit": 5, "sort": "flip"},
    "ultra": {"ultra_only": True, "sort": "flip"},
}


def risk_adjusted_sql(profit: ColumnElement[Any]) -> ColumnElement[Any]:
    """The user's expected profit x P(sale) x P(authentic); a loss stays a loss."""
    return case(
        (profit > 0, profit * Opportunity.sale_probability * Opportunity.authenticity_probability),
        else_=profit,
    )


def risk_adjusted(profit: Decimal | None, p_sale: Decimal | None, p_auth: Decimal | None) -> Decimal | None:
    if profit is None:
        return None
    if profit <= 0:
        return profit
    if p_sale is None or p_auth is None:
        return None
    return (profit * p_sale * p_auth).quantize(Decimal("0.01"))


def user_insights(stored: dict[str, Any] | None, card: OpportunityCard, smart: Any) -> dict[str, Any] | None:
    """Stored insights with the money figures recomputed on the user's own costs."""
    if not stored:
        return None
    d = dict(stored)
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    d["net_margin"] = f(card.expected_profit)
    d["roi"] = f(card.expected_roi)
    d["risk_adjusted_profit"] = f(card.risk_adjusted_profit)
    d["max_price"] = f(getattr(smart, "max_buy_price", None))
    d["suggested_offer"] = f(getattr(smart, "suggested_offer", None))
    d["reason"] = ins.reason_lines(d)
    return d


def price_band(price: float) -> str:
    for lo, hi, label in PRICE_BANDS:
        if price >= lo and (hi is None or price < hi):
            return label
    return PRICE_BANDS[-1][2]


def price_band_sql(price: ColumnElement[Any]) -> ColumnElement[Any]:
    whens = [(price < hi, label) for _lo, hi, label in PRICE_BANDS if hi is not None]
    return case(*whens, else_=PRICE_BANDS[-1][2])


def _d(v: Decimal | float | int) -> ColumnElement[Any]:
    return literal(Decimal(str(v)))


def user_money_sql(costs: CostProfile) -> tuple[ColumnElement[Any], ColumnElement[Any], ColumnElement[Any]]:
    """(total acquisition cost, expected profit, expected ROI) as SQL expressions."""
    price = Opportunity.listing_price
    sale = Opportunity.expected_sale_price
    shipping = (
        func.coalesce(Listing.shipping_fee, _d(costs.shipping_in))
        if costs.use_listing_shipping
        else _d(costs.shipping_in)
    )
    tac = (
        price * _d(1 + costs.buyer_protection_pct)
        + _d(costs.buyer_protection_fixed + costs.other_acquisition)
        + shipping
    )
    variable = 1 - costs.selling_fee_pct - costs.payment_fee_pct
    fixed_sale = (
        costs.selling_fee_fixed
        + costs.payment_fee_fixed
        + costs.advertising
        + costs.packaging
        + costs.shipping_out
        + costs.other_sale
    )
    nsr = sale * _d(variable) - _d(fixed_sale)
    profit = nsr - tac
    roi = profit / func.nullif(tac, 0)
    return tac, profit, roi


@dataclass
class FeedRow:
    opportunity: Opportunity
    listing: Listing
    brand: Brand | None
    category: Category | None
    image_url: str | None
    favorite_state: str | None
    first_price: Decimal | None
    personal_score: int | None


class OpportunityQueries:
    def __init__(self, session: AsyncSession, user_id: uuid.UUID, econ: UserEconomics) -> None:
        self.session = session
        self.user_id = user_id
        self.econ = econ

    # ------------------------------------------------------------------ feed
    def _personal_expr(self) -> tuple[ColumnElement[Any], list[Any]]:
        prefs = self.econ.preferences
        if prefs is not None and not prefs.personalization_enabled:
            return Opportunity.flip_score, []
        ua_b, ua_c, ua_p = aliased(UserAffinity), aliased(UserAffinity), aliased(UserAffinity)
        joins = [
            (ua_b, and_(ua_b.user_id == self.user_id, ua_b.dimension == "brand", ua_b.key == Brand.slug)),
            (
                ua_c,
                and_(ua_c.user_id == self.user_id, ua_c.dimension == "category", ua_c.key == Category.slug),
            ),
            (
                ua_p,
                and_(
                    ua_p.user_id == self.user_id,
                    ua_p.dimension == "price_band",
                    ua_p.key == price_band_sql(Opportunity.listing_price),
                ),
            ),
        ]
        adj = (
            func.coalesce(ua_b.adjustment, 0)
            + func.coalesce(ua_c.adjustment, 0)
            + func.coalesce(ua_p.adjustment, 0)
        )
        adj = func.greatest(-15, func.least(15, adj))
        expr = func.greatest(0, func.least(100, func.round(Opportunity.flip_score + adj)))
        return expr, joins

    def build(
        self, f: OpportunityFilters, light: bool = True
    ) -> tuple[Select[Any], dict[str, ColumnElement[Any]]]:
        """Feed query. ``light`` (cards) skips the large JSON/text columns only the detail page uses."""
        params = f.model_dump()
        hidden_gems = False
        if f.preset:
            for k, v in PRESETS[f.preset].items():
                if k == "_hidden_gems":
                    hidden_gems = True
                elif k == "sort" or params.get(k) in (None, False):
                    params[k] = v
        tac, profit, roi = user_money_sql(self.econ.costs)
        personal, aff_joins = self._personal_expr()
        image = (
            select(ListingImage.url)
            .where(ListingImage.listing_id == Listing.id, ListingImage.removed_at.is_(None))
            .order_by(ListingImage.position)
            .limit(1)
            .scalar_subquery()
        )
        first_price = (
            select(ListingPriceHistory.price)
            .where(ListingPriceHistory.listing_id == Listing.id)
            .order_by(ListingPriceHistory.observed_at)
            .limit(1)
            .scalar_subquery()
        )
        fav = aliased(Favorite)
        stmt = (
            select(
                Opportunity,
                Listing,
                Brand,
                Category,
                image.label("image_url"),
                fav.state.label("favorite_state"),
                first_price.label("first_price"),
                personal.label("personal_score"),
            )
            .join(Listing, Listing.id == Opportunity.listing_id)
            .outerjoin(Brand, Brand.id == Listing.brand_id)
            .outerjoin(Category, Category.id == Listing.category_id)
            .outerjoin(fav, and_(fav.listing_id == Listing.id, fav.user_id == self.user_id))
            # Brand and category are selected explicitly above: skip the model's eager joins.
            .options(noload(Listing.brand), noload(Listing.category), noload(Listing.seller))
        )
        if light:
            stmt = stmt.options(
                *(
                    defer(col, raiseload=True)
                    for col in (
                        Opportunity.score_breakdown,
                        Opportunity.risk_factors,
                        Opportunity.market_snapshot,
                        Opportunity.ai_analysis,
                        Listing.description,
                        Listing.identification,
                        Listing.raw,
                    )
                )
            )
        for alias, cond in aff_joins:
            stmt = stmt.outerjoin(alias, cond)

        if not params["include_inactive"]:
            stmt = stmt.where(Opportunity.is_active.is_(True))
        if not params.get("include_insufficient"):
            stmt = stmt.where(Opportunity.data_quality != "insufficient")
        if params["state"]:
            stmt = stmt.where(fav.state == params["state"])
        elif not params["include_ignored"]:
            stmt = stmt.where(or_(fav.state.is_(None), fav.state != FavoriteState.IGNORED.value))
        if q := (params["q"] or "").strip():
            stmt = stmt.where(
                or_(
                    Listing.title.ilike(f"%{_escape_like(q)}%", escape="\\"),
                    func.similarity(Listing.title, q) > 0.3,
                )
            )
        if params["brands"]:
            stmt = stmt.where(Brand.slug.in_(params["brands"]))
        if params["categories"]:
            parent = aliased(Category)
            stmt = stmt.outerjoin(parent, parent.id == Category.parent_id).where(
                or_(Category.slug.in_(params["categories"]), parent.slug.in_(params["categories"]))
            )
        if params["sizes"]:
            stmt = stmt.where(Listing.size_normalized.in_([s.upper() for s in params["sizes"]]))
        if params["conditions"]:
            stmt = stmt.where(Listing.condition.in_(params["conditions"]))
        if params["countries"]:
            stmt = stmt.where(Listing.country.in_([c.upper() for c in params["countries"]]))
        if params["demand_levels"]:
            stmt = stmt.where(Opportunity.demand_level.in_(params["demand_levels"]))
        if params["min_price"] is not None:
            stmt = stmt.where(Opportunity.listing_price >= _d(params["min_price"]))
        if params["max_price"] is not None:
            stmt = stmt.where(Opportunity.listing_price <= _d(params["max_price"]))
        if params["min_profit"] is not None:
            stmt = stmt.where(profit >= _d(params["min_profit"]))
        if params["min_roi"] is not None:
            stmt = stmt.where(roi >= _d(params["min_roi"]))
        if params["min_flip"] is not None:
            stmt = stmt.where(Opportunity.flip_score >= params["min_flip"])
        if params["min_confidence"] is not None:
            stmt = stmt.where(Opportunity.confidence_score >= params["min_confidence"])
        if params["max_risk"] is not None:
            stmt = stmt.where(Opportunity.risk_score <= params["max_risk"])
        if params["min_velocity"] is not None:
            stmt = stmt.where(Opportunity.velocity_score >= params["min_velocity"])
        now = datetime.now(UTC)
        if params["published_within_hours"]:
            stmt = stmt.where(Listing.listed_at >= now - timedelta(hours=params["published_within_hours"]))
        if params["published_after"]:
            stmt = stmt.where(Listing.listed_at >= params["published_after"])
        if params["published_before"]:
            stmt = stmt.where(Listing.listed_at <= params["published_before"])
        if params["vintage_only"]:
            stmt = stmt.where(Listing.is_vintage.is_(True))
        if params["ultra_only"]:
            stmt = stmt.where(Opportunity.is_ultra_deal.is_(True))
        if hidden_gems:
            # Badly described listings (low identification) that are still clearly undervalued.
            stmt = stmt.where(
                Opportunity.identification_confidence < 70, Opportunity.discount_vs_market >= 0.3
            )

        rap = risk_adjusted_sql(profit)
        sort_cols = {
            "expected": [rap.desc().nulls_last(), Opportunity.flip_score.desc()],
            "flip": [Opportunity.flip_score.desc(), Opportunity.confidence_score.desc()],
            "personal": [personal.desc(), Opportunity.flip_score.desc()],
            "profit": [profit.desc().nulls_last()],
            "roi": [roi.desc().nulls_last()],
            "newest": [Listing.listed_at.desc()],
            "discount": [Opportunity.discount_vs_market.desc().nulls_last()],
            "confidence": [Opportunity.confidence_score.desc(), Opportunity.flip_score.desc()],
            "velocity": [Opportunity.velocity_score.desc().nulls_last(), Opportunity.flip_score.desc()],
            "price_asc": [Opportunity.listing_price.asc()],
            "risk_asc": [Opportunity.risk_score.asc(), Opportunity.flip_score.desc()],
        }[params["sort"]]
        stmt = stmt.order_by(*sort_cols, Opportunity.id)
        return stmt, {"tac": tac, "profit": profit, "roi": roi, "rap": rap}

    async def feed(self, f: OpportunityFilters) -> tuple[list[OpportunityCard], int]:
        stmt, _ = self.build(f)
        total = (
            await self.session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
        ).scalar_one()
        rows = (await self.session.execute(stmt.offset((f.page - 1) * f.page_size).limit(f.page_size))).all()
        cards = [self.card(FeedRow(*r)) for r in rows]
        return cards, total

    # ------------------------------------------------------------------ card
    def card(self, row: FeedRow) -> OpportunityCard:
        o, li = row.opportunity, row.listing
        costs = self.econ.costs
        scenario = (
            profit_for(
                o.listing_price, o.expected_sale_price, costs, li.shipping_fee, li.buyer_protection_fee
            )
            if o.expected_sale_price is not None
            else None
        )
        tac = (
            scenario.acquisition.total
            if scenario
            else profit_for(
                o.listing_price, Decimal(0), costs, li.shipping_fee, li.buyer_protection_fee
            ).acquisition.total
        )
        reasons = [
            Reason.model_validate(r)
            for r in (o.explanation or [])
            if r.get("type") in ("positive", "negative")
        ][:4]
        previous = row.first_price if row.first_price is not None and row.first_price > li.price else None
        return OpportunityCard(
            id=o.id,
            listing_id=li.id,
            title=li.title,
            brand=BrandRef(slug=row.brand.slug, name=row.brand.name) if row.brand else None,
            category=CategoryRef(slug=row.category.slug, name=row.category.name, name_it=row.category.name_it)
            if row.category
            else None,
            model_name=li.model_name,
            size=li.size_normalized,
            condition=li.condition,
            country=li.country,
            image_url=row.image_url,
            url=li.url,
            is_active=o.is_active,
            listing_status=li.status,
            listing_price=li.price,
            currency=li.currency,
            total_acquisition_cost=tac,
            fair_market_value=o.fair_market_value,
            expected_sale_price=o.expected_sale_price,
            expected_profit=scenario.net_profit if scenario else None,
            expected_roi=scenario.roi if scenario else None,
            discount_vs_market=o.discount_vs_market,
            flip_score=o.flip_score,
            personal_flip_score=int(row.personal_score) if row.personal_score is not None else None,
            confidence_score=o.confidence_score,
            risk_score=o.risk_score,
            risk_level=o.risk_level,
            demand_level=o.demand_level,
            velocity_score=o.velocity_score,
            estimated_days_to_sell=float(o.estimated_days_to_sell)
            if o.estimated_days_to_sell is not None
            else None,
            velocity_bucket=o.velocity_bucket,
            deal_tier=o.deal_tier,
            is_ultra_deal=o.is_ultra_deal,
            verdict=o.verdict,
            recommended_action=o.recommended_action,
            published_at=li.published_at,
            analyzed_at=o.analyzed_at,
            favorite_state=row.favorite_state,
            previous_price=previous,
            top_reasons=reasons,
            data_quality=o.data_quality,
            insufficient_reason=o.insufficient_reason,
            headline=o.headline,
            analysis_depth=o.analysis_depth,
            risk_adjusted_profit=risk_adjusted(
                scenario.net_profit if scenario else None, o.sale_probability, o.authenticity_probability
            )
            if o.expected_sale_price is not None
            else None,
            sale_probability=o.sale_probability,
            authenticity_probability=o.authenticity_probability,
            authenticity_verdict=o.authenticity_verdict,
            decision_verdict=o.decision_verdict,
            data_completeness_score=o.data_completeness_score,
        )

    async def card_by_listing_ids(self, listing_ids: list[uuid.UUID]) -> list[OpportunityCard]:
        if not listing_ids:
            return []
        stmt, _ = self.build(
            OpportunityFilters(
                include_inactive=True, include_ignored=True, include_insufficient=True, page_size=100
            )
        )
        stmt = stmt.where(Listing.id.in_(listing_ids))
        return [self.card(FeedRow(*r)) for r in (await self.session.execute(stmt)).all()]

    # ---------------------------------------------------------------- detail
    async def detail(self, opportunity_id: uuid.UUID) -> OpportunityDetail:
        stmt, _ = self.build(
            OpportunityFilters(include_inactive=True, include_ignored=True, include_insufficient=True),
            light=False,
        )
        row = (await self.session.execute(stmt.where(Opportunity.id == opportunity_id))).first()
        if row is None:
            raise NotFoundError("Opportunità non trovata o non più disponibile.")
        fr = FeedRow(*row)
        card = self.card(fr)
        o = fr.opportunity
        listing = (
            await self.session.execute(
                select(Listing).options(selectinload(Listing.images)).where(Listing.id == o.listing_id)
            )
        ).scalar_one()

        comp_listing = aliased(Listing)
        comp_image = (
            select(ListingImage.url)
            .where(ListingImage.listing_id == comp_listing.id, ListingImage.removed_at.is_(None))
            .order_by(ListingImage.position)
            .limit(1)
            .scalar_subquery()
        )
        comp_rows = (
            await self.session.execute(
                select(MarketComparable, comp_listing, comp_image.label("image_url"))
                .join(comp_listing, comp_listing.id == MarketComparable.comparable_listing_id)
                .where(MarketComparable.listing_id == o.listing_id)
                .order_by(MarketComparable.included.desc(), MarketComparable.similarity.desc())
            )
        ).all()
        comparables = [
            ComparableOut(
                listing_id=cl.id,
                title=cl.title,
                url=cl.url,
                price=mc.price,
                adjusted_price=mc.adjusted_price,
                condition=cl.condition,
                size=cl.size_normalized,
                country=cl.country,
                status=cl.status,
                similarity=float(mc.similarity),
                is_sold=mc.is_sold,
                included=mc.included,
                exclusion_reason=mc.exclusion_reason,
                listing_date=cl.published_at,
                sold_at=cl.sold_at,
                image_url=img,
            )
            for mc, cl, img in comp_rows
        ]
        history = (
            await self.session.execute(
                select(ListingPriceHistory.price, ListingPriceHistory.observed_at)
                .where(ListingPriceHistory.listing_id == o.listing_id)
                .order_by(ListingPriceHistory.observed_at)
            )
        ).all()

        scenarios = self._scenarios(o, listing)
        smart = self._smart_buy(o, listing)
        snapshot = o.market_snapshot or {}
        breakdown = o.score_breakdown or {}
        seller = listing.seller
        listing_out = ListingDetailOut(
            id=listing.id,
            external_id=listing.external_id,
            provider=listing.provider,
            url=listing.url,
            title=listing.title,
            description=listing.description,
            price=listing.price,
            currency=listing.currency,
            brand_raw=listing.brand_raw,
            category_raw=listing.category_raw,
            size_raw=listing.size_raw,
            condition_raw=listing.condition_raw,
            condition=listing.condition,
            color=listing.color,
            material=listing.material,
            country=listing.country,
            status=listing.status,
            published_at=listing.published_at,
            first_seen_at=listing.first_seen_at,
            last_seen_at=listing.last_seen_at,
            favourite_count=listing.favourite_count,
            view_count=listing.view_count,
            shipping_fee=listing.shipping_fee,
            buyer_protection_fee=listing.buyer_protection_fee,
            buyer_protection_available=listing.buyer_protection_available,
            is_vintage=listing.is_vintage,
            images=[ImageOut(url=i.url, position=i.position) for i in listing.images],
            seller=SellerOut(
                rating=float(seller.rating) if seller.rating is not None else None,
                review_count=seller.review_count,
                reliability_score=o.seller_score,
                reliability=breakdown.get("seller"),
            )
            if seller
            else None,
            duplicate_of_id=listing.duplicate_of_id,
        )
        listing_price = float(o.listing_price)
        stats = snapshot.get("stats") or {}
        median = stats.get("median")
        market = {
            "fair_market_value": float(o.fair_market_value) if o.fair_market_value is not None else None,
            "median": float(o.market_median) if o.market_median is not None else None,
            "mean": float(o.market_mean) if o.market_mean is not None else None,
            "p25": float(o.market_p25) if o.market_p25 is not None else None,
            "p75": float(o.market_p75) if o.market_p75 is not None else None,
            "min_reasonable": float(o.market_min) if o.market_min is not None else None,
            "max_reasonable": float(o.market_max) if o.market_max is not None else None,
            "quick_sale_price": float(o.quick_sale_price) if o.quick_sale_price is not None else None,
            "expected_sale_price": float(o.expected_sale_price)
            if o.expected_sale_price is not None
            else None,
            "optimistic_sale_price": float(o.optimistic_sale_price)
            if o.optimistic_sale_price is not None
            else None,
            "listing_price": listing_price,
            "discount_vs_market": float(o.discount_vs_market) if o.discount_vs_market is not None else None,
            "discount_vs_median": round((listing_price - median) / median, 4) if median else None,
            **{
                k: snapshot.get(k)
                for k in (
                    "n_used",
                    "n_sold",
                    "n_active",
                    "n_outliers",
                    "avg_similarity",
                    "ask_to_sale_ratio",
                    "confidence",
                    "confidence_breakdown",
                    "notes",
                    "histogram",
                    "sold_stats",
                    "active_stats",
                    "used_prior",
                )
            },
        }
        insights = user_insights(breakdown.get("insights"), card, smart)
        return OpportunityDetail(
            insights=insights,
            card=card,
            listing=listing_out,
            identification=listing.identification,
            market=market,
            comparables=comparables,
            demand={
                "level": o.demand_level,
                "score": o.demand_score,
                "sell_through_rate": float(o.sell_through_rate) if o.sell_through_rate is not None else None,
                **(breakdown.get("demand") or {}),
            },
            velocity={
                "estimated_days": float(o.estimated_days_to_sell)
                if o.estimated_days_to_sell is not None
                else None,
                "bucket": o.velocity_bucket,
                "score": o.velocity_score,
                **(breakdown.get("velocity") or {}),
            },
            scenarios=scenarios,
            smart_buy=smart,
            risk={
                "score": o.risk_score,
                "level": o.risk_level,
                "factors": o.risk_factors,
                "signals": breakdown.get("risk_signals") or [],
            },
            score={
                "flip_score": o.flip_score,
                "personal_flip_score": card.personal_flip_score,
                "confidence_score": o.confidence_score,
                "deal_tier": o.deal_tier,
                "is_ultra_deal": o.is_ultra_deal,
                "components": breakdown.get("components"),
                "penalties": breakdown.get("penalties"),
                "cap": breakdown.get("cap"),
                "base": breakdown.get("base"),
                "confidence_components": breakdown.get("confidence_components"),
                "algorithm_version": o.algorithm_version,
                "analyzed_at": o.analyzed_at.isoformat(),
                "data_quality": o.data_quality,
                "insufficient_reason": o.insufficient_reason,
                "headline": o.headline,
                "analysis_depth": o.analysis_depth,
                "acquisition_mode": o.acquisition_mode,
            },
            market_comparison=breakdown.get("market_comparison"),
            time_online=breakdown.get("velocity_detail"),
            explanation=[Reason.model_validate(r) for r in o.explanation or []],
            ai_analysis=o.ai_analysis,
            price_history=[PricePoint(price=p, observed_at=t) for p, t in history],
            recommended_action=smart.action,
            decision=o.decision,
        )

    def _scenarios(self, o: Opportunity, listing: Listing) -> list[ScenarioOut]:
        costs = self.econ.costs
        days = (o.score_breakdown or {}).get("velocity") or {}
        out: list[ScenarioOut] = []
        for name, price, d in (
            ("conservative", o.quick_sale_price, days.get("quick_sale_days")),
            (
                "expected",
                o.expected_sale_price,
                float(o.estimated_days_to_sell) if o.estimated_days_to_sell else None,
            ),
            ("optimistic", o.optimistic_sale_price, days.get("optimistic_sale_days")),
        ):
            if price is None:
                continue
            r = profit_for(o.listing_price, price, costs, listing.shipping_fee, listing.buyer_protection_fee)
            out.append(
                ScenarioOut(
                    name=name,  # type: ignore[arg-type]
                    sale_price=price,
                    total_acquisition_cost=r.acquisition.total,
                    net_sale_revenue=r.sale.net,
                    net_profit=r.net_profit,
                    roi=r.roi,
                    estimated_days=d,
                    acquisition_breakdown=acquisition_lines(r.acquisition),
                    sale_breakdown=sale_lines(r.sale),
                )
            )
        return out

    def _smart_buy(self, o: Opportunity, listing: Listing) -> SmartBuyOut:
        t: EconomicTargets = self.econ.targets
        costs = self.econ.costs
        max_buy = max_buy_price(o.expected_sale_price, costs, t.min_profit, t.min_roi, listing.shipping_fee)
        good_buy = max_buy_price(o.quick_sale_price, costs, t.min_profit, t.min_roi, listing.shipping_fee)
        plan = build_offer_plan(
            o.listing_price,
            max_buy,
            good_buy,
            o.flip_score,
            o.risk_score,
            market_reliable=o.fair_market_value is not None,
        )
        return SmartBuyOut(
            max_buy_price=max_buy,
            good_buy_price=good_buy,
            suggested_offer=plan.suggested_offer,
            listed_price=o.listing_price,
            action=plan.action.value,
            rationale=plan.rationale,
            min_profit=t.min_profit,
            min_roi=t.min_roi,
        )

    # ------------------------------------------------------------------ stats
    async def quick_stats(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        _tac, profit, roi = user_money_sql(self.econ.costs)
        active_good = and_(Opportunity.is_active.is_(True), Opportunity.flip_score >= 60)
        row = (
            await self.session.execute(
                select(
                    func.count().filter(and_(active_good, Opportunity.created_at >= day_start)),
                    func.avg(roi).filter(active_good),
                    func.coalesce(
                        func.sum(profit).filter(
                            and_(Opportunity.is_active.is_(True), Opportunity.flip_score >= 70)
                        ),
                        0,
                    ),
                    func.count().filter(
                        and_(Opportunity.is_active.is_(True), Opportunity.is_ultra_deal.is_(True))
                    ),
                    func.count().filter(Opportunity.analyzed_at >= day_start),
                    func.count().filter(active_good),
                )
                .select_from(Opportunity)
                .join(Listing, Listing.id == Opportunity.listing_id)
            )
        ).one()
        tracked = (await self.session.execute(select(func.count()).select_from(Listing))).scalar_one()
        state = await self.session.get(SystemState, "scanner_last_run")
        return {
            "opportunities_today": row[0],
            "average_expected_roi": float(row[1]) if row[1] is not None else None,
            "potential_profit": float(row[2] or 0),
            "ultra_deals": row[3],
            "listings_analyzed": row[4],
            "active_opportunities": row[5],
            "listings_tracked": tracked,
            "last_scan_at": state.value.get("at") if state else None,
        }


def acquisition_lines(a: AcquisitionCost) -> list[CostLine]:
    lines = [
        CostLine(label="Prezzo d'acquisto", amount=a.purchase_price),
        CostLine(label="Protezione acquisti", amount=a.buyer_protection),
        CostLine(label="Spedizione", amount=a.shipping),
        CostLine(label="Altri costi d'acquisto", amount=a.other),
    ]
    if a.restoration:
        lines.append(CostLine(label="Ripristino", amount=a.restoration))
    return lines


def sale_lines(s: SaleRevenue) -> list[CostLine]:
    lines = [
        CostLine(label="Commissioni di vendita", amount=s.selling_fees),
        CostLine(label="Promozione / advertising", amount=s.advertising),
        CostLine(label="Imballaggio", amount=s.packaging),
        CostLine(label="Costi di pagamento", amount=s.payment_fees),
        CostLine(label="Spedizione a tuo carico", amount=s.shipping),
        CostLine(label="Altri costi di vendita", amount=s.other),
    ]
    if s.contingency:
        lines.append(CostLine(label="Riserva per imprevisti", amount=s.contingency))
    return lines


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
