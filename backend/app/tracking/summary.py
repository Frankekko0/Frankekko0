"""Readable analysis summary shown on the tracking page (and to the browser extension)."""

from __future__ import annotations

from typing import Any

from app.db.models import Listing, Opportunity


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def analysis_summary(o: Opportunity, listing: Listing) -> dict[str, Any]:
    snap = o.market_snapshot or {}
    breakdown = o.score_breakdown or {}
    insufficient = o.data_quality == "insufficient"
    return {
        "opportunity_id": str(o.id),
        "analyzed_at": o.analyzed_at.isoformat(),
        "algorithm_version": o.algorithm_version,
        "acquisition_mode": o.acquisition_mode,
        "analysis_depth": o.analysis_depth,
        "data_quality": o.data_quality,
        "insufficient_reason": o.insufficient_reason,
        "is_active": o.is_active,
        # The score is withheld when the data cannot support it.
        "flip_score": None if insufficient else o.flip_score,
        "confidence_score": o.confidence_score,
        "risk_score": o.risk_score,
        "risk_level": o.risk_level,
        "verdict": None if insufficient else o.verdict,
        # The decision engine's verdict (the one place a verdict is made); None before it existed.
        "decision_verdict": o.decision_verdict,
        "recommended_action": o.recommended_action,
        "market": {
            "comparables_found": o.comparables_count,
            "comparables_sold": o.sold_comparables_count,
            "fair_market_value": _f(o.fair_market_value),
            "min": _f(o.market_min),
            "p25": _f(o.market_p25),
            "median": _f(o.market_median),
            "p75": _f(o.market_p75),
            "notes": snap.get("notes") or [],
            **(breakdown.get("market_comparison") or {}),
        },
        "velocity": {
            "estimated_days": _f(o.estimated_days_to_sell),
            "sell_through_rate": _f(o.sell_through_rate),
            **(breakdown.get("velocity_detail") or {}),
        },
        "economics": {
            "listing_price": _f(o.listing_price),
            "total_acquisition_cost": _f(o.total_acquisition_cost),
            "resale_low": _f(o.quick_sale_price),
            "resale_expected": _f(o.expected_sale_price),
            "resale_high": _f(o.optimistic_sale_price),
            "expected_net_revenue": _f(o.expected_net_revenue),
            "net_margin": _f(o.expected_profit),
            "roi": _f(o.expected_roi),
            "net_margin_low": _f(o.conservative_profit),
            "net_margin_high": _f(o.optimistic_profit),
            "max_buy_price": _f(o.max_buy_price),
        },
        "risk_signals": breakdown.get("risk_signals") or [],
        "risk_factors": o.risk_factors or [],
        "headline": o.headline,
        "reasons": o.explanation or [],
        "listing_status": listing.status,
    }
