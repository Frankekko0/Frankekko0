"""Market comparison summary: what the estimate is based on, in plain numbers.

Comparables are listings of the same brand (and category family) ranked by similarity on model,
size, condition, title, gender, colour, material and country. Prices are reported as they were
used: brought to the condition of the analysed item (a "very good" comparable of an item in
"good" condition counts a little less), so they compare like for like.
"""

from __future__ import annotations

import math
from typing import Any

from app.ingestion.normalizer import size_distance
from app.pricing.comparables import ItemProfile, ScoredComparable
from app.pricing.market_value import MarketEstimate


def quantile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation between closest ranks (same as numpy's default)."""
    if not sorted_values:
        raise ValueError("empty")
    pos = (len(sorted_values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def price_stats(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    v = sorted(values)
    return {
        "n": len(v),
        "min": round(v[0], 2),
        "p25": round(quantile(v, 0.25), 2),
        "median": round(quantile(v, 0.5), 2),
        "p75": round(quantile(v, 0.75), 2),
        "max": round(v[-1], 2),
    }


def market_comparison(
    subject: ItemProfile, similar: list[ScoredComparable], market: MarketEstimate
) -> dict[str, Any]:
    """``similar``: every similar listing found (sold, on sale, removed); ``market``: the estimate,
    whose ``comparables`` carry the inclusion flags (outliers excluded)."""
    used = [c for c in market.comparables if c.included]
    sold = [c for c in used if c.is_sold]
    active = [c for c in used if not c.is_sold]
    same_size = (
        sum(1 for c in used if size_distance(subject.size, c.item.size) == 0) if subject.size else None
    )
    return {
        "found": len(similar),
        "found_sold": sum(1 for c in similar if c.item.status == "sold"),
        "found_active": sum(1 for c in similar if c.item.status == "active"),
        "found_removed": sum(1 for c in similar if c.item.status == "removed"),
        "used": len(used),
        "used_sold": len(sold),
        "used_active": len(active),
        "outliers_excluded": market.n_outliers,
        "prices": price_stats([c.adjusted_price for c in used]),
        "sold_prices": price_stats([c.adjusted_price for c in sold]),
        "active_prices": price_stats([c.adjusted_price for c in active]),
        "matches": {
            "same_model": sum(1 for c in used if subject.model and c.item.model == subject.model)
            if subject.model
            else None,
            "same_size": same_size,
            "same_condition": sum(1 for c in used if c.item.condition == subject.condition),
        },
        "avg_similarity": round(sum(c.similarity for c in used) / len(used), 3) if used else None,
        "prices_adjusted_to_condition": True,
        "used_segment_prior": market.used_prior,
    }
