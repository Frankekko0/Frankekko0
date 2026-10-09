"""Operating figures used when the user's own history is too short. Every output says when it used them."""

from __future__ import annotations

from dataclasses import dataclass

MIN_SALES_FOR_MEASURED = 5


@dataclass(frozen=True)
class Operating:
    avg_cost: float  # euros of capital per item
    avg_profit: float  # euros of profit per item sold
    hold_days: float  # days from purchase to sale
    minutes_per_item: float  # end to end: find, buy, receive, photograph, list, answer, pack, ship
    basis: str  # "measured" | "assumption"

    def as_dict(self) -> dict[str, object]:
        return {
            "avg_cost": round(self.avg_cost, 2),
            "avg_profit": round(self.avg_profit, 2),
            "hold_days": round(self.hold_days, 1),
            "minutes_per_item": self.minutes_per_item,
            "basis": self.basis,
        }


DEFAULT = Operating(avg_cost=20.0, avg_profit=9.0, hold_days=18.0, minutes_per_item=45.0, basis="assumption")


def measured(
    sales: list[tuple[float, float, float]], minutes_per_item: float = DEFAULT.minutes_per_item
) -> Operating:
    """From closed sales as (cost, profit, holding days). Fewer than 5: the assumption, said so."""
    if len(sales) < MIN_SALES_FOR_MEASURED:
        return Operating(
            DEFAULT.avg_cost, DEFAULT.avg_profit, DEFAULT.hold_days, minutes_per_item, "assumption"
        )
    n = len(sales)
    return Operating(
        sum(s[0] for s in sales) / n,
        sum(s[1] for s in sales) / n,
        max(1.0, sum(s[2] for s in sales) / n),
        minutes_per_item,
        "measured",
    )
