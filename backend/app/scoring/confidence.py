"""Deal Confidence Score (0-100): how sure the system is about its own analysis.

Separate from the Flip Score on purpose: "Flip 94 / Confidence 42" means "possibly a huge deal,
but the data is thin", which is different from "Flip 87 / Confidence 94".
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceInput:
    market_confidence: int
    identification_confidence: int
    photo_count: int
    description_length: int
    size_known: bool
    condition_known: bool
    seller_known: bool
    demand_observations: int


@dataclass(frozen=True)
class ConfidenceResult:
    score: int
    components: dict[str, float]


def compute_confidence(inp: ConfidenceInput) -> ConfidenceResult:
    completeness = (
        0.35 * min(1.0, inp.photo_count / 4)
        + 0.20 * min(1.0, inp.description_length / 120)
        + 0.15 * (1.0 if inp.size_known else 0.0)
        + 0.15 * (1.0 if inp.condition_known else 0.0)
        + 0.15 * (1.0 if inp.seller_known else 0.0)
    )
    demand_depth = 1 - math.exp(-inp.demand_observations / 15)
    components = {
        "market": inp.market_confidence / 100,
        "identification": inp.identification_confidence / 100,
        "completeness": completeness,
        "demand_data": demand_depth,
    }
    score = 100 * (
        0.45 * components["market"]
        + 0.25 * components["identification"]
        + 0.15 * components["completeness"]
        + 0.15 * components["demand_data"]
    )
    return ConfidenceResult(max(0, min(100, round(score))), {k: round(v, 3) for k, v in components.items()})
