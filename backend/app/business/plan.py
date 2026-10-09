"""The business plan: what the goals require and whether capital and time allow it, in three scenarios."""

from __future__ import annotations

from dataclasses import dataclass

from app.business.assumptions import Operating

WEEKS_PER_MONTH = 4.33
UTILISATION = 0.8  # capital cannot be kept fully busy: some sits between a sale and the next purchase


@dataclass(frozen=True)
class Goals:
    monthly_profit_target: float
    initial_capital: float
    max_capital: float | None = None
    weekly_hours: float | None = None
    horizon_months: int = 12
    reinvest_pct: float = 0.7


@dataclass(frozen=True)
class ScenarioPlan:
    name: str
    items_per_month: float
    capital_needed: float
    hours_per_month: float
    rotation_per_month: float
    achievable_profit: float
    limited_by: str | None  # capital | time | None
    feasible: bool


SCENARIOS = {
    "prudente": (0.7, 1.3),  # (profit per item factor, holding time factor)
    "base": (1.0, 1.0),
    "ambizioso": (1.25, 0.85),
}


def scenario(goals: Goals, op: Operating, name: str) -> ScenarioPlan:
    fp, fh = SCENARIOS[name]
    profit_item = op.avg_profit * fp
    hold = op.hold_days * fh
    items = goals.monthly_profit_target / profit_item if profit_item > 0 else float("inf")
    capital = items * op.avg_cost * hold / 30.0 / UTILISATION
    hours = items * op.minutes_per_item / 60.0
    cap_limit = (
        min(x for x in (goals.initial_capital, goals.max_capital) if x is not None)
        if goals.max_capital is not None
        else goals.initial_capital
    )
    items_by_capital = cap_limit * UTILISATION * 30.0 / (op.avg_cost * hold)
    items_by_time = (
        (goals.weekly_hours * WEEKS_PER_MONTH * 60.0 / op.minutes_per_item)
        if goals.weekly_hours
        else float("inf")
    )
    cap = min(items, items_by_capital, items_by_time)
    limited = None if cap >= items - 1e-9 else ("capital" if items_by_capital <= items_by_time else "time")
    return ScenarioPlan(
        name=name,
        items_per_month=round(items, 1),
        capital_needed=round(capital, 2),
        hours_per_month=round(hours, 1),
        rotation_per_month=round(30.0 / hold, 2),
        achievable_profit=round(cap * profit_item, 2),
        limited_by=limited,
        feasible=limited is None,
    )


def trajectory(goals: Goals, op: Operating) -> list[dict[str, float]]:
    """Month by month: capital grows by the reinvested share of the profit, up to the capital and time ceilings."""
    out: list[dict[str, float]] = []
    capital = goals.initial_capital
    cum = 0.0
    monthly_return = (op.avg_profit / op.avg_cost) * (30.0 / op.hold_days) * UTILISATION
    time_cap = (
        goals.weekly_hours * WEEKS_PER_MONTH * 60.0 / op.minutes_per_item * op.avg_profit
        if goals.weekly_hours
        else float("inf")
    )
    for m in range(1, goals.horizon_months + 1):
        profit = min(capital * monthly_return, time_cap)
        cum += profit
        add = profit * goals.reinvest_pct
        capital = capital + add
        if goals.max_capital is not None:
            capital = min(capital, goals.max_capital)
        out.append(
            {
                "month": m,
                "capital": round(capital, 2),
                "profit": round(profit, 2),
                "cumulative_profit": round(cum, 2),
            }
        )
    return out


def business_plan(goals: Goals, op: Operating) -> dict[str, object]:
    plans = [scenario(goals, op, n) for n in SCENARIOS]
    traj = trajectory(goals, op)
    reach = next((t["month"] for t in traj if t["profit"] >= goals.monthly_profit_target), None)
    return {
        "operating": op.as_dict(),
        "scenarios": [p.__dict__ for p in plans],
        "trajectory": traj,
        "month_target_reached": reach,
        "note": (
            "Cifre operative misurate sulle tue vendite."
            if op.basis == "measured"
            else "Cifre operative ipotizzate (poche vendite chiuse): il piano migliora con i dati reali."
        ),
    }
