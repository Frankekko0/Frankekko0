"""How much to put into one deal: fractional Kelly on the simulated returns.

The full-Kelly fraction maximises the expected log growth of capital, ``E[log(1 + f * r)]``, over the
simulated returns ``r`` (profit per euro invested). It is far too aggressive when the edge itself is
an estimate, so the fraction actually used is a fraction of it (default a quarter), scaled by the
confidence of the analysis and capped (default 20% of capital in one item). A deal whose cost exceeds
the resulting stake is not forbidden outright: the answer says by how much to negotiate down.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class KellyResult:
    full_fraction: float  # growth-optimal share of capital, 0 when there is no edge
    fraction_used: float
    max_stake: float  # euros
    affordable: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "full_kelly": round(self.full_fraction, 4),
            "fraction_used": round(self.fraction_used, 4),
            "max_stake": round(self.max_stake, 2),
            "affordable": self.affordable,
            "reason": self.reason,
        }


def growth_optimal_fraction(returns: list[float], f_max: float = 1.0, step: float = 0.005) -> float:
    """argmax over f in [0, f_max] of the mean of log(1 + f*r); 0 when no f beats not investing."""
    if not returns:
        return 0.0
    best_f, best_g = 0.0, 0.0
    steps = int(f_max / step)
    for i in range(1, steps + 1):
        f = i * step
        total = 0.0
        ok = True
        for r in returns:
            x = 1.0 + f * r
            if x <= 1e-9:  # ruin: this fraction is out
                ok = False
                break
            total += math.log(x)
        if not ok:
            break  # larger fractions only ruin more
        g = total / len(returns)
        if g > best_g:
            best_f, best_g = f, g
    return best_f


def kelly_stake(
    returns: list[float],
    capital: float,
    cost: float,
    confidence: float = 1.0,
    kelly_multiplier: float = 0.25,
    cap_fraction: float = 0.20,
) -> KellyResult:
    if capital <= 0:
        return KellyResult(0.0, 0.0, 0.0, False, "nessun capitale disponibile")
    full = growth_optimal_fraction(returns)
    if full <= 0:
        return KellyResult(
            0.0, 0.0, 0.0, False, "nessun vantaggio atteso: la crescita del capitale non migliora"
        )
    used = min(cap_fraction, full * kelly_multiplier * max(0.0, min(1.0, confidence)))
    stake = used * capital
    if cost <= stake:
        return KellyResult(full, used, stake, True, "il costo sta dentro la quota consigliata del capitale")
    return KellyResult(
        full,
        used,
        stake,
        False,
        f"costo {cost:.2f} € sopra la quota consigliata ({stake:.2f} €): trattare o rinunciare",
    )
