"""The advanced assessment of one listing, from numbers the analysis already has.

``distribution`` runs before the decision (its probability of loss and unfavourable scenario are a Strong
buy requirement); ``premortem`` and ``voi`` are attached to the decision afterwards, once the dossier has
found the contradictions. Everything is seeded by the listing so a re-analysis of the same data gives the
same numbers.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.intelligence import montecarlo, premortem, voi

SAMPLES = 1500
RETURN_RATE = 0.04  # assumption: share of sales that come back (no real data yet, see LIMITATIONS)
HIDDEN_DEFECT_BASE = 0.06


@dataclass(frozen=True)
class AssessInputs:
    seed_key: str
    cost: float
    resale_low: float
    resale_mid: float
    resale_high: float
    net_of_price: Callable[[float], float]
    p_authentic: float
    estimated_days: float | None
    return_cost: float
    photos_analysed: bool
    condition_known: bool


def hidden_defect_probability(photos_analysed: bool, condition_known: bool) -> float:
    return HIDDEN_DEFECT_BASE + (0.0 if photos_analysed else 0.06) + (0.0 if condition_known else 0.04)


def distribution(i: AssessInputs) -> montecarlo.ProfitDistribution:
    hazard = 1.0 / i.estimated_days if i.estimated_days and i.estimated_days > 0 else None
    return montecarlo.simulate(
        montecarlo.MCInputs(
            cost=i.cost,
            resale_low=i.resale_low,
            resale_mid=i.resale_mid,
            resale_high=i.resale_high,
            net_of_price=i.net_of_price,
            p_fake=max(0.0, min(1.0, 1 - i.p_authentic)),
            p_return=RETURN_RATE,
            p_defect=hidden_defect_probability(i.photos_analysed, i.condition_known),
            return_cost=i.return_cost,
            hazard=hazard,
        ),
        n=SAMPLES,
        seed=zlib.crc32(i.seed_key.encode()) & 0x7FFFFFFF,
    )


def value_of_analysis(i: AssessInputs, expected_profit: float, cost_of_analysis: float) -> voi.Voi:
    """Whether a photo analysis could change the decision: the larger value of resolving authenticity or a
    hidden defect (each treated as a risk that is either present or not)."""
    p_def = hidden_defect_probability(i.photos_analysed, i.condition_known)
    profit_without_defect = expected_profit
    profit_with_defect = i.net_of_price(i.resale_mid * 0.75) - i.cost
    a = voi.decide_analysis(1 - i.p_authentic, profit_without_defect, -i.cost, cost_of_analysis)
    d = voi.decide_analysis(p_def, profit_without_defect, profit_with_defect, cost_of_analysis)
    return a if a.resolvable >= d.resolvable else d


def intelligence_block(
    dist: montecarlo.ProfitDistribution,
    voi_result: voi.Voi,
    modes: list[premortem.FailureMode],
    seller_risk: dict[str, Any],
    premortem_required: bool,
) -> dict[str, Any]:
    return {
        "distribution": dist.as_dict(),
        "premortem": {"required": premortem_required, "modes": [m.as_dict() for m in modes]},
        "voi": voi_result.as_dict(),
        "seller_risk": seller_risk,
    }
