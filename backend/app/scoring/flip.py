"""Flip Score (0-100): how attractive a listing is to buy and resell.

Weighted sum of normalized components (configurable weights, renormalized to 100%) minus
explicit penalties. Saturating curves keep a single extreme metric from dominating.
Every component and penalty is returned so the score is fully explainable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.domain.enums import Condition, DealTier
from app.scoring.curves import PROFIT_FULL, ROI_FULL, UNDERVALUATION_FULL, concave

DEFAULT_WEIGHTS: dict[str, float] = {
    "undervaluation": 30,
    "roi": 20,
    "profit": 15,
    "demand": 15,
    "velocity": 10,
    "freshness": 5,
    "seller": 5,
}
COMPONENT_LABELS = {
    "undervaluation": "Sottovalutazione prezzo",
    "roi": "ROI atteso",
    "profit": "Profitto netto atteso",
    "demand": "Domanda",
    "velocity": "Velocità di vendita",
    "freshness": "Freschezza annuncio",
    "seller": "Affidabilità venditore",
}

ULTRA_MIN_FLIP = 90
ULTRA_MIN_CONFIDENCE = 80
ULTRA_MIN_ROI = Decimal("0.60")


@dataclass(frozen=True)
class FlipInput:
    discount_vs_market: float | None  # (FMV - price) / FMV
    expected_roi: float | None  # ratio
    expected_profit: float | None  # EUR
    demand_score: int
    velocity_score: int
    listing_age_hours: float | None
    seller_score: int
    sell_through_rate: float
    comparables_used: int
    market_dispersion: float | None
    market_confidence: int
    identification_confidence: int
    condition: str
    suspicious_terms: bool
    brand_counterfeit_risk: float
    risk_score: int
    photos_reused: bool = False


@dataclass
class FlipResult:
    score: int
    base: float
    components: dict[str, dict[str, float]]
    penalties: list[dict[str, Any]] = field(default_factory=list)
    cap: dict[str, Any] | None = None

    @property
    def tier(self) -> DealTier:
        return deal_tier(self.score)


def undervaluation_score(discount: float | None) -> float:
    return concave(discount or 0.0, UNDERVALUATION_FULL)


def roi_score(roi: float | None) -> float:
    return concave(roi or 0.0, ROI_FULL)


def profit_score(profit: float | None) -> float:
    return concave(profit or 0.0, PROFIT_FULL)


def freshness_score(age_hours: float | None) -> float:
    if age_hours is None:
        return 40.0
    return 100 * math.exp(-max(0.0, age_hours) / 24)


def normalize_weights(weights: dict[str, float] | None) -> dict[str, float]:
    merged = {**DEFAULT_WEIGHTS, **{k: float(v) for k, v in (weights or {}).items() if k in DEFAULT_WEIGHTS}}
    merged = {k: max(0.0, v) for k, v in merged.items()}
    total = sum(merged.values())
    if total <= 0:
        merged, total = dict(DEFAULT_WEIGHTS), float(sum(DEFAULT_WEIGHTS.values()))
    return {k: v / total for k, v in merged.items()}


def compute_flip_score(inp: FlipInput, weights: dict[str, float] | None = None) -> FlipResult:
    w = normalize_weights(weights)
    raw = {
        "undervaluation": undervaluation_score(inp.discount_vs_market),
        "roi": roi_score(inp.expected_roi),
        "profit": profit_score(inp.expected_profit),
        "demand": float(inp.demand_score),
        "velocity": float(inp.velocity_score),
        "freshness": freshness_score(inp.listing_age_hours),
        "seller": float(inp.seller_score),
    }
    components = {
        k: {"score": round(v, 1), "weight": round(w[k], 4), "contribution": round(v * w[k], 2)}
        for k, v in raw.items()
    }
    base = sum(c["contribution"] for c in components.values())

    penalties: list[dict[str, Any]] = []

    def penalize(code: str, label: str, points: float) -> None:
        if points > 0:
            penalties.append({"code": code, "label": label, "points": round(points, 1)})

    discount = inp.discount_vs_market or 0.0
    fake_points, fake_label = 0.0, ""
    for points, label, applies in (
        (15, "Rischio contraffazione (descrizione sospetta)", inp.suspicious_terms),
        (12, "Rischio contraffazione (foto riutilizzate da un altro venditore)", inp.photos_reused),
        (
            10,
            "Prezzo troppo basso per un brand spesso contraffatto",
            inp.brand_counterfeit_risk >= 0.3 and discount > 0.55,
        ),
        (8, "Prezzo troppo bello per essere vero", discount > 0.65),
    ):
        if applies and points > fake_points:
            fake_points, fake_label = points, label
    penalize("fake_risk", fake_label, fake_points)
    if inp.condition == Condition.SATISFACTORY:
        penalize("poor_condition", "Condizioni discrete", 8)
    elif inp.condition == Condition.GOOD:
        penalize("used_condition", "Condizioni buone ma usate", 3)
    if inp.identification_confidence < 50:
        penalize(
            "insufficient_info",
            "Informazioni sul prodotto insufficienti",
            min(10, (50 - inp.identification_confidence) / 5),
        )
    if inp.sell_through_rate < 0.2:
        penalize("weak_demand", "Domanda debole", 8)
    elif inp.sell_through_rate < 0.3:
        penalize("soft_demand", "Domanda contenuta", 4)
    if inp.comparables_used < 5:
        penalize("few_comparables", "Comparabili insufficienti", 8)
    elif inp.comparables_used < 10:
        penalize("limited_comparables", "Dati comparabili limitati", 3)
    if inp.market_dispersion is not None and inp.market_dispersion > 0.6:
        penalize("unreliable_market", "Prezzo di mercato poco affidabile (prezzi molto dispersi)", 6)
    elif inp.market_confidence < 35:
        penalize("unreliable_market", "Prezzo di mercato poco affidabile", 5)
    if inp.risk_score >= 50:
        penalize("high_risk", "Rischio complessivo elevato", min(12.0, (inp.risk_score - 45) / 2.5))

    score = base - sum(p["points"] for p in penalties)
    cap: dict[str, Any] | None = None
    if inp.discount_vs_market is None:
        cap = {"max": 35, "reason": "Valore di mercato non stimabile"}
    elif inp.expected_profit is not None and inp.expected_profit <= 0:
        cap = {"max": 30, "reason": "Profitto atteso nullo o negativo"}
    if cap:
        score = min(score, cap["max"])
    return FlipResult(max(0, min(100, round(score))), round(base, 2), components, penalties, cap)


def deal_tier(score: int) -> DealTier:
    if score >= 90:
        return DealTier.EXCEPTIONAL
    if score >= 80:
        return DealTier.EXCELLENT
    if score >= 70:
        return DealTier.GOOD
    if score >= 60:
        return DealTier.MODERATE
    return DealTier.LOW


def is_ultra_deal(flip_score: int, confidence: int, roi: Decimal | None) -> bool:
    return (
        flip_score > ULTRA_MIN_FLIP
        and confidence > ULTRA_MIN_CONFIDENCE
        and roi is not None
        and roi > ULTRA_MIN_ROI
    )
