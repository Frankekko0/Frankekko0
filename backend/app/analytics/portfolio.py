"""My Flips & portfolio analytics: realized profit, ROI, holding time, win rate, monthly profit."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO, money
from app.db.models import InventoryItem, Purchase, Sale


@dataclass(frozen=True)
class SaleFigures:
    net_revenue: Decimal
    profit: Decimal
    roi: Decimal
    holding_days: int


def sale_figures(
    purchase_total_cost: Decimal,
    sale_price: Decimal,
    selling_fees: Decimal,
    shipping_cost: Decimal,
    packaging_cost: Decimal,
    other_costs: Decimal,
    purchase_date: date,
    sale_date: date,
) -> SaleFigures:
    net = money(sale_price - selling_fees - shipping_cost - packaging_cost - other_costs)
    profit = net - purchase_total_cost
    roi = (profit / purchase_total_cost).quantize(Decimal("0.0001")) if purchase_total_cost > 0 else ZERO
    return SaleFigures(net, profit, roi, max(0, (sale_date - purchase_date).days))


async def portfolio_summary(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    purchases = (await session.execute(select(Purchase).where(Purchase.user_id == user_id))).scalars().all()
    sales = (await session.execute(select(Sale).where(Sale.user_id == user_id))).scalars().all()
    inventory = (
        (await session.execute(select(InventoryItem).where(InventoryItem.user_id == user_id))).scalars().all()
    )
    sold_ids = {s.purchase_id for s in sales}
    by_purchase = {p.id: p for p in purchases}

    total_invested = sum((p.total_cost for p in purchases), ZERO)
    unsold = [p for p in purchases if p.id not in sold_ids]
    inv_by_purchase = {i.purchase_id: i for i in inventory}
    inventory_value = ZERO
    inventory_cost = ZERO
    for p in unsold:
        item = inv_by_purchase.get(p.id)
        if item is not None and item.status == "returned":
            continue
        inventory_cost += p.total_cost
        inventory_value += (
            (item.estimated_value if item and item.estimated_value else None)
            or p.expected_sale_price
            or p.total_cost
        )
    revenue = sum((s.sale_price for s in sales), ZERO)
    profit = sum((s.profit for s in sales), ZERO)
    n = len(sales)
    monthly: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"profit": ZERO, "revenue": ZERO, "sales": 0, "invested": ZERO}
    )
    for s in sales:
        key = s.sale_date.strftime("%Y-%m")
        monthly[key]["profit"] += s.profit
        monthly[key]["revenue"] += s.sale_price
        monthly[key]["sales"] += 1
    for p in purchases:
        monthly[p.purchase_date.strftime("%Y-%m")]["invested"] += p.total_cost
    best = max(sales, key=lambda s: s.profit, default=None)
    return {
        "total_invested": float(total_invested),
        "inventory_items": len(unsold),
        "inventory_cost": float(inventory_cost),
        "inventory_value": float(inventory_value),
        "revenue": float(revenue),
        "profit": float(profit),
        "average_roi": float(sum((s.roi for s in sales), ZERO) / n) if n else None,
        "average_holding_days": round(sum(s.holding_days for s in sales) / n, 1) if n else None,
        "win_rate": round(sum(1 for s in sales if s.profit > 0) / n, 4) if n else None,
        "flips_completed": n,
        "purchases": len(purchases),
        "monthly": [
            {
                "month": k,
                "profit": float(v["profit"]),
                "revenue": float(v["revenue"]),
                "sales": v["sales"],
                "invested": float(v["invested"]),
            }
            for k, v in sorted(monthly.items())
        ],
        "best_flip": {
            "title": by_purchase[best.purchase_id].title if best.purchase_id in by_purchase else None,
            "profit": float(best.profit),
            "roi": float(best.roi),
        }
        if best
        else None,
    }
