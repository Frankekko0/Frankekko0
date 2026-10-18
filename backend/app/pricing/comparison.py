"""Market comparison summary: what the estimate is based on, in plain numbers.

Comparables are listings of the same brand (and category family) ranked by similarity on model,
size, condition, title, gender, colour, material and country, in tiers: the price comes from the
subject's own model or line (``tier``), the rest of the brand is a reference only. Prices are reported
as they were used: brought to the condition of the analysed item (a "very good" comparable of an item
in "good" condition counts a little less), so they compare like for like.
"""

from __future__ import annotations

import math
from typing import Any

from app.ingestion.normalizer import size_distance
from app.domain.enums import CONDITION_ORDER, Condition
from app.pricing.comparables import TIER_FAMILY, TIER_LINE, TIER_MODEL, ItemProfile, ScoredComparable, tier_of
from app.pricing.market_value import (
    REASON_ASKING,
    REASON_OTHER_LEVEL,
    REASON_OUTLIER,
    REASON_STALE_ASK,
    ACTIVE_FRESH_DAYS,
    MarketEstimate,
)


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
        # Which comparables carry the price ("model" | "line" | "family" = brand and category only), how many
        # of the subject's own model/line were used, and the price-level / trend warnings.
        "tier": market.tier,
        "specific_used": market.n_specific,
        "mixed_levels": market.mixed,
        "trend": market.trend,
        "median_age_days": round(market.median_age_days, 1) if market.median_age_days is not None else None,
        "reference": market.reference,
    }


# Why a comparable is not part of the price, in words (the codes are ``ScoredComparable.exclusion_reason``).
EXCLUSION_LABELS: dict[str, str] = {
    REASON_OUTLIER: "prezzo anomalo rispetto agli altri",
    REASON_ASKING: "prezzo richiesto: con abbastanza vendite resta solo di riferimento",
    "other_line": "altra linea o modello: solo di riferimento",
    REASON_OTHER_LEVEL: "su un altro livello di prezzo (stagione, versione o mercato cambiato)",
    REASON_STALE_ASK: f"annuncio non rivisto da oltre {ACTIVE_FRESH_DAYS} giorni: potrebbe non esistere più",
}


def exclusion_label(reason: str | None) -> str | None:
    return EXCLUSION_LABELS.get(reason or "", None) if reason else None


def explain_comparable(subject: ItemProfile, comp: ItemProfile) -> dict[str, Any]:
    """What a comparable has in common with the analysed item, in short Italian labels (nothing stored:
    computed from the two listings when the analysis is shown)."""
    tier = tier_of(subject, comp)
    labels = [
        {TIER_MODEL: "stesso modello", TIER_LINE: "stessa linea", TIER_FAMILY: "altra linea o modello"}[tier]
    ]
    dist = size_distance(subject.size, comp.size) if subject.size and comp.size else None
    if dist == 0:
        labels.append("stessa taglia")
    elif dist == 1:
        labels.append("taglia vicina")
    elif dist is not None:
        labels.append("taglia diversa")
    ra = _rank(subject.condition)
    rb = _rank(comp.condition)
    if subject.condition == comp.condition:
        labels.append("stessa condizione")
    elif ra is not None and rb is not None:
        step = rb - ra
        labels.append(f"condizione {'migliore' if step > 0 else 'peggiore'} di {abs(step)}")
    if subject.category and comp.category == subject.category:
        labels.append("stessa categoria")
    elif subject.parent_category and comp.parent_category == subject.parent_category:
        labels.append("categoria affine")
    return {"tier": tier, "match": labels}


def _rank(value: str) -> int | None:
    try:
        return CONDITION_ORDER.get(Condition(value))
    except ValueError:
        return None
