"""The resale price: where to start, where to stop and how to come down (never above what sells)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.intelligence import survival

START_HEADROOM = 0.10  # buyers on Vinted offer about this much less: room for a first offer
MAX_RATIO = 1.5  # never start above 1.5x the market reference


@dataclass(frozen=True)
class ResalePlan:
    start_price: float
    best_price: float  # maximises profit per day
    floor: float  # lowest price that still clears the minimum profit
    expected_days_at_best: float
    p_sold_30d_at_best: float
    markdowns: list[survival.MarkdownStep]
    basis: str  # "measured" or "prior"
    reliable: bool
    note: str

    def as_dict(self) -> dict[str, object]:
        return {
            "start_price": self.start_price,
            "best_price": self.best_price,
            "floor": self.floor,
            "expected_days_at_best": round(self.expected_days_at_best, 1),
            "p_sold_30d_at_best": round(self.p_sold_30d_at_best, 3),
            "markdowns": [{"day": m.day, "price": m.price, "why": m.why} for m in self.markdowns],
            "basis": self.basis,
            "reliable": self.reliable,
            "note": self.note,
        }


def floor_price(
    profit_at: Callable[[float], float], min_profit: float, lo: float = 0.5, hi: float = 2000.0
) -> float:
    """Smallest price whose profit is at least ``min_profit`` (profit grows with price: bisection)."""
    if profit_at(hi) < min_profit:
        return hi
    for _ in range(60):
        mid = (lo + hi) / 2
        if profit_at(mid) >= min_profit:
            hi = mid
        else:
            lo = mid
    return round(hi + 0.005, 2)


def plan_resale(
    *,
    reference: float,
    profit_at: Callable[[float], float],
    fit: survival.SurvivalFit,
    min_profit: float = 5.0,
    steps: int = 4,
) -> ResalePlan | None:
    """``None`` when no price reaches the minimum profit (the item cannot be resold at the target)."""
    if reference <= 0:
        return None
    floor = floor_price(profit_at, min_profit)
    best = survival.best_price(fit, reference, profit_at, min_profit=min_profit)
    if best is None:
        return None
    start = round(min(best.price * (1 + START_HEADROOM), reference * MAX_RATIO), 2)
    start = max(start, floor)
    plan = survival.markdown_plan(fit, reference, start, floor, steps=steps)
    note = (
        "Tempi di vendita misurati su articoli simili."
        if fit.reliable
        else "Pochi articoli simili venduti: i tempi di vendita sono un'ipotesi prudente, non una misura."
    )
    return ResalePlan(
        start_price=start,
        best_price=best.price,
        floor=floor,
        expected_days_at_best=best.expected_days,
        p_sold_30d_at_best=best.p_sold_30d,
        markdowns=plan,
        basis=fit.source,
        reliable=fit.reliable,
        note=note,
    )
