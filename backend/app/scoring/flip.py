"""Flip Score (0-100): how attractive a listing is to buy and resell.

Eight components with the weights of the product brief (configurable, renormalised to 100%):

    profit 25 · ROI 15 · demand and liquidity 15 · price against the market 15 ·
    condition 10 · risk 10 · quality of the information 5 · time to sell 5

Double counting is avoided on purpose:

* profit, ROI and the discount all come from the same pair (price, value). They are scored as one
  *economic pillar* that mixes their weighted mean with their weakest member, so a lopsided
  metric (a 90% discount on a 3 EUR item) cannot carry the score alone;
* what used to be separate penalties (condition, risk, thin data) is now one component each, and
  non-compensable cases (fake risk, thin evidence) are vetoes of the decision layer, not points;
* the seller and the freshness of the listing feed the risk score and the alert rules, not this one.

The score never decides alone: confidence, risk and data completeness are separate scores
(see ``app.decision``). Every component is returned so the score is fully explainable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.domain.enums import Condition, DealTier
from app.scoring.curves import PROFIT_FULL, ROI_FULL, UNDERVALUATION_FULL, concave

DEFAULT_WEIGHTS: dict[str, float] = {
    "profit": 25,
    "roi": 15,
    "demand": 15,
    "price_vs_market": 15,
    "condition": 10,
    "risk": 10,
    "info": 5,
    "sale_time": 5,
}
COMPONENT_LABELS = {
    "profit": "Profitto netto atteso",
    "roi": "ROI atteso",
    "demand": "Domanda e liquidità",
    "price_vs_market": "Prezzo rispetto al mercato",
    "condition": "Condizioni",
    "risk": "Rischio",
    "info": "Qualità delle informazioni",
    "sale_time": "Tempo di vendita",
}
# Weights saved before the brief's weights existed keep their meaning where one exists.
LEGACY_ALIASES = {"undervaluation": "price_vs_market", "velocity": "sale_time"}
# The components fed by the same (price, value) pair: scored together as one pillar.
ECONOMIC_PILLAR = ("profit", "roi", "price_vs_market")
PILLAR_MEAN_SHARE = 0.6  # the rest is the weakest member of the pillar

CONDITION_SCORES: dict[str, float] = {
    Condition.NEW_WITH_TAGS: 100,
    Condition.NEW_WITHOUT_TAGS: 92,
    Condition.VERY_GOOD: 80,
    Condition.GOOD: 60,
    Condition.SATISFACTORY: 35,
    Condition.UNKNOWN: 40,
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
    risk_score: int
    condition: str
    info_score: int  # data completeness of the listing, 0-100


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


def price_vs_market_score(discount: float | None) -> float:
    return concave(discount or 0.0, UNDERVALUATION_FULL)


def roi_score(roi: float | None) -> float:
    return concave(roi or 0.0, ROI_FULL)


def profit_score(profit: float | None) -> float:
    return concave(profit or 0.0, PROFIT_FULL)


def condition_score(condition: str) -> float:
    return CONDITION_SCORES.get(condition, CONDITION_SCORES[Condition.UNKNOWN])


def normalize_weights(weights: dict[str, float] | None) -> dict[str, float]:
    saved: dict[str, float] = {}
    for key, value in (weights or {}).items():
        key = LEGACY_ALIASES.get(key, key)
        if key in DEFAULT_WEIGHTS:
            saved[key] = float(value)
    merged = {k: max(0.0, v) for k, v in {**DEFAULT_WEIGHTS, **saved}.items()}
    total = sum(merged.values())
    if total <= 0:
        merged, total = dict(DEFAULT_WEIGHTS), float(sum(DEFAULT_WEIGHTS.values()))
    return {k: v / total for k, v in merged.items()}


def compute_flip_score(inp: FlipInput, weights: dict[str, float] | None = None) -> FlipResult:
    w = normalize_weights(weights)
    raw = {
        "profit": profit_score(inp.expected_profit),
        "roi": roi_score(inp.expected_roi),
        "price_vs_market": price_vs_market_score(inp.discount_vs_market),
        "demand": float(inp.demand_score),
        "condition": condition_score(inp.condition),
        "risk": float(100 - max(0, min(100, inp.risk_score))),
        "info": float(max(0, min(100, inp.info_score))),
        "sale_time": float(inp.velocity_score),
    }

    # The economic pillar: weighted mean mixed with the weakest member (see module docstring).
    pillar_weight = sum(w[k] for k in ECONOMIC_PILLAR)
    weighted = {k: w[k] * raw[k] for k in ECONOMIC_PILLAR}
    if pillar_weight > 0:
        mean = sum(weighted.values()) / pillar_weight
        weakest = min(raw[k] for k in ECONOMIC_PILLAR if w[k] > 0)
        pillar_points = pillar_weight * (PILLAR_MEAN_SHARE * mean + (1 - PILLAR_MEAN_SHARE) * weakest)
    else:
        pillar_points = 0.0
    weighted_total = sum(weighted.values())

    components: dict[str, dict[str, float]] = {}
    for k, v in raw.items():
        if k in ECONOMIC_PILLAR:
            share = weighted[k] / weighted_total if weighted_total > 0 else 0.0
            contribution = pillar_points * share
        else:
            contribution = v * w[k]
        components[k] = {
            "score": round(v, 1),
            "weight": round(w[k], 4),
            "contribution": round(contribution, 2),
        }
    base = pillar_points + sum(raw[k] * w[k] for k in raw if k not in ECONOMIC_PILLAR)

    score = base
    cap: dict[str, Any] | None = None
    if inp.discount_vs_market is None:
        cap = {"max": 35, "reason": "Valore di mercato non stimabile"}
    elif inp.expected_profit is not None and inp.expected_profit <= 0:
        cap = {"max": 30, "reason": "Profitto atteso nullo o negativo"}
    if cap:
        score = min(score, cap["max"])
    return FlipResult(max(0, min(100, round(score))), round(base, 2), components, [], cap)


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
