"""Demand (sell-through) and sales-velocity estimation.

Sell-through rate = sold / (sold + still active + removed) among comparable listings in the window.
Only confirmed sales count: listings removed without evidence of a sale count as not sold.
Rates are smoothed with a weak prior so 2 sold out of 2 is not reported as 100% demand.

Velocity combines the observed time-to-sale of comparable sold items with a category baseline
(shrinkage by sample size) and maps days to a 0-100 score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.enums import DemandLevel, VelocityBucket
from app.pricing.stats import WeightedValue, weighted_percentile
from app.scoring.curves import days_score, sell_through_score

PRIOR_STR = 0.4
PRIOR_STRENGTH = 4.0


@dataclass(frozen=True)
class DemandResult:
    sell_through_rate: float
    raw_sell_through_rate: float | None
    level: DemandLevel
    score: int
    observations: int
    favourites_signal: float


@dataclass(frozen=True)
class VelocityResult:
    estimated_days: float
    bucket: VelocityBucket
    score: int
    sample_size: int
    quick_sale_days: float
    optimistic_sale_days: float


def demand_level(str_value: float) -> DemandLevel:
    """Thresholds on the windowed sell-through (sold / (sold + still active) among comparables).

    A rolling window always contains fresh, not-yet-sold listings, so a windowed rate of ~50%
    already reflects strong demand.
    """
    if str_value >= 0.6:
        return DemandLevel.VERY_HIGH
    if str_value >= 0.45:
        return DemandLevel.HIGH
    if str_value >= 0.3:
        return DemandLevel.MEDIUM
    if str_value >= 0.15:
        return DemandLevel.LOW
    return DemandLevel.VERY_LOW


def analyze_demand(
    sold: int,
    active: int,
    removed: int = 0,
    favourites: int = 0,
    listing_age_hours: float | None = None,
    segment_avg_favourites_per_day: float | None = None,
) -> DemandResult:
    n = sold + removed + active
    raw = sold / n if n else None
    smoothed = (sold + PRIOR_STR * PRIOR_STRENGTH) / (n + PRIOR_STRENGTH)

    # Favourites on the subject listing: interest relative to the segment's usual pace.
    fav_signal = 0.0
    if listing_age_hours is not None and listing_age_hours > 0.5 and favourites > 0:
        per_day = favourites / max(listing_age_hours / 24, 0.25)
        baseline = segment_avg_favourites_per_day or 2.0
        fav_signal = max(-1.0, min(1.0, math.log2(per_day / baseline) / 2))
    score = sell_through_score(smoothed) + 8 * fav_signal
    return DemandResult(
        sell_through_rate=round(smoothed, 4),
        raw_sell_through_rate=round(raw, 4) if raw is not None else None,
        level=demand_level(smoothed),
        score=max(0, min(100, round(score))),
        observations=n,
        favourites_signal=round(fav_signal, 3),
    )


def velocity_bucket(days: float) -> VelocityBucket:
    if days <= 3:
        return VelocityBucket.D0_3
    if days <= 7:
        return VelocityBucket.D4_7
    if days <= 14:
        return VelocityBucket.D8_14
    if days <= 30:
        return VelocityBucket.D15_30
    return VelocityBucket.D30_PLUS


def velocity_score_from_days(days: float, sell_through: float) -> int:
    return max(0, min(100, round(0.8 * days_score(days) + 0.2 * sell_through_score(sell_through))))


def analyze_velocity(
    days_to_sale_samples: list[tuple[float, float]],
    sell_through: float,
    baseline_days: float,
) -> VelocityResult:
    """``days_to_sale_samples`` = [(days, weight)] from comparable sold listings."""
    samples = [WeightedValue(max(0.02, d), max(w, 1e-6)) for d, w in days_to_sale_samples if d >= 0]
    # Baseline adjusted by demand: weak demand means slower sales.
    demand_adj = math.sqrt(0.5 / max(sell_through, 0.1))
    baseline_est = baseline_days * demand_adj
    n = len(samples)
    if n:
        observed = weighted_percentile(samples, 0.5)
        k = 3.0
        days = (n * observed + k * baseline_est) / (n + k)
    else:
        days = baseline_est
    days = round(min(120.0, max(0.5, days)), 1)
    return VelocityResult(
        estimated_days=days,
        bucket=velocity_bucket(days),
        score=velocity_score_from_days(days, sell_through),
        sample_size=n,
        quick_sale_days=round(max(0.5, days * 0.5), 1),
        optimistic_sale_days=round(days * 1.8, 1),
    )
