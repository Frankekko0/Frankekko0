"""Database side of business mode: the figures the pure modules need, and the alerts they raise."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business import assumptions, cashflow, kpi, niches, tax
from app.db.models import (
    Alert,
    AutonomyAction,
    Brand,
    BusinessGoals,
    Category,
    Expense,
    InventoryItem,
    MarketStatistic,
    Purchase,
    Sale,
)
from app.domain.enums import AlertPriority, AlertType
from app.profit.calculator import CostProfile, sale_revenue
from app.selling import accounting
from app.selling.stages import IN_STOCK


async def goals_row(session: AsyncSession, user_id: uuid.UUID) -> BusinessGoals:
    row = await session.get(BusinessGoals, user_id)
    if row is None:
        row = BusinessGoals(user_id=user_id)
        session.add(row)
        await session.flush()
    return row


async def books(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[list[Purchase], list[Sale], list[Expense]]:
    purchases = (await session.execute(select(Purchase).where(Purchase.user_id == user_id))).scalars().all()
    sales = (await session.execute(select(Sale).where(Sale.user_id == user_id))).scalars().all()
    expenses = (await session.execute(select(Expense).where(Expense.user_id == user_id))).scalars().all()
    return list(purchases), list(sales), list(expenses)


def operating_from(
    purchases: list[Purchase], sales: list[Sale], minutes_per_item: float = 45.0
) -> assumptions.Operating:
    by_id = {p.id: p for p in purchases}
    rows = [
        (float(by_id[s.purchase_id].total_cost), float(s.profit), float(s.holding_days))
        for s in sales
        if s.purchase_id in by_id
    ]
    return assumptions.measured(rows, minutes_per_item)


def accounting_books(
    purchases: list[Purchase], sales: list[Sale], expenses: list[Expense]
) -> tuple[list[accounting.PurchaseRec], list[accounting.SaleRec], list[accounting.ExpenseRec]]:
    by_id = {p.id: p for p in purchases}
    sold = {s.purchase_id for s in sales}
    return (
        [accounting.PurchaseRec(str(p.id), p.title, p.purchase_date, p.total_cost, p.id in sold) for p in purchases],
        [
            accounting.SaleRec(
                str(s.id), str(s.purchase_id), by_id[s.purchase_id].title if s.purchase_id in by_id else "—", s.sale_date,
                s.sale_price, s.selling_fees, s.shipping_cost, s.packaging_cost, s.other_costs,
                by_id[s.purchase_id].total_cost if s.purchase_id in by_id else Decimal(0),
            )
            for s in sales
        ],
        [accounting.ExpenseRec(str(e.id), e.spent_on, e.kind, e.amount, e.note) for e in expenses],
    )  # fmt: skip


def niche_key(p: Purchase) -> str:
    return f"{p.brand_name or '—'} · {p.category_name or '—'}"


def niche_sales(purchases: list[Purchase], sales: list[Sale]) -> list[niches.NicheSale]:
    by_id = {p.id: p for p in purchases}
    return [
        niches.NicheSale(
            niche_key(by_id[s.purchase_id]),
            float(by_id[s.purchase_id].total_cost),
            float(s.profit),
            float(s.holding_days),
            s.sale_date,
            float(s.sale_price),
        )
        for s in sales
        if s.purchase_id in by_id
    ]


async def stock_items(session: AsyncSession, user_id: uuid.UUID) -> list[tuple[Purchase, InventoryItem]]:
    rows = (
        await session.execute(
            select(Purchase, InventoryItem)
            .join(InventoryItem, InventoryItem.purchase_id == Purchase.id)
            .where(Purchase.user_id == user_id, InventoryItem.stage.in_(IN_STOCK))
        )
    ).all()
    return [(p, i) for p, i in rows if p.sale is None]


def stock_forecast(
    items: list[tuple[Purchase, InventoryItem]], costs: CostProfile
) -> tuple[list[cashflow.StockForecast], int]:
    """Expected net revenue of each item in stock; items with no known price are left out and counted."""
    out, unknown = [], 0
    for p, i in items:
        price = i.listed_price or p.expected_sale_price
        if price is None:
            unknown += 1
            continue
        out.append(
            cashflow.StockForecast(
                float(sale_revenue(price, costs).net), float(p.total_cost), i.stage == "listed"
            )
        )
    return out, unknown


def kpi_inputs(
    purchases: list[Purchase], sales: list[Sale], stock: list[tuple[Purchase, InventoryItem]]
) -> tuple[list[kpi.SaleItem], list[kpi.StockItem]]:
    by_id = {p.id: p for p in purchases}
    sale_items = [
        kpi.SaleItem(by_id[s.purchase_id].total_cost, s.net_revenue, s.profit, s.holding_days, s.sale_date)
        for s in sales
        if s.purchase_id in by_id
    ]
    stock_rows = [kpi.StockItem(p.total_cost, p.purchase_date, i.stage == "listed") for p, i in stock]
    return sale_items, stock_rows


async def cash_now(session: AsyncSession, user_id: uuid.UUID, goals: BusinessGoals) -> float:
    """The capital put in plus every cash movement of the ledger (a basis the report states)."""
    p, s, e = await books(session, user_id)
    pr, sl, ex = accounting_books(p, s, e)
    total = accounting.summarize(pr, sl, ex, date(2000, 1, 1), date(2100, 1, 1))["cash_flow"]
    return float(goals.initial_capital or 0) + float(total)


def monthly_running_costs(expenses: list[Expense], today: date) -> float:
    since = today - timedelta(days=90)
    recent = [float(e.amount) for e in expenses if e.spent_on >= since]
    return sum(recent) / 3.0 if recent else 0.0


async def actions_by_status(session: AsyncSession, user_id: uuid.UUID, since: datetime) -> dict[str, int]:
    rows = (
        (
            await session.execute(
                select(AutonomyAction.status).where(
                    AutonomyAction.user_id == user_id, AutonomyAction.created_at >= since
                )
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, int] = {}
    for st in rows:
        out[st] = out.get(st, 0) + 1
    return out


async def tax_notices(
    session: AsyncSession, user_id: uuid.UUID, today: date | None = None
) -> list[tax.TaxNotice]:
    today = today or datetime.now(UTC).date()
    goals = await goals_row(session, user_id)
    thresholds = tax.parse(list(goals.tax_thresholds or []))
    if not thresholds:
        return []
    p, s, e = await books(session, user_id)
    pr, sl, ex = accounting_books(p, s, e)
    y = accounting.summarize(pr, sl, ex, date(today.year, 1, 1), today)
    return tax.check(
        thresholds, float(y["revenue"]), float(y["realized_profit"]), int(y["sales_count"]), today
    )


async def raise_tax_alerts(session: AsyncSession, user_id: uuid.UUID, today: date | None = None) -> int:
    """An in-app notice the first time a threshold is near and again if it is crossed (once per year and level)."""
    today = today or datetime.now(UTC).date()
    created = 0
    for n in await tax_notices(session, user_id, today):
        if n.level == "ok":
            continue
        stmt = (
            pg_insert(Alert)
            .values(
                id=uuid.uuid4(), user_id=user_id, type=AlertType.SYSTEM.value,
                priority=AlertPriority.HIGH.value if n.level == "exceeded" else AlertPriority.NORMAL.value,
                title=f"Soglia «{n.name}»: {'superata' if n.level == 'exceeded' else 'ti stai avvicinando'}"[:200],
                body=n.message, payload={"source": "business.tax", **n.as_dict()},
                dedupe_key=f"tax:{today.year}:{n.name}:{n.level}", created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
            .returning(Alert.id)
        )  # fmt: skip
        if (await session.execute(stmt)).scalar_one_or_none() is not None:
            created += 1
    return created


async def market_ref_for(
    session: AsyncSession, brand_slug: str | None, category_slug: str | None, size: str | None
) -> Any:
    """The most specific stored segment for brand + category (+ size) with its sold prices."""
    from app.business.scanner import MarketRef

    if not brand_slug:
        return None
    brand = (await session.execute(select(Brand).where(Brand.slug == brand_slug))).scalar_one_or_none()
    if brand is None:
        return None
    q = select(MarketStatistic).where(
        MarketStatistic.brand_id == brand.id, MarketStatistic.model_name.is_(None)
    )
    if category_slug:
        cat = (
            await session.execute(select(Category).where(Category.slug == category_slug))
        ).scalar_one_or_none()
        if cat is not None:
            q = q.where(MarketStatistic.category_id == cat.id)
    rows = (await session.execute(q)).scalars().all()
    if not rows:
        return None
    sized = [r for r in rows if size and r.size_normalized == (size or "").upper()]
    best = max(sized or rows, key=lambda r: r.sold_count)
    median = best.median_sold_price or best.median_price
    return MarketRef(
        float(median),
        float(best.p25_price),
        float(best.p75_price),
        int(best.sold_count),
        "brand+categoria" + (f"+taglia {best.size_normalized}" if best in sized else ""),
    )
