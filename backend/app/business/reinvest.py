"""Reinvestment policy: how much of the profit goes back into stock, how much is withdrawn, how much is reserve.

Simulates compound growth month by month under a policy and capacity ceilings (maximum capital, and the
profit the available hours can produce), then proposes the policy with the highest final capital that still
pays out at least the withdrawal the user wants. Within the user's limits only: the proposal never exceeds
the reinvest ceiling or breaks the reserve.
"""

from __future__ import annotations

from dataclasses import dataclass

CANDIDATES = (0.0, 0.25, 0.5, 0.75, 1.0)


@dataclass(frozen=True)
class Sim:
    reinvest_pct: float
    final_capital: float
    total_withdrawn: float
    total_profit: float
    capped_by: str | None


def simulate(
    capital0: float,
    monthly_return: float,
    months: int,
    reinvest_pct: float,
    max_capital: float | None = None,
    time_cap_profit: float | None = None,
    reserve: float = 0.0,
) -> Sim:
    capital, withdrawn, total, capped = capital0, 0.0, 0.0, None
    for _ in range(months):
        profit = max(0.0, (capital - reserve) * monthly_return)
        if time_cap_profit is not None and profit > time_cap_profit:
            profit, capped = time_cap_profit, "time"
        total += profit
        keep = profit * reinvest_pct
        if max_capital is not None and capital + keep > max_capital:
            keep = max(0.0, max_capital - capital)
            capped = capped or "capital"
        capital += keep
        withdrawn += profit - keep
    return Sim(reinvest_pct, round(capital, 2), round(withdrawn, 2), round(total, 2), capped)


def propose(
    capital0: float,
    monthly_return: float,
    months: int,
    min_total_withdrawn: float = 0.0,
    max_capital: float | None = None,
    time_cap_profit: float | None = None,
    reserve: float = 0.0,
    reinvest_ceiling: float = 1.0,
) -> dict[str, object]:
    sims = [
        simulate(capital0, monthly_return, months, r, max_capital, time_cap_profit, reserve)
        for r in CANDIDATES
        if r <= reinvest_ceiling + 1e-9
    ]
    ok = [s for s in sims if s.total_withdrawn >= min_total_withdrawn]
    best = max(ok, key=lambda s: (s.final_capital, -s.reinvest_pct)) if ok else None
    return {
        "options": [s.__dict__ for s in sims],
        "proposal": best.__dict__ if best else None,
        "note": (
            "Nessuna politica rispetta il prelievo richiesto con questi rendimenti."
            if best is None
            else "Politica con il capitale finale più alto che rispetta il prelievo richiesto, entro i tuoi limiti."
        ),
    }
