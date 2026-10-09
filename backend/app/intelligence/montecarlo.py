"""Profit as a distribution, not a number (Monte Carlo, seeded so results can be replayed).

Simulated: the resale price (triangular between the fast, fair and optimistic estimates), the time to
sell (exponential, from the survival model; an item still unsold at the horizon is liquidated at a
discount), and three things that go wrong: the item is not authentic (the purchase is mostly lost), it
comes back (shipping lost, the item stays in stock) and a hidden defect forces a price cut.

Decisions should use the expected value, the unfavourable scenario (P10) and the probability of a loss,
together. ``returns`` (profit per euro invested) feeds the Kelly sizing.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MCInputs:
    cost: float  # total acquisition cost, > 0
    resale_low: float  # fast sale
    resale_mid: float  # fair
    resale_high: float  # optimistic
    net_of_price: Callable[[float], float]  # revenue after selling costs for a sale price
    p_fake: float = 0.0
    p_return: float = 0.0
    p_defect: float = 0.0
    defect_cut: float = 0.25  # share of the price lost to a hidden defect
    return_cost: float = 0.0  # shipping lost when an item comes back
    hazard: float | None = None  # sale rate per day; None: time is not modelled
    horizon_days: float = 60.0
    liquidation_factor: float = 0.65  # an item unsold at the horizon sells at this share of the price
    fake_recovery: float = 0.0  # share of the cost recovered when the item is not authentic


@dataclass
class ProfitDistribution:
    n: int
    mean: float
    p10: float
    p50: float
    p90: float
    p_loss: float
    expected_days: float | None
    p_unsold: float
    returns: list[float] = field(default_factory=list, repr=False)  # profit / cost per sample
    profits: list[float] = field(default_factory=list, repr=False)

    def as_dict(self) -> dict[str, Any]:
        r = lambda v: None if v is None else round(v, 2)  # noqa: E731
        return {
            "n": self.n,
            "mean": r(self.mean),
            "p10": r(self.p10),
            "p50": r(self.p50),
            "p90": r(self.p90),
            "p_loss": round(self.p_loss, 3),
            "expected_days": r(self.expected_days),
            "p_unsold_at_horizon": round(self.p_unsold, 3),
        }


def _pct(sorted_xs: list[float], q: float) -> float:
    i = min(len(sorted_xs) - 1, max(0, int(q * (len(sorted_xs) - 1) + 0.5)))
    return sorted_xs[i]


def simulate(inp: MCInputs, n: int = 4000, seed: int = 7) -> ProfitDistribution:
    if inp.cost <= 0:
        raise ValueError("il costo d'acquisto deve essere positivo")
    rng = random.Random(seed)
    low = min(inp.resale_low, inp.resale_mid)
    high = max(inp.resale_high, inp.resale_mid)
    profits: list[float] = []
    days: list[float] = []
    unsold = 0
    for _ in range(n):
        u = rng.random()
        if u < inp.p_fake:
            profits.append(-inp.cost * (1 - inp.fake_recovery))
            continue
        if u < inp.p_fake + inp.p_return:
            profits.append(-inp.return_cost)
            continue
        price = rng.triangular(low, high, inp.resale_mid) if high > low else inp.resale_mid
        if rng.random() < inp.p_defect:
            price *= 1 - inp.defect_cut
        if inp.hazard and inp.hazard > 0:
            t = rng.expovariate(inp.hazard)
            if t > inp.horizon_days:
                unsold += 1
                price *= inp.liquidation_factor
                t = inp.horizon_days
            days.append(t)
        profits.append(inp.net_of_price(price) - inp.cost)
    srt = sorted(profits)
    return ProfitDistribution(
        n=n,
        mean=sum(profits) / n,
        p10=_pct(srt, 0.10),
        p50=_pct(srt, 0.50),
        p90=_pct(srt, 0.90),
        p_loss=sum(1 for p in profits if p < 0) / n,
        expected_days=(sum(days) / len(days)) if days else None,
        p_unsold=unsold / n,
        returns=[p / inp.cost for p in profits],
        profits=profits,
    )
