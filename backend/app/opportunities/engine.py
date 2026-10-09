"""Opportunity engine: the pure computation behind every analysis.

Given a subject listing, its comparable candidates and optional segment statistics, produce the
full analysis (market value, scenarios, demand, velocity, costs, profit, ROI, max buy price,
offers, risk, confidence, Flip Score, explanation and AI verdict). No I/O happens here, which
keeps the core deterministic, fast to test and reusable for ad-hoc analyses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.ai.deal_analyst import DealAnalysis, DealContext, RuleBasedDealAnalyst, ScenarioSummary
from app.analytics.calibration import Calibration
from app.authenticity.assess import AuthInput, assess, photo_evidence
from app.demand.analysis import DemandResult, VelocityResult, analyze_demand, analyze_velocity
from app.demand.time_online import time_online
from app.domain.enums import Condition, DealTier, RecommendedAction
from app.opportunities import insights as ins
from app.pricing.comparables import ItemProfile, ScoredComparable, select_comparables
from app.pricing.comparison import market_comparison
from app.pricing.evidence import (
    NEW_CAP_NOTE,
    PriceEvidence,
    build_provenance,
    cap_at_new_price,
    with_evidence,
)
from app.pricing.market_value import (
    SOLD_ONLY_MIN,
    MarketEstimate,
    SegmentPrior,
    _euros,
    estimate_market_value,
)
from app.profit.calculator import CostProfile, Scenario, acquisition_cost, max_buy_price, profit_scenarios
from app.profit.offers import OfferPlan, build_offer_plan
from app.scoring.confidence import ConfidenceInput, ConfidenceResult, compute_confidence
from app.scoring.explain import DEMAND_LABELS, ExplanationContext, build_explanation
from app.scoring.flip import FlipInput, FlipResult, compute_flip_score, deal_tier, is_ultra_deal
from app.scoring.risk import RiskInput, RiskResult, assess_risk
from app.scoring.seller import SellerProfile, SellerScore, seller_reliability
from app.scoring.signals import Signal, SignalInput, build_signals, description_quality, worst_level

PRICING_COMPARABLES = 60
# On-sale listings kept next to enough sold ones: reference only (see ``estimate_market_value``).
REFERENCE_ASKS = 20
SAME_ITEM_ANOMALY = "Lo stesso articolo in più copie o in più taglie dallo stesso venditore"
# Below this many direct comparables the estimate is labelled "indicative".
LIMITED_BELOW = 8


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
    is_repost: bool = False
    vision: dict[str, Any] | None = None
    description: str = ""
    # Buyer protection actually shown on the listing (None: the user's cost profile applies).
    buyer_protection_fee: Decimal | None = None
    price_history: list[tuple[datetime, Decimal]] = field(default_factory=list)
    # Price behaviour of the seller's other listings (see ``AnalysisPipeline.seller_habits``).
    seller_habits: dict[str, Any] | None = None

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
    # "ok" | "limited" | "insufficient": insufficient means no reliable estimate (score hidden).
    data_quality: str = "ok"
    insufficient_reason: str | None = None
    market_comparison: dict[str, Any] = field(default_factory=dict)
    time_online: dict[str, Any] = field(default_factory=dict)
    risk_signals: list[Signal] = field(default_factory=list)
    headline: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    insights: dict[str, Any] = field(default_factory=dict)
    risk_adjusted_profit: float | None = None
    sale_probability: float | None = None
    authenticity: dict[str, Any] = field(default_factory=dict)
    # Where every number comes from (see ``app.pricing.evidence.build_provenance``).
    provenance: dict[str, Any] = field(default_factory=dict)

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
    calibration: Calibration | None = None,
    evidence: PriceEvidence | None = None,
) -> AnalysisResult:
    """``candidates`` are marketplace listings (they also feed demand and timing); ``evidence``
    adds the user's own records and other marketplaces' prices to the price only."""
    # The condition the photos show when worse than the declared one: prices follow the item.
    condition = ins.effective_condition(subject.profile.condition, subject.vision)
    priced = (
        subject.profile
        if condition == subject.profile.condition
        else replace(subject.profile, condition=condition)
    )
    similar, comps, market = price_estimate(priced, candidates, now, prior, calibration, evidence)
    calibrated = bool(calibration is not None and calibration.active and market.has_value)
    pool = [c for c in similar if c.item.status in ("sold", "active")]

    # ---- demand & velocity (whole similar pool, not only the pricing sample) ----------------
    sold = sum(1 for c in similar if c.item.status == "sold")
    removed = sum(1 for c in similar if c.item.status == "removed")
    active = sum(1 for c in similar if c.item.status == "active")
    if not similar and prior and prior.sell_through_rate is not None:
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
        removed,
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
    acq = acquisition_cost(price, costs, subject.shipping_fee, subject.buyer_protection_fee)
    scenarios = profit_scenarios(
        price,
        market.quick_sale_price,
        market.expected_sale_price,
        market.optimistic_sale_price,
        costs,
        subject.shipping_fee,
        (velocity.quick_sale_days, velocity.estimated_days, velocity.optimistic_sale_days),
        subject.buyer_protection_fee,
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
    signals = build_signals(
        SignalInput(
            title=subject.profile.title,
            description=subject.description,
            brand_name=subject.brand_name,
            brand_slug=subject.profile.brand,
            brand_counterfeit_risk=subject.brand_counterfeit_risk,
            category=subject.profile.category,
            color=subject.profile.color,
            photo_count=subject.photo_count,
            price=price,
            fair_market_value=fmv,
            suspicious_terms=tuple(subject.suspicious_terms),
            photos_reused_by_other_seller=bool(subject.identification.get("photos_reused_by_other_seller")),
            seller_known=subject.seller is not None,
            seller_rating=float(subject.seller.rating)
            if subject.seller and subject.seller.rating is not None
            else None,
            seller_review_count=subject.seller.review_count if subject.seller else 0,
            seller_anomalies=tuple(subject.seller.anomalies) if subject.seller else (),
            vision=subject.vision,
        )
    )
    by_code = {s.code: s for s in signals}
    has_description = bool(subject.description.strip())
    label_level = by_code["label_photos"].level
    risk = assess_risk(
        RiskInput(
            price=price,
            fair_market_value=fmv,
            brand_counterfeit_risk=subject.brand_counterfeit_risk,
            seller=subject.seller,
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
            condition=condition,
            photos_reused_by_other_seller=bool(subject.identification.get("photos_reused_by_other_seller")),
            is_repost=subject.is_repost,
            brand_known=subject.profile.brand is not None,
            generic_description=description_quality(subject.description).generic if has_description else None,
            label_photo_missing=(label_level in ("low", "medium") and by_code["label_photos"].verifiable)
            if label_level != "info"
            else None,
            title_photo_mismatches=tuple(by_code["title_photo_mismatch"].evidence)
            if by_code["title_photo_mismatch"].level not in ("ok", "info")
            else (),
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
            condition=condition,
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
    comparison = market_comparison(priced, similar, market)
    online = time_online(similar, now)
    quality, reason = assess_data_quality(market, len(similar))
    auth = assess_authenticity(subject, price, fmv)
    p_sale = sale_probability(similar, now, prior)
    margin = float(expected.result.net_profit) if expected and market.has_value else None
    rap = ins.risk_adjusted_profit(margin, p_sale["p"], auth.p_authentic)
    result = AnalysisResult(
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
        pool_size=len(similar),
        data_quality=quality,
        insufficient_reason=reason,
        market_comparison=comparison,
        time_online=online,
        risk_signals=signals,
        risk_adjusted_profit=rap,
        sale_probability=p_sale["p"],
        authenticity=auth.as_dict(),
    )
    result.headline = build_headline(result)
    result.insights = build_insights(result, similar, now, condition, p_sale, margin)
    if velocity.sample_size:
        days_basis = "sold"
    elif prior is not None and prior.avg_days_to_sale:
        days_basis = "segment"
    else:
        days_basis = "category_baseline"
    result.provenance = build_provenance(
        market,
        evidence=evidence,
        prior=prior,
        calibrated=calibrated,
        calibration_n=calibration.n if calibration is not None else 0,
        velocity_days=velocity.estimated_days,
        velocity_n=velocity.sample_size,
        days_basis=days_basis,
        p_sale=p_sale,
        new_cap_applied=any(n.startswith(NEW_CAP_NOTE) for n in market.notes),
    )
    return result


def sale_probability(
    similar: list[ScoredComparable], now: datetime, prior: SegmentPrior | None
) -> dict[str, Any]:
    """P(sold within 30 days) from similar listings with a known outcome; the segment's sold
    share only when those are too few (and said so); otherwise "dati insufficienti"."""
    p = ins.probability_of_sale(similar, now)
    if p["p"] is None and prior and prior.sample_size >= 20 and prior.sell_through_rate is not None:
        return {
            "p": round(float(prior.sell_through_rate), 3),
            "n": prior.sample_size,
            "horizon_days": ins.HORIZON_DAYS,
            "source": "segment",
            "reason": f"{p['reason']} Usata la quota di venduti del segmento (brand e categoria).",
        }
    return {**p, "source": "similar" if p["p"] is not None else None}


def assess_authenticity(subject: SubjectContext, price: Decimal, fmv: Decimal | None) -> Any:
    seller = subject.seller
    photos = photo_evidence(subject.vision)
    return assess(
        AuthInput(
            brand_slug=subject.profile.brand,
            brand_name=subject.brand_name,
            brand_counterfeit_risk=subject.brand_counterfeit_risk,
            price=float(price),
            market_value=float(fmv) if fmv else None,
            photo_count=subject.photo_count,
            suspicious_terms=subject.suspicious_terms,
            seller_reviews=seller.review_count if seller else None,
            seller_rating=float(seller.rating) if seller and seller.rating is not None else None,
            seller_multi_size_same_item=bool(seller and SAME_ITEM_ANOMALY in seller.anomalies),
            photos_reused_by_other_seller=bool(subject.identification.get("photos_reused_by_other_seller")),
            **photos,
        )
    )


def _float(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def _conf(n: int | None, scale: float) -> int:
    return round(100 * (1 - math.exp(-(n or 0) / scale)))


def build_insights(
    r: AnalysisResult,
    similar: list[ScoredComparable],
    now: datetime,
    condition: str,
    p_sale: dict[str, Any],
    margin: float | None,
) -> dict[str, Any]:
    s, m = r.subject, r.market
    seller = s.seller
    inp = ins.InsightInput(
        now=now,
        price=s.profile.price,
        favourites=s.favourite_count or 0,
        listing_age_hours=s.listing_age_hours,
        size=s.profile.size,
        color=s.profile.color,
        condition_declared=s.profile.condition,
        condition_effective=condition,
        price_history=s.price_history,
        seller_rating=float(seller.rating) if seller and seller.rating is not None else None,
        seller_reviews=seller.review_count if seller else None,
        seller_habits=s.seller_habits,
        identification=s.identification,
        vision=s.vision,
    )
    tracked = {c.item.id for c in similar if c.item.tracked}
    has = m.has_value
    comp = r.flip.components
    margin_pillar = sum(comp[k]["score"] for k in ("undervaluation", "roi", "profit")) / 3 if has else 0.0
    demand_pillar = (r.demand.score + r.velocity.score) / 2
    roi = r.expected_roi
    d: dict[str, Any] = {
        "resale": {
            "low": _float(m.quick_sale_price),
            "probable": _float(m.expected_sale_price),
            "high": _float(m.optimistic_sale_price),
            "confidence": m.confidence if has else 0,
            "calibrated": any("calibrati" in n for n in m.notes),
        },
        "net_margin": margin,
        "roi": float(roi) if roi is not None and has else None,
        "margin_confidence": m.confidence if has else 0,
        "days_to_sell": r.velocity.estimated_days if r.velocity.sample_size or has else None,
        "days_confidence": _conf(r.velocity.sample_size, 8),
        "max_price": float(r.max_buy_price) if r.max_buy_price is not None else None,
        "suggested_offer": float(r.offer.suggested_offer) if r.offer.suggested_offer is not None else None,
        "offer_confidence": m.confidence if has else 0,
        "p_sale": {**p_sale, "confidence": _conf(p_sale.get("n"), 15) if p_sale["p"] is not None else 0},
        "authenticity": r.authenticity,
        "risk_adjusted_profit": r.risk_adjusted_profit,
        "pillars": ins.pillars(margin_pillar, demand_pillar, r.risk.score, r.seller.score),
        "demand": ins.demand_detail(inp, similar, tracked),
        "seller": ins.seller_detail(inp),
        "identification": ins.identification_detail(s.identification),
        "condition": ins.condition_check(inp),
        "insufficient_reason": r.insufficient_reason,
        "comparables_rule": "sold_only" if m.n_sold and not m.n_active else "sold_and_active",
    }
    d["reason"] = ins.reason_lines(d)
    return d


def pricing_comparables(pool: list[ScoredComparable]) -> list[ScoredComparable]:
    """The most similar listings; with enough sales, the most similar *sold* ones (real prices),
    plus a few on-sale ones shown for reference."""
    sold = [c for c in pool if c.item.status == "sold"]
    if len(sold) >= SOLD_ONLY_MIN:
        active = [c for c in pool if c.item.status != "sold"]
        return sold[:PRICING_COMPARABLES] + active[:REFERENCE_ASKS]
    return pool[:PRICING_COMPARABLES]


def price_estimate(
    subject: ItemProfile,
    candidates: list[ItemProfile],
    now: datetime,
    prior: SegmentPrior | None,
    calibration: Calibration | None = None,
    evidence: PriceEvidence | None = None,
) -> tuple[list[ScoredComparable], list[ScoredComparable], MarketEstimate]:
    """(every similar listing, comparables used for the price, market estimate).

    Every similar listing (sold, on sale, removed) feeds demand and timing; prices come only
    from sold and on-sale ones: a removed listing tells nothing about what the item is worth.
    The same function runs in the retroactive check, so measured errors are the real ones.
    """
    similar = select_comparables(subject, candidates, now, max_count=10_000)
    comps, market = estimate_from_similar(subject, similar, now, prior, evidence)
    if calibration is not None and calibration.active and market.has_value:
        market = calibrate(market, calibration, subject)
        if evidence is not None and evidence.gate.use_new_cap:
            # The calibrated range replaces the capped one: the new price caps the final maximum.
            market = replace(market, notes=[n for n in market.notes if not n.startswith(NEW_CAP_NOTE)])
            market = cap_at_new_price(market, evidence, subject.condition)
    return similar, comps, market


def estimate_from_similar(
    subject: ItemProfile,
    similar: list[ScoredComparable],
    now: datetime,
    prior: SegmentPrior | None,
    evidence: PriceEvidence | None = None,
) -> tuple[list[ScoredComparable], MarketEstimate]:
    """The price from already scored similar listings plus the extra evidence (own records,
    other marketplaces) under the backtest gate. Concluded sales of every source count for the
    sold-first rule; the comparables passed in are never mutated when evidence is merged."""
    pool = [c for c in similar if c.item.status in ("sold", "active")]
    if evidence is not None:
        pool = with_evidence(subject, pool, evidence, now)
    comps = pricing_comparables(pool)
    market = estimate_market_value(comps, subject.condition, now, prior)
    if evidence is not None and evidence.gate.use_new_cap:
        market = cap_at_new_price(market, evidence, subject.condition)
    return comps, market


def calibrate(market: MarketEstimate, cal: Calibration, subject: ItemProfile) -> MarketEstimate:
    """Minimum and maximum resale from the errors measured on real sales (about 8 in 10 real
    sales fall between them); the probable price moves only if that proved more accurate."""
    expected = float(market.expected_sale_price)  # type: ignore[arg-type]
    low, mid, high = cal.apply(
        expected, subject.category, subject.brand, subject.condition, market.confidence
    )

    mid_d = _euros(mid)
    return replace(
        market,
        fair_market_value=mid_d,
        expected_sale_price=mid_d,
        quick_sale_price=min(_euros(low, "floor"), mid_d),
        optimistic_sale_price=max(_euros(high), mid_d),
        notes=[*market.notes, f"Minimo e massimo calibrati sugli errori misurati su {cal.n} vendite reali."],
    )


def assess_data_quality(market: MarketEstimate, found: int) -> tuple[str, str | None]:
    """Is the estimate reliable? Few comparables are declared, never papered over."""
    if not market.has_value:
        return (
            "insufficient",
            f"Solo {market.n_used} comparabili utilizzabili su {found} annunci simili trovati: "
            "non abbastanza per stimare il valore. Nessun punteggio assegnato.",
        )
    if market.n_used < LIMITED_BELOW or market.confidence < 40:
        return (
            "limited",
            f"Stima indicativa: basata su {market.n_used} comparabili "
            f"(confidenza di mercato {market.confidence}/100).",
        )
    return "ok", None


def build_headline(r: AnalysisResult) -> str:
    """The main reason in one line (used by the browser extension and the tracking page)."""
    if r.data_quality == "insufficient":
        return f"Dati insufficienti: solo {r.market.n_used} comparabili"
    parts: list[str] = []
    if r.discount_vs_market is not None:
        pct = round(float(r.discount_vs_market) * 100)
        parts.append(
            f"{pct}% sotto il mercato"
            if pct > 0
            else f"{-pct}% sopra il mercato"
            if pct < 0
            else "in linea col mercato"
        )
    if r.expected_profit is not None:
        margin = r.expected_profit
        sign = "+" if margin > 0 else "−" if margin < 0 else ""
        roi = round(float(r.expected_roi or 0) * 100)
        parts.append(f"margine {sign}€{abs(margin):.0f} (ROI {roi}%)")
    parts.append(DEMAND_LABELS[r.demand.level].lower())
    worst = worst_level(r.risk_signals)
    if worst in ("medium", "high"):
        flagged = next(s for s in r.risk_signals if s.level == worst)
        parts.append(f"⚠ {flagged.title.lower()}")
    if r.data_quality == "limited":
        parts.append("pochi comparabili")
    return " · ".join(parts)


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
