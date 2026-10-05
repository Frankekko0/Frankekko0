"""Market Price Engine: Fair Market Value and realistic resale scenarios.

Pipeline (pure function ``estimate_market_value``):

1. outlier removal, separately for sold and active comparables (log-space IQR + MAD);
2. "realized" distribution = sold prices (full weight) + active asking prices discounted by the
   ask-to-sale ratio (asks are systematically higher than what items actually sell for) with a
   reduced weight that shrinks as sold evidence grows;
3. optional Bayesian blend with the segment prior from the Market Database when evidence is thin;
4. FMV = weighted median of the realized distribution; scenarios: quick = P25,
   expected = FMV, optimistic = P75 (capped at P90);
5. market confidence from sample size, sold share, similarity, dispersion and recency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from app.pricing.comparables import CONDITION_MULTIPLIER, ScoredComparable
from app.pricing.stats import DistributionStats, WeightedValue, describe, detect_outliers, histogram

DEFAULT_ASK_TO_SALE = 0.88
MIN_COMPARABLES = 3


@dataclass(frozen=True)
class SegmentPrior:
    """Aggregated statistics of the product segment (from the Market Database)."""

    median_price: float
    p25_price: float
    p75_price: float
    sample_size: int
    ask_to_sale_ratio: float | None = None
    sell_through_rate: float | None = None
    avg_days_to_sale: float | None = None


@dataclass
class MarketEstimate:
    fair_market_value: Decimal | None
    quick_sale_price: Decimal | None
    expected_sale_price: Decimal | None
    optimistic_sale_price: Decimal | None
    stats: DistributionStats | None
    sold_stats: DistributionStats | None
    active_stats: DistributionStats | None
    n_used: int
    n_sold: int
    n_active: int
    n_outliers: int
    avg_similarity: float
    ask_to_sale_ratio: float
    confidence: int
    used_prior: bool
    notes: list[str] = field(default_factory=list)
    histogram: list[dict[str, float]] = field(default_factory=list)
    comparables: list[ScoredComparable] = field(default_factory=list)
    confidence_breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def has_value(self) -> bool:
        return self.fair_market_value is not None

    def snapshot(self) -> dict[str, Any]:
        def s(st: DistributionStats | None) -> dict[str, float] | None:
            if st is None:
                return None
            return {k: round(v, 2) if isinstance(v, float) else v for k, v in st.__dict__.items()}

        return {
            "stats": s(self.stats),
            "sold_stats": s(self.sold_stats),
            "active_stats": s(self.active_stats),
            "n_used": self.n_used,
            "n_sold": self.n_sold,
            "n_active": self.n_active,
            "n_outliers": self.n_outliers,
            "avg_similarity": round(self.avg_similarity, 3),
            "ask_to_sale_ratio": round(self.ask_to_sale_ratio, 3),
            "confidence": self.confidence,
            "confidence_breakdown": {k: round(v, 3) for k, v in self.confidence_breakdown.items()},
            "used_prior": self.used_prior,
            "notes": self.notes,
            "histogram": self.histogram,
        }


def _euros(value: float, mode: str = "round") -> Decimal:
    rounding = ROUND_FLOOR if mode == "floor" else ROUND_HALF_UP
    return Decimal(str(value)).quantize(Decimal("1"), rounding=rounding).quantize(Decimal("0.01"))


def _mark_outliers(group: list[ScoredComparable]) -> list[ScoredComparable]:
    if not group:
        return []
    result = detect_outliers([c.adjusted_price for c in group])
    for i in result.removed:
        group[i].included = False
        group[i].exclusion_reason = "outlier"
    return [group[i] for i in result.kept]


def estimate_market_value(
    comparables: list[ScoredComparable],
    subject_condition: str,
    now: datetime,
    prior: SegmentPrior | None = None,
) -> MarketEstimate:
    sold = [c for c in comparables if c.is_sold]
    active = [c for c in comparables if not c.is_sold]
    sold_in = _mark_outliers(sold)
    active_in = _mark_outliers(active)
    n_outliers = (len(sold) - len(sold_in)) + (len(active) - len(active_in))
    notes: list[str] = []

    sold_stats = describe([WeightedValue(c.adjusted_price, c.weight) for c in sold_in]) if sold_in else None
    active_stats = (
        describe([WeightedValue(c.adjusted_price, c.weight) for c in active_in]) if active_in else None
    )

    # Ask-to-sale ratio: how much below asking prices items actually sell.
    if sold_stats and active_stats and sold_stats.n >= 3 and active_stats.n >= 3:
        k_ask = min(1.05, max(0.6, sold_stats.median / active_stats.median))
    elif prior and prior.ask_to_sale_ratio:
        k_ask = prior.ask_to_sale_ratio
    else:
        k_ask = DEFAULT_ASK_TO_SALE

    active_factor = 0.7 if len(sold_in) < 5 else 0.35
    realized: list[WeightedValue] = [WeightedValue(c.adjusted_price, c.weight) for c in sold_in]
    realized += [WeightedValue(c.adjusted_price * k_ask, c.weight * active_factor) for c in active_in]
    used = sold_in + active_in
    n_used = len(used)
    avg_sim = sum(c.similarity for c in used) / n_used if used else 0.0

    if n_used < MIN_COMPARABLES and not (prior and prior.sample_size >= 5):
        notes.append("Non ci sono abbastanza dati per stimare con affidabilità il prezzo di mercato.")
        return MarketEstimate(
            None,
            None,
            None,
            None,
            None,
            sold_stats,
            active_stats,
            n_used,
            len(sold_in),
            len(active_in),
            n_outliers,
            avg_sim,
            k_ask,
            0,
            False,
            notes,
            [],
            comparables,
        )

    used_prior = False
    if n_used >= MIN_COMPARABLES:
        stats = describe(realized)
        median, p25, p75, p90, max_r = stats.median, stats.p25, stats.p75, stats.p90, stats.max_reasonable
    else:
        stats = None
        median = p25 = p75 = p90 = max_r = 0.0

    if prior and prior.sample_size >= 5 and n_used < 10:
        # Shrink towards the segment prior (condition-adjusted) when direct evidence is thin.
        cond_mult = CONDITION_MULTIPLIER.get(subject_condition, 0.95)
        pm, p25p, p75p = (
            prior.median_price * cond_mult,
            prior.p25_price * cond_mult,
            prior.p75_price * cond_mult,
        )
        k = 3.0
        w_direct = n_used / (n_used + k)
        if stats is None:
            median, p25, p75, p90, max_r = pm, p25p, p75p, p75p * 1.1, p75p * 1.2
            notes.append("Stima basata sulle statistiche del segmento (pochi comparabili diretti).")
        else:
            median = w_direct * median + (1 - w_direct) * pm
            p25 = w_direct * p25 + (1 - w_direct) * p25p
            p75 = w_direct * p75 + (1 - w_direct) * p75p
        used_prior = True

    quick = min(p25, median * 0.93)
    optimistic = max(p75, median * 1.07)
    optimistic = min(optimistic, max(p90, median * 1.07), max(max_r, median * 1.07))

    # ---- confidence -------------------------------------------------------------------------
    sample_f = 1 - math.exp(-n_used / 10)
    sold_f = min(1.0, len(sold_in) / 8)
    sim_f = min(1.0, max(0.0, (avg_sim - 0.4) / 0.6)) if used else 0.0
    dispersion = stats.dispersion if stats else 0.6
    disp_f = 1 / (1 + 2 * dispersion)
    recent = [c for c in used if (obs := c.item.observed_at()) and (now - obs).days <= 45]
    recency_f = 0.6 + 0.4 * (len(recent) / n_used) if used else 0.6
    breakdown = {
        "sample": sample_f,
        "sold": sold_f,
        "similarity": sim_f,
        "dispersion": disp_f,
        "recency": recency_f,
    }
    confidence = 100 * (0.35 * sample_f + 0.20 * sold_f + 0.20 * sim_f + 0.15 * disp_f + 0.10 * recency_f)
    if stats is None:
        confidence = min(confidence, 30)
    if n_used < 5:
        notes.append(f"Solo {n_used} comparabili utilizzabili: stima indicativa.")
    if dispersion > 0.6:
        notes.append("Prezzi di mercato molto dispersi: la stima è meno affidabile.")
    if not sold_in:
        notes.append("Nessuna vendita reale osservata: stima basata su prezzi richiesti scontati.")
    if n_outliers:
        notes.append(f"{n_outliers} prezzi anomali esclusi dal calcolo.")

    return MarketEstimate(
        fair_market_value=_euros(median),
        quick_sale_price=_euros(quick, "floor"),
        expected_sale_price=_euros(median),
        optimistic_sale_price=_euros(optimistic),
        stats=stats,
        sold_stats=sold_stats,
        active_stats=active_stats,
        n_used=n_used,
        n_sold=len(sold_in),
        n_active=len(active_in),
        n_outliers=n_outliers,
        avg_similarity=avg_sim,
        ask_to_sale_ratio=k_ask,
        confidence=round(confidence),
        used_prior=used_prior,
        notes=notes,
        histogram=histogram([c.adjusted_price for c in used]),
        comparables=comparables,
        confidence_breakdown=breakdown,
    )
