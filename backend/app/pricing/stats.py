"""Robust weighted statistics for market prices.

* Weighted percentiles (linear interpolation on the weighted CDF midpoints).
* Outlier detection on log-prices combining Tukey's IQR fences and the modified z-score
  (median absolute deviation). Log space is used because prices are multiplicative: a 150 EUR
  ask among 38-49 EUR comparables is an outlier, while 30 vs 60 EUR is a plausible spread.
* Small samples (n < 4) fall back to a ratio filter around the median.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class WeightedValue:
    value: float
    weight: float = 1.0


def weighted_percentile(items: Sequence[WeightedValue], q: float) -> float:
    """q in [0, 1]. Uses midpoint cumulative weights so that equal weights reproduce the usual median."""
    if not items:
        raise ValueError("empty sample")
    data = sorted((i for i in items if i.weight > 0), key=lambda i: i.value)
    if not data:
        raise ValueError("all weights are zero")
    if len(data) == 1:
        return data[0].value
    total = sum(i.weight for i in data)
    positions: list[float] = []
    cum = 0.0
    for item in data:
        positions.append((cum + item.weight / 2) / total)
        cum += item.weight
    if q <= positions[0]:
        return data[0].value
    if q >= positions[-1]:
        return data[-1].value
    for idx in range(1, len(data)):
        if q <= positions[idx]:
            lo, hi = positions[idx - 1], positions[idx]
            frac = (q - lo) / (hi - lo) if hi > lo else 0.0
            return data[idx - 1].value + frac * (data[idx].value - data[idx - 1].value)
    return data[-1].value


def weighted_mean(items: Sequence[WeightedValue]) -> float:
    total = sum(i.weight for i in items)
    if total <= 0:
        raise ValueError("all weights are zero")
    return sum(i.value * i.weight for i in items) / total


def median(values: Sequence[float]) -> float:
    return weighted_percentile([WeightedValue(v) for v in values], 0.5)


@dataclass(frozen=True)
class OutlierResult:
    kept: list[int]  # indices of inliers
    removed: list[int]  # indices of outliers
    method: str
    lower_bound: float | None
    upper_bound: float | None


def detect_outliers(values: Sequence[float], iqr_k: float = 1.5, z_threshold: float = 3.5) -> OutlierResult:
    """Return indices of inliers/outliers. Non-positive prices are always outliers."""
    idx_pos = [i for i, v in enumerate(values) if v > 0]
    removed = [i for i, v in enumerate(values) if v <= 0]
    n = len(idx_pos)
    if n == 0:
        return OutlierResult([], removed, "none", None, None)
    logs = {i: math.log(values[i]) for i in idx_pos}

    if n < 4:
        med = median([values[i] for i in idx_pos])
        lo, hi = med / 3, med * 3
        kept = [i for i in idx_pos if lo <= values[i] <= hi]
        removed += [i for i in idx_pos if i not in kept]
        return OutlierResult(kept, sorted(removed), "ratio", lo, hi)

    log_values = [logs[i] for i in idx_pos]
    q1 = weighted_percentile([WeightedValue(v) for v in log_values], 0.25)
    q3 = weighted_percentile([WeightedValue(v) for v in log_values], 0.75)
    iqr = q3 - q1
    # Floor the IQR so very tight clusters (all 40 EUR) don't flag a 44 EUR item as outlier.
    iqr = max(iqr, 0.08)
    lo_fence, hi_fence = q1 - iqr_k * iqr, q3 + iqr_k * iqr

    med = median(log_values)
    mad = median([abs(v - med) for v in log_values])
    mad = max(mad, 0.04)

    kept: list[int] = []
    for i in idx_pos:
        v = logs[i]
        z = 0.6745 * (v - med) / mad
        if lo_fence <= v <= hi_fence and abs(z) <= z_threshold:
            kept.append(i)
        else:
            removed.append(i)
    return OutlierResult(kept, sorted(removed), "iqr+mad", math.exp(lo_fence), math.exp(hi_fence))


@dataclass(frozen=True)
class DistributionStats:
    n: int
    median: float
    mean: float
    p10: float
    p25: float
    p75: float
    p90: float
    min_reasonable: float
    max_reasonable: float
    dispersion: float  # IQR / median (0 = perfectly consistent market)


def describe(items: Sequence[WeightedValue]) -> DistributionStats:
    if not items:
        raise ValueError("empty sample")
    med = weighted_percentile(items, 0.5)
    p25 = weighted_percentile(items, 0.25)
    p75 = weighted_percentile(items, 0.75)
    return DistributionStats(
        n=len(items),
        median=med,
        mean=weighted_mean(items),
        p10=weighted_percentile(items, 0.10),
        p25=p25,
        p75=p75,
        p90=weighted_percentile(items, 0.90),
        min_reasonable=min(i.value for i in items),
        max_reasonable=max(i.value for i in items),
        dispersion=(p75 - p25) / med if med > 0 else 0.0,
    )


def histogram(values: Sequence[float], bins: int = 12) -> list[dict[str, float]]:
    """Equal-width histogram for charts: [{start, end, count}]."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if math.isclose(lo, hi):
        return [{"start": round(lo, 2), "end": round(hi, 2), "count": len(values)}]
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        b = min(bins - 1, int((v - lo) / width))
        counts[b] += 1
    return [
        {"start": round(lo + b * width, 2), "end": round(lo + (b + 1) * width, 2), "count": counts[b]}
        for b in range(bins)
    ]
