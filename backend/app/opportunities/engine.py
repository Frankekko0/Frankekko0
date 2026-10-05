"""Opportunity engine: the pure computation behind every analysis.

Given a subject listing, its comparable candidates and optional segment statistics, produce the
full analysis (market value, scenarios, demand, velocity, costs, profit, ROI, max buy price,
offers, risk, confidence, Flip Score, explanation and AI verdict). No I/O happens here, which
keeps the core deterministic, fast to test and reusable for ad-hoc analyses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.ai.deal_analyst import DealAnalysis, DealContext, RuleBasedDealAnalyst, ScenarioSummary
from app.demand.analysis import DemandResult, VelocityResult, analyze_demand, analyze_velocity
from app.domain.enums import Condition, DealTier, RecommendedAction
from app.pricing.comparables import ItemProfile, ScoredComparable, select_comparables
from app.pricing.market_value import MarketEstimate, SegmentPrior, estimate_market_value
from app.profit.calculator import CostProfile, Scenario, acquisition_cost, max_buy_price, profit_scenarios
from app.profit.offers import OfferPlan, build_offer_plan
from app.scoring.confidence import ConfidenceInput, ConfidenceResult, compute_confidence
from app.scoring.explain import ExplanationContext, build_explanation
from app.scoring.flip import FlipInput, FlipResult, compute_flip_score, deal_tier, is_ultra_deal
from app.scoring.risk import RiskInput, RiskResult, assess_risk
from app.scoring.seller import SellerProfile, SellerScore, seller_reliability

PRICING_COMPARABLES = 60


@dataclass(frozen=True)
class EconomicTargets:
    min_profit: Decimal = Decimal("10")
    min_roi: Decimal = Decimal("0.40")


@dataclass
class SubjectContext:
    profile: ItemProfile
    description_length: int
    photo_count: int
    favourite_count: int
    listing_age_hours: float | None
    shipping_fee: Decimal | None
    brand_name: str | None
    brand_counterfeit_risk: float
    category_baseline_days: int
    identification: dict[str, Any]
    identification_confidence: int
    seller: SellerProfile | None
    seller_account_age_days: int | None
    is_repost: bool = False
    vision: dict[str, Any] | None = None

    @property
    def suspicious_terms(self) -> list[str]:
        return list(self.identification.get("suspicious_terms") or [])

    @property
    def defect_terms(self) -> list[str]:
        return list(self.identification.get("defect_terms") or [])


@dataclass
class AnalysisResult:
    subject: SubjectContext
    market: MarketEstimate
    comparables: list[ScoredComparable]
    demand: DemandResult
    velocity: VelocityResult
    scenarios: list[Scenario]
    total_acquisition_cost: Decimal
    discount_vs_market: Decimal | None
    max_buy_price: Decimal | None
    good_buy_price: Decimal | None
    offer: OfferPlan
    seller: SellerScore
    risk: RiskResult
    confidence: ConfidenceResult
    flip: FlipResult
    tier: DealTier
    ultra: bool
    explanation: list[dict[str, Any]]
    analysis: DealAnalysis
    pool_size: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def scenario(self, name: str) -> Scenario | None:
        return next((s for s in self.scenarios if s.name == name), None)

    @property
    def expected_profit(self) -> Decimal | None:
        s = self.scenario("expected")
        return s.result.net_profit if s else None

    @property
    def expected_roi(self) -> Decimal | None:
        s = self.scenario("expected")
        return s.result.roi if s else None


def run_analysis(
    subject: SubjectContext,
    candidates: list[ItemProfile],
    now: datetime,
    costs: CostProfile,
    targets: EconomicTargets,
    prior: SegmentPrior | None = None,
    weights: dict[str, float] | None = None,
) -> AnalysisResult:
    pool = select_comparables(subject.profile, candidates, now, max_count=10_000)
    comps = pool[:PRICING_COMPARABLES]
    market = estimate_market_value(comps, subject.profile.condition, now, prior)

    # ---- demand & velocity (whole similar pool, not only the pricing sample) ----------------
    sold = sum(1 for c in pool if c.item.status == "sold")
    possibly = sum(1 for c in pool if c.item.status == "possibly_sold")
    active = sum(1 for c in pool if c.item.status == "active")
    if not pool and prior and prior.sell_through_rate is not None:
        approx_n = min(prior.sample_size, 20)
        sold = round(prior.sell_through_rate * approx_n)
        active = approx_n - sold
    fav_rates = [
        fav / max((now - c.item.published_at).total_seconds() / 86400, 0.5)
        for c in pool
        if c.item.status == "active" and c.item.published_at and (fav := c.item.favourite_count) is not None
    ]
    demand = analyze_demand(
        sold,
        active,
        possibly,
        favourites=subject.favourite_count,
        listing_age_hours=subject.listing_age_hours,
        segment_avg_favourites_per_day=(sum(fav_rates) / len(fav_rates)) if fav_rates else None,
    )
    samples = [
        ((c.item.sold_at - c.item.published_at).total_seconds() / 86400, c.weight)
        for c in pool
        if c.item.status == "sold" and c.item.sold_at and c.item.published_at
    ]
    baseline = float(subject.category_baseline_days)
    if not samples and prior and prior.avg_days_to_sale:
        baseline = prior.avg_days_to_sale
    velocity = analyze_velocity(samples, demand.sell_through_rate, baseline)

    # ---- economics --------------------------------------------------------------------------
    price = subject.profile.price
    acq = acquisition_cost(price, costs, subject.shipping_fee)
    scenarios = profit_scenarios(
        price,
        market.quick_sale_price,
        market.expected_sale_price,
        market.optimistic_sale_price,
        costs,
        subject.shipping_fee,
        (velocity.quick_sale_days, velocity.estimated_days, velocity.optimistic_sale_days),
    )
    fmv = market.fair_market_value
    discount = ((fmv - price) / fmv).quantize(Decimal("0.0001")) if fmv and fmv > 0 else None
    max_buy = max_buy_price(
        market.expected_sale_price, costs, targets.min_profit, targets.min_roi, subject.shipping_fee
    )
    good_buy = max_buy_price(
        market.quick_sale_price, costs, targets.min_profit, targets.min_roi, subject.shipping_fee
    )
    expected = next((s for s in scenarios if s.name == "expected"), None)

    # ---- seller, risk, confidence, flip --------------------------------------------------------
    seller = seller_reliability(subject.seller, now)
    vision = subject.vision or {}
    risk = assess_risk(
        RiskInput(
            price=price,
            fair_market_value=fmv,
            brand_counterfeit_risk=subject.brand_counterfeit_risk,
            seller=subject.seller,
            seller_account_age_days=subject.seller_account_age_days,
            photo_count=subject.photo_count,
            description_length=subject.description_length,
            suspicious_terms=tuple(subject.suspicious_terms),
            defect_terms=tuple(subject.defect_terms),
            vision_defects=tuple(
                d.get("kind", "") for d in vision.get("defects", []) if d.get("severity") != "minor"
            ),
            vision_concerns=tuple(vision.get("authenticity_concerns", [])),
            identification_confidence=subject.identification_confidence,
            comparables_used=market.n_used,
            market_confidence=market.confidence,
            condition=subject.profile.condition,
            photos_reused_by_other_seller=bool(subject.identification.get("photos_reused_by_other_seller")),
            is_repost=subject.is_repost,
            brand_known=subject.profile.brand is not None,
        )
    )
    confidence = compute_confidence(
        ConfidenceInput(
            market_confidence=market.confidence,
            identification_confidence=subject.identification_confidence,
            photo_count=subject.photo_count,
            description_length=subject.description_length,
            size_known=subject.profile.size is not None,
            condition_known=subject.profile.condition != Condition.UNKNOWN,
            seller_known=subject.seller is not None,
            demand_observations=demand.observations,
        )
    )
    flip = compute_flip_score(
        FlipInput(
            discount_vs_market=float(discount) if discount is not None else None,
            expected_roi=float(expected.result.roi) if expected else None,
            expected_profit=float(expected.result.net_profit) if expected else None,
            demand_score=demand.score,
            velocity_score=velocity.score,
            listing_age_hours=subject.listing_age_hours,
            seller_score=seller.score,
            sell_through_rate=demand.sell_through_rate,
            comparables_used=market.n_used,
            market_dispersion=market.stats.dispersion if market.stats else None,
            market_confidence=market.confidence,
            identification_confidence=subject.identification_confidence,
            condition=subject.profile.condition,
            suspicious_terms=bool(subject.suspicious_terms),
            brand_counterfeit_risk=subject.brand_counterfeit_risk,
            risk_score=risk.score,
            photos_reused=bool(subject.identification.get("photos_reused_by_other_seller")),
        ),
        weights,
    )
    roi = expected.result.roi if expected else None
    offer = build_offer_plan(
        price, max_buy, good_buy, flip.score, risk.score, market_reliable=fmv is not None
    )
    explanation = build_explanation(
        ExplanationContext(
            flip=flip,
            discount_vs_market=float(discount) if discount is not None else None,
            expected_roi=roi,
            expected_profit=expected.result.net_profit if expected else None,
            demand=demand,
            velocity=velocity,
            seller=seller,
            listing_age_hours=subject.listing_age_hours,
            comparables_used=market.n_used,
            sold_comparables=market.n_sold,
        )
    )
    ctx = build_deal_context(
        subject,
        market,
        scenarios,
        acq.total,
        discount,
        max_buy,
        good_buy,
        offer,
        demand,
        velocity,
        flip.score,
        confidence.score,
        risk,
    )
    analysis = RuleBasedDealAnalyst().analyze_sync(ctx)
    return AnalysisResult(
        subject=subject,
        market=market,
        comparables=comps,
        demand=demand,
        velocity=velocity,
        scenarios=scenarios,
        total_acquisition_cost=acq.total,
        discount_vs_market=discount,
        max_buy_price=max_buy,
        good_buy_price=good_buy,
        offer=offer,
        seller=seller,
        risk=risk,
        confidence=confidence,
        flip=flip,
        tier=deal_tier(flip.score),
        ultra=is_ultra_deal(flip.score, confidence.score, roi),
        explanation=explanation,
        analysis=analysis,
        pool_size=len(pool),
    )


def build_deal_context(
    subject: SubjectContext,
    market: MarketEstimate,
    scenarios: list[Scenario],
    total_cost: Decimal,
    discount: Decimal | None,
    max_buy: Decimal | None,
    good_buy: Decimal | None,
    offer: OfferPlan,
    demand: DemandResult,
    velocity: VelocityResult,
    flip_score: int,
    confidence: int,
    risk: RiskResult,
) -> DealContext:
    ident = subject.identification
    seller_summary = None
    if subject.seller:
        s = subject.seller
        seller_summary = (
            f"{float(s.rating):.1f}★ su {s.review_count} recensioni"
            if s.rating is not None and s.review_count
            else "venditore senza recensioni"
        )
    return DealContext(
        title=subject.profile.title,
        brand=subject.brand_name,
        category=subject.profile.category,
        model=subject.profile.model,
        condition=subject.profile.condition,
        size=subject.profile.size,
        listing_price=subject.profile.price,
        total_acquisition_cost=total_cost,
        fair_market_value=market.fair_market_value,
        discount_vs_market=float(discount) if discount is not None else None,
        scenarios=[
            ScenarioSummary(
                name=s.name, sale_price=s.sale_price, net_profit=s.result.net_profit, roi=s.result.roi
            )
            for s in scenarios
        ],
        max_buy_price=max_buy,
        good_buy_price=good_buy,
        suggested_offer=offer.suggested_offer,
        demand_level=demand.level.value,
        sell_through_rate=demand.sell_through_rate,
        estimated_days_to_sell=velocity.estimated_days,
        flip_score=flip_score,
        confidence_score=confidence,
        risk_score=risk.score,
        risk_factors=[f.label for f in risk.factors],
        seller_summary=seller_summary,
        identification_confidence=subject.identification_confidence,
        comparables_used=market.n_used,
        sold_comparables=market.n_sold,
        market_notes=market.notes,
        suspicious_terms=list(ident.get("suspicious_terms") or []),
        defect_terms=list(ident.get("defect_terms") or []),
        is_vintage=subject.profile.is_vintage,
    )


def recommended_action(result: AnalysisResult) -> RecommendedAction:
    if result.analysis.verdict == "SKIP" and result.offer.action != RecommendedAction.WATCH:
        return RecommendedAction.SKIP
    return result.offer.action
