"""The ledger: purchases, sales and other costs, a summary for the accountant and a CSV export.

Cash basis: a purchase is an outflow on its purchase date, a sale an inflow on its sale date, an expense
an outflow on its date. *Realised profit* counts only closed sales (revenue minus selling costs minus the
cost of the item sold); the cost of unsold stock is shown separately and is not a loss. No tax rate or
threshold is invented here: the ones the user configures come from their own source, with a date.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.tracking.queries import csv_cell

ZERO = Decimal("0")


@dataclass(frozen=True)
class PurchaseRec:
    id: str
    title: str
    purchase_date: date
    total_cost: Decimal  # price + protection + shipping + other costs paid at purchase
    sold: bool


@dataclass(frozen=True)
class SaleRec:
    id: str
    purchase_id: str
    title: str
    sale_date: date
    sale_price: Decimal
    selling_fees: Decimal
    shipping_cost: Decimal
    packaging_cost: Decimal
    other_costs: Decimal
    purchase_total_cost: Decimal


@dataclass(frozen=True)
class ExpenseRec:
    id: str
    spent_on: date
    kind: str
    amount: Decimal
    note: str | None = None


@dataclass(frozen=True)
class LedgerRow:
    on: date
    kind: str  # purchase | sale | fee | shipping | expense
    ref: str
    description: str
    amount: Decimal  # signed: money in is positive, money out is negative


def build_ledger(
    purchases: list[PurchaseRec], sales: list[SaleRec], expenses: list[ExpenseRec], start: date, end: date
) -> list[LedgerRow]:
    rows: list[LedgerRow] = []
    for p in purchases:
        if start <= p.purchase_date <= end:
            rows.append(LedgerRow(p.purchase_date, "purchase", p.id, f"Acquisto: {p.title}", -p.total_cost))
    for s in sales:
        if not (start <= s.sale_date <= end):
            continue
        rows.append(LedgerRow(s.sale_date, "sale", s.id, f"Vendita: {s.title}", s.sale_price))
        if s.selling_fees:
            rows.append(LedgerRow(s.sale_date, "fee", s.id, f"Commissioni: {s.title}", -s.selling_fees))
        outbound = s.shipping_cost + s.packaging_cost + s.other_costs
        if outbound:
            rows.append(
                LedgerRow(s.sale_date, "shipping", s.id, f"Spedizione e imballo: {s.title}", -outbound)
            )
    for e in expenses:
        if start <= e.spent_on <= end:
            rows.append(
                LedgerRow(
                    e.spent_on,
                    "expense",
                    e.id,
                    f"Spesa ({e.kind}){': ' + e.note if e.note else ''}",
                    -e.amount,
                )
            )
    rows.sort(key=lambda r: (r.on, r.kind, r.ref))
    return rows


def summarize(
    purchases: list[PurchaseRec],
    sales: list[SaleRec],
    expenses: list[ExpenseRec],
    start: date,
    end: date,
) -> dict[str, Any]:
    rows = build_ledger(purchases, sales, expenses, start, end)
    inflow = sum((r.amount for r in rows if r.amount > 0), ZERO)
    outflow = -sum((r.amount for r in rows if r.amount < 0), ZERO)
    in_period = [s for s in sales if start <= s.sale_date <= end]
    revenue = sum((s.sale_price for s in in_period), ZERO)
    selling_costs = sum(
        (s.selling_fees + s.shipping_cost + s.packaging_cost + s.other_costs for s in in_period), ZERO
    )
    cost_of_goods = sum((s.purchase_total_cost for s in in_period), ZERO)
    other = sum((e.amount for e in expenses if start <= e.spent_on <= end), ZERO)
    realized = revenue - selling_costs - cost_of_goods
    stock = [p for p in purchases if not p.sold and p.purchase_date <= end]
    by_month: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"in": ZERO, "out": ZERO})
    for r in rows:
        key = r.on.strftime("%Y-%m")
        by_month[key]["in" if r.amount > 0 else "out"] += abs(r.amount)
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "basis": "cassa: acquisti e spese alla data di pagamento, vendite alla data di incasso",
        "cash_in": inflow,
        "cash_out": outflow,
        "cash_flow": inflow - outflow,
        "revenue": revenue,
        "selling_costs": selling_costs,
        "cost_of_goods_sold": cost_of_goods,
        "other_expenses": other,
        "realized_profit": realized,
        "realized_profit_after_expenses": realized - other,
        "sales_count": len(in_period),
        "purchases_count": sum(1 for p in purchases if start <= p.purchase_date <= end),
        "stock_at_cost": sum((p.total_cost for p in stock), ZERO),
        "stock_items": len(stock),
        "by_month": {k: {"in": v["in"], "out": v["out"]} for k, v in sorted(by_month.items())},
    }


def to_csv(rows: list[LedgerRow]) -> str:
    """Excel-friendly: BOM, semicolons, formula-safe cells."""
    out = io.StringIO()
    out.write("﻿")
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow(["data", "tipo", "riferimento", "descrizione", "importo_eur", "saldo_progressivo_eur"])
    running = ZERO
    for r in rows:
        running += r.amount
        w.writerow(
            [
                r.on.isoformat(),
                csv_cell(r.kind),
                csv_cell(r.ref),
                csv_cell(r.description),
                f"{r.amount:.2f}".replace(".", ","),
                f"{running:.2f}".replace(".", ","),
            ]
        )
    return out.getvalue()


def accountant_report(
    summary: dict[str, Any], holder_status: str, thresholds: list[dict[str, Any]], year_revenue: Decimal
) -> dict[str, Any]:
    """The one-page summary for the accountant. Thresholds are the user's, with source and date."""
    progress = []
    for t in thresholds:
        try:
            amount = Decimal(str(t["amount"]))
        except (KeyError, InvalidOperation):  # a malformed entry is skipped, not guessed
            continue
        progress.append(
            {
                "name": t.get("name", "soglia"),
                "amount": amount,
                "source": t.get("source"),
                "as_of": t.get("as_of"),
                "used": year_revenue,
                "share": float(year_revenue / amount) if amount > 0 else None,
            }
        )
    return {
        "summary": summary,
        "holder_status": holder_status,
        "thresholds": progress,
        "year_revenue": year_revenue,
        "notes": [
            "Prospetto informativo: non è una dichiarazione fiscale né una consulenza.",
            "Nessuna aliquota o soglia è stata inserita dal sistema: quelle sopra le ha configurate l'utente, con fonte e data.",
            "Verificare con il commercialista lo status (privato, occasionale, abituale, attività), gli obblighi di fatturazione, "
            "le comunicazioni delle piattaforme e la garanzia legale sull'usato.",
        ],
    }
