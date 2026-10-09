"""The unit economics of the business, from the ledger and the stock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

AGING_BUCKETS = (("0-14", 0, 14), ("15-30", 15, 30), ("31-60", 31, 60), ("60+", 61, 10**6))
DAILY_CAPITAL_COST = 0.002


@dataclass(frozen=True)
class StockItem:
    cost: Decimal
    purchased_on: date
    listed: bool = False


@dataclass(frozen=True)
class SaleItem:
    cost: Decimal
    net_revenue: Decimal  # sale price minus selling costs
    profit: Decimal
    holding_days: int
    sold_on: date


def aging(stock: list[StockItem], today: date) -> list[dict[str, object]]:
    out = []
    for label, lo, hi in AGING_BUCKETS:
        items = [s for s in stock if lo <= (today - s.purchased_on).days <= hi]
        out.append(
            {"bucket": label, "items": len(items), "cost": float(sum((s.cost for s in items), Decimal(0)))}
        )
    return out


def kpis(
    sales: list[SaleItem],
    stock: list[StockItem],
    returns: int,
    today: date,
    start: date,
    minutes_per_item: float = 45.0,
    weekly_hours: float | None = None,
) -> dict[str, object]:
    in_period = [s for s in sales if start <= s.sold_on <= today]
    n = len(in_period)
    profit = float(sum((s.profit for s in in_period), Decimal(0)))
    margin = float(sum((s.net_revenue - s.cost for s in in_period), Decimal(0)))
    avg_stock = float(sum((s.cost for s in stock), Decimal(0)))
    sold_cost = float(sum((s.cost for s in in_period), Decimal(0)))
    available = n + len(stock)
    days = max(1, (today - start).days)
    hours_est = n * minutes_per_item / 60.0
    hours = (weekly_hours * days / 7.0) if weekly_hours else hours_est
    idle = (
        float(sum((s.cost for s in stock), Decimal(0)))
        * DAILY_CAPITAL_COST
        * (sum((today - s.purchased_on).days for s in stock) / len(stock) if stock else 0)
    )
    return {
        "period": {"start": start.isoformat(), "end": today.isoformat(), "days": days},
        "sales": n,
        "realized_profit": round(profit, 2),
        "contribution_margin_per_item": round(margin / n, 2) if n else None,
        "gmroi": round(margin / avg_stock, 3) if avg_stock > 0 else None,
        "gmroi_note": "margine di contribuzione / costo delle giacenze attuali (stima: la giacenza media del periodo non è registrata)",
        "sell_through": round(n / available, 3) if available else None,
        "inventory_turnover": round(sold_cost / avg_stock, 3) if avg_stock > 0 else None,
        "aging": aging(stock, today),
        "cash_conversion_days": round(sum(s.holding_days for s in in_period) / n, 1) if n else None,
        "idle_capital_cost": round(idle, 2),
        "hourly_profit": round(profit / hours, 2) if hours > 0 and n else None,
        "hours_basis": "dichiarate dall'utente (ore settimanali)"
        if weekly_hours
        else f"stimate: {minutes_per_item:g} minuti per articolo",
        "return_rate": round(returns / (n + returns), 3) if (n + returns) else None,
    }
