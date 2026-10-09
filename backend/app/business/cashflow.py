"""Cash flow forecast at 30, 60 and 90 days, with a stress test.

Inflows: items in stock sell with the probability given by an exponential time-to-sell with the mean holding
time, at their expected net revenue, and the money arrives after the payout delay. Outflows: the planned
purchases (the reinvested share of what is sold) and the monthly running costs seen in the ledger. The stress
scenario halves the sales, delays the payments and adds a return peak. A negative or sub-reserve closing cash
is a liquidity risk, reported as such.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class StockForecast:
    expected_net_revenue: float  # what it would bring if sold
    cost: float
    listed: bool


@dataclass(frozen=True)
class Stress:
    sales_factor: float = 1.0
    extra_payout_delay: int = 0
    return_rate: float = 0.04


BASE = Stress()
STRESS = Stress(sales_factor=0.5, extra_payout_delay=14, return_rate=0.15)


LIST_DELAY_DAYS = 5  # an item not yet listed needs this long before it can sell


def forecast(
    cash_now: float,
    stock: list[StockForecast],
    hold_days: float,
    monthly_running_costs: float,
    reinvest_pct: float,
    reserve: float,
    stress: Stress = BASE,
    payout_delay: int = 3,
    horizons: tuple[int, ...] = (30, 60, 90),
    committed_purchases: list[float] | None = None,
) -> list[dict[str, object]]:
    """``committed_purchases``: what was already decided to buy at each horizon (the stress scenario keeps the
    purchases of the base plan: you buy before you know sales will halve)."""
    rate = stress.sales_factor / max(1.0, hold_days)
    out: list[dict[str, object]] = []
    for i, h in enumerate(horizons):
        arrive_by = h - payout_delay - stress.extra_payout_delay
        gross = cost_sold = 0.0
        for item in stock:
            window = arrive_by - (0 if item.listed else LIST_DELAY_DAYS)
            p = 1 - math.exp(-rate * window) if window > 0 else 0.0
            gross += item.expected_net_revenue * p
            cost_sold += item.cost * p
        inflow = gross * (1 - stress.return_rate)
        if committed_purchases is not None:
            purchases = committed_purchases[i]
        else:
            profit = max(0.0, gross - cost_sold)
            purchases = min(max(0.0, cash_now + inflow - reserve), cost_sold + profit * reinvest_pct)
        running = monthly_running_costs * h / 30.0
        closing = cash_now + inflow - purchases - running
        out.append(
            {
                "horizon_days": h,
                "opening_cash": round(cash_now, 2),
                "inflow": round(inflow, 2),
                "planned_purchases": round(purchases, 2),
                "running_costs": round(running, 2),
                "closing_cash": round(closing, 2),
                "liquidity_risk": closing < max(0.0, reserve),
            }
        )
    return out


def with_stress(
    cash_now: float,
    stock: list[StockForecast],
    hold_days: float,
    monthly_running_costs: float,
    reinvest_pct: float,
    reserve: float,
) -> dict[str, object]:
    base = forecast(cash_now, stock, hold_days, monthly_running_costs, reinvest_pct, reserve, BASE)
    committed = [float(r["planned_purchases"]) for r in base]  # type: ignore[arg-type]
    stressed = forecast(
        cash_now,
        stock,
        hold_days,
        monthly_running_costs,
        reinvest_pct,
        reserve,
        STRESS,
        committed_purchases=committed,
    )
    risky = [s["horizon_days"] for s in stressed if s["liquidity_risk"]]
    return {
        "base": base,
        "stress": stressed,
        "stress_definition": {
            "sales": "-50%",
            "payments_delayed_days": STRESS.extra_payout_delay,
            "returns": f"{STRESS.return_rate:.0%}",
        },
        "liquidity_risk_in_stress": bool(risky),
        "risk_horizons": risky,
        "message": (
            f"Con vendite dimezzate la liquidità scende sotto la riserva a {', '.join(str(r) for r in risky)} giorni: "
            "rallenta gli acquisti e libera capitale dalle giacenze ferme."
            if risky
            else "Anche con vendite dimezzate la liquidità resta sopra la riserva."
        ),
    }
