"""The permanent record of one analysis: five blocks, the inputs it read and why it was run (pure).

``opportunities`` keeps the *current* analysis of a listing for fast queries; every analysis that
produced a different result is also stored, unchanged, in ``analyses`` so that a past decision can
be reproduced and explained (what the listing looked like, what the market was, what was decided).

Blocks, each with its own version:

* ``product``  - what the item is: the listing's fields and the typed identification facts;
* ``visual``   - what the photos show (empty until a photo analysis exists, never invented);
* ``economic`` - costs, scenarios, profit and ROI, the prices to pay;
* ``market``   - the value estimate, its range and where it comes from, the comparables;
* ``decision`` - scores, verdict, reasons, risks, authenticity.

Re-running an analysis with the same inputs, the same algorithm version and the same results does
not add a row: the existing one is still the current analysis.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

SCHEMA_VERSION = 1
BLOCK_VERSION = 1
TRIGGERS = (
    "new",
    "price_change",
    "photos",
    "status_change",
    "data_changed",
    "recompute",
    "manual",
    "migrated",
)

# The scalar results that define "the same analysis": none of them depends on the clock.
RESULT_FIELDS = (
    "data_quality",
    "insufficient_reason",
    "headline",
    "fair_market_value",
    "market_median",
    "market_p25",
    "market_p75",
    "market_min",
    "market_max",
    "quick_sale_price",
    "expected_sale_price",
    "optimistic_sale_price",
    "discount_vs_market",
    "total_acquisition_cost",
    "expected_net_revenue",
    "expected_profit",
    "expected_roi",
    "conservative_profit",
    "optimistic_profit",
    "max_buy_price",
    "good_buy_price",
    "suggested_offer",
    "demand_score",
    "velocity_score",
    "flip_score",
    "confidence_score",
    "risk_score",
    "risk_level",
    "seller_score",
    "deal_tier",
    "is_ultra_deal",
    "verdict",
    "recommended_action",
    "comparables_count",
    "sold_comparables_count",
    "identification_confidence",
    "authenticity_probability",
    "authenticity_verdict",
)


def jsonable(value: Any) -> Any:
    """JSON-safe copy: money and ratios as exact strings, times as ISO, enums by value."""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f") if value == value.to_integral() else str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return jsonable(value.value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(v) for v in value]
    return value


def _digest(payload: Any) -> str:
    raw = json.dumps(jsonable(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def listing_inputs(listing: Any) -> dict[str, Any]:
    """What the analysis reads from the listing, small enough to compare and to store."""
    return {
        "price": listing.price,
        "currency": listing.currency,
        "status": listing.status,
        "capture_level": listing.capture_level,
        "title": hashlib.md5((listing.title or "").encode(), usedforsecurity=False).hexdigest(),
        "description": hashlib.md5((listing.description or "").encode(), usedforsecurity=False).hexdigest(),
        "photos": [i.image_key for i in listing.images],
        "shipping_fee": listing.shipping_fee,
        "buyer_protection_fee": listing.buyer_protection_fee,
        "brand_id": listing.brand_id,
        "category_id": listing.category_id,
        "model_name": listing.model_name,
        "size": listing.size_normalized,
        "condition": listing.condition,
        "seller_id": str(listing.seller_id) if listing.seller_id else None,
        "is_repost": listing.duplicate_of_id is not None,
    }


def inputs_hash(inputs: dict[str, Any]) -> str:
    return _digest(inputs)


def result_hash(values: dict[str, Any]) -> str:
    return _digest({k: values.get(k) for k in RESULT_FIELDS})


def classify_trigger(
    prev_inputs: dict[str, Any] | None, inputs: dict[str, Any], override: str | None = None
) -> str:
    """Why a new analysis exists. Most specific cause first."""
    if override:
        if override not in TRIGGERS:
            raise ValueError(f"unknown analysis trigger: {override}")
        return override
    if not prev_inputs or prev_inputs.get("migrated"):
        return "new" if prev_inputs is None else "recompute"
    cur = jsonable(inputs)
    if cur["price"] != prev_inputs.get("price") or cur["currency"] != prev_inputs.get("currency"):
        return "price_change"
    if cur["photos"] != prev_inputs.get("photos"):
        return "photos"
    if cur["status"] != prev_inputs.get("status"):
        return "status_change"
    if cur != prev_inputs:
        return "data_changed"
    return "recompute"  # same listing, different result: the market moved or the algorithm changed


def _compact_comparables(comparables: list[Any], limit: int = 50) -> dict[str, Any]:
    included = sorted((c for c in comparables if c.included), key=lambda c: c.weight, reverse=True)
    excluded: dict[str, int] = {}
    for c in comparables:
        if not c.included:
            key = c.exclusion_reason or "excluded"
            excluded[key] = excluded.get(key, 0) + 1
    return {
        "used": [
            {
                "listing_id": c.item.id,
                "price": round(c.item.price, 2) if isinstance(c.item.price, float) else c.item.price,
                "adjusted_price": round(c.adjusted_price, 2),
                "weight": round(c.weight, 4),
                "similarity": round(c.similarity, 4),
                "sold": c.is_sold,
                "source": getattr(c.item, "source", None),
            }
            for c in included[:limit]
        ],
        "used_total": len(included),
        "excluded_by_reason": excluded,
    }


def build_blocks(listing: Any, result: Any, values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The five blocks of an analysis, from the listing, the result and the opportunity values."""
    ident = dict(listing.identification or {})
    vision = ident.pop("vision", None)
    reused = ident.get("photos_reused_by_other_seller")
    v = values

    def pick(*keys: str) -> dict[str, Any]:
        return {k: v.get(k) for k in keys}

    product = {
        "v": BLOCK_VERSION,
        "title": listing.title,
        "brand": listing.brand_raw,
        "brand_id": listing.brand_id,
        "category": listing.category_raw,
        "category_id": listing.category_id,
        "model": listing.model_name,
        "gender": listing.gender,
        "size": {"raw": listing.size_raw, "normalized": listing.size_normalized},
        "condition": {"raw": listing.condition_raw, "normalized": listing.condition},
        "color": listing.color,
        "material": listing.material,
        "is_vintage": listing.is_vintage,
        "product_code": listing.product_code,
        "price": listing.price,
        "currency": listing.currency,
        "shipping_fee": listing.shipping_fee,
        "buyer_protection_fee": listing.buyer_protection_fee,
        "identification_confidence": listing.identification_confidence,
        "identification": ident,
        "capture_level": listing.capture_level,
    }
    visual = (
        {"v": BLOCK_VERSION, "analysed": True, "vision": vision, "photos_reused_by_other_seller": reused}
        if vision
        else {"v": BLOCK_VERSION, "analysed": False, "photos_reused_by_other_seller": reused}
    ) | {"photo_keys": [i.image_key for i in listing.images], "photo_count": listing.photo_count}
    economic = {
        "v": BLOCK_VERSION,
        **pick(
            "listing_price",
            "currency",
            "total_acquisition_cost",
            "expected_net_revenue",
            "max_buy_price",
            "good_buy_price",
            "suggested_offer",
            "risk_adjusted_profit",
        ),
        "evaluation": (v.get("score_breakdown") or {}).get("economics"),
        "scenarios": {
            name: {"profit": v.get(f"{name_key}_profit"), "roi": v.get(f"{name_key}_roi")}
            for name, name_key in (
                ("conservative", "conservative"),
                ("expected", "expected"),
                ("optimistic", "optimistic"),
            )
        },
    }
    market = {
        "v": BLOCK_VERSION,
        **pick(
            "data_quality",
            "insufficient_reason",
            "fair_market_value",
            "market_median",
            "market_mean",
            "market_p25",
            "market_p75",
            "market_min",
            "market_max",
            "quick_sale_price",
            "expected_sale_price",
            "optimistic_sale_price",
            "discount_vs_market",
            "comparables_count",
            "sold_comparables_count",
            "demand_level",
            "demand_score",
            "sell_through_rate",
            "velocity_score",
            "estimated_days_to_sell",
        ),
        "snapshot": v.get("market_snapshot"),
        "provenance": (v.get("score_breakdown") or {}).get("provenance"),
        "comparables": _compact_comparables(result.comparables),
    }
    breakdown = v.get("score_breakdown") or {}
    decision = {
        "v": BLOCK_VERSION,
        **pick(
            "flip_score",
            "confidence_score",
            "risk_score",
            "risk_level",
            "seller_score",
            "deal_tier",
            "is_ultra_deal",
            "verdict",
            "recommended_action",
            "headline",
            "sale_probability",
            "authenticity_probability",
            "authenticity_verdict",
        ),
        "explanation": v.get("explanation"),
        "risk_factors": v.get("risk_factors"),
        "components": breakdown.get("components"),
        "penalties": breakdown.get("penalties"),
        "cap": breakdown.get("cap"),
        "base": breakdown.get("base"),
        "confidence_components": breakdown.get("confidence_components"),
    }
    return {
        "product": jsonable(product),
        "visual": jsonable(visual),
        "economic": jsonable(economic),
        "market": jsonable(market),
        "decision": jsonable(decision),
    }
