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
    # What the photo analysis could see of the item (None: not evaluated). Thin coverage or poor files lower the
    # confidence of everything that rests on «no visible defect».
    inspection_coverage: int | None = None
    photo_quality: int | None = None


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
    if inp.inspection_coverage is not None:
        quality = inp.photo_quality if inp.photo_quality is not None else 60
        seen = 0.7 * inp.inspection_coverage / 100 + 0.3 * quality / 100
        components["photo_view"] = seen
        # An unseen part is a risk nobody priced: the confidence follows what was seen.
        score *= 0.6 + 0.4 * seen
    return ConfidenceResult(max(0, min(100, round(score))), {k: round(v, 3) for k, v in components.items()})
