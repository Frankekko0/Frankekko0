"""Business mode: goals, plan, KPIs, cash flow with stress test, niches, CEO report, fiscal thresholds, refurbishing,
in-store scanner, operations and risk."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, Economics
from app.api.v1.plan import plan as purchase_plan
from app.api.v1.selling import _plain
from app.business import cashflow, ceo_report, kpi, niches, refurb, reinvest, risk, scanner, service, sop, tax
from app.business import plan as bplan
from app.core.errors import AppError
from app.db.models import Expense, InventoryItem, Purchase, Sale
from app.schemas.common import Money, Schema
from app.selling import accounting
from app.selling import service as selling

router = APIRouter(prefix="/business", tags=["business"])


# ------------------------------------------------------------------ goals
class GoalsIn(Schema):
    monthly_profit_target: Money | None = Field(default=None, ge=0, le=1_000_000)
    initial_capital: Money | None = Field(default=None, ge=0, le=10_000_000)
    max_capital: Money | None = Field(default=None, ge=0, le=10_000_000)
    weekly_hours: int | None = Field(default=None, ge=1, le=100)
    horizon_months: int = Field(default=12, ge=1, le=60)
    reinvest_pct: float = Field(default=0.7, ge=0, le=1)
    min_reserve: Money = Field(default=Decimal(0), ge=0)
    explore_share: float = Field(default=0.10, ge=0, le=0.5)
    holder_status: Literal["private", "occasional", "habitual", "business"] = "private"
    tax_thresholds: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


def _goals_out(g: Any) -> dict[str, Any]:
    return {
        "monthly_profit_target": float(g.monthly_profit_target)
        if g.monthly_profit_target is not None
        else None,
        "initial_capital": float(g.initial_capital) if g.initial_capital is not None else None,
        "max_capital": float(g.max_capital) if g.max_capital is not None else None,
        "weekly_hours": g.weekly_hours,
        "horizon_months": g.horizon_months,
        "reinvest_pct": float(g.reinvest_pct),
        "min_reserve": float(g.min_reserve),
        "explore_share": float(g.explore_share),
        "holder_status": g.holder_status,
        "tax_thresholds": list(g.tax_thresholds or []),
    }


@router.get("/goals", response_model=dict[str, Any])
async def get_goals(user: CurrentUser, db: DB) -> dict[str, Any]:
    g = await service.goals_row(db, user.id)
    await db.commit()
    return _goals_out(g)


@router.put("/goals", response_model=dict[str, Any])
async def put_goals(body: GoalsIn, user: CurrentUser, db: DB) -> dict[str, Any]:
    try:
        tax.parse(
            body.tax_thresholds
        )  # the thresholds are the user's, with source and date: nothing is stored without them
    except tax.ThresholdError as exc:
        raise AppError(str(exc), code="invalid_threshold") from exc
    if (
        body.max_capital is not None
        and body.initial_capital is not None
        and body.max_capital < body.initial_capital
    ):
        raise AppError("Il capitale massimo è inferiore a quello iniziale.", code="invalid_goals")
    g = await service.goals_row(db, user.id)
    for k, v in body.model_dump().items():
        setattr(g, k, Decimal(str(v)) if k in ("reinvest_pct", "explore_share") else v)
    await db.commit()
    return _goals_out(g)


def _need_target(g: Any) -> bplan.Goals:
    if g.monthly_profit_target is None or g.initial_capital is None:
        raise AppError(
            "Imposta almeno l'obiettivo di profitto mensile e il capitale iniziale.", code="goals_missing"
        )
    return bplan.Goals(
        float(g.monthly_profit_target), float(g.initial_capital), float(g.max_capital) if g.max_capital is not None else None,
        float(g.weekly_hours) if g.weekly_hours else None, g.horizon_months, float(g.reinvest_pct),
    )  # fmt: skip


# ------------------------------------------------------------------ plan, KPIs, cash flow
@router.get("/plan", response_model=dict[str, Any])
async def business_plan(user: CurrentUser, db: DB) -> dict[str, Any]:
    g = await service.goals_row(db, user.id)
    goals = _need_target(g)
    p, s, _ = await service.books(db, user.id)
    return _plain(bplan.business_plan(goals, service.operating_from(p, s)))


@router.get("/kpis", response_model=dict[str, Any])
async def business_kpis(
    user: CurrentUser, db: DB, start: date | None = None, end: date | None = None
) -> dict[str, Any]:
    today = end or datetime.now(UTC).date()
    since = start or today - timedelta(days=30)
    g = await service.goals_row(db, user.id)
    p, s, _ = await service.books(db, user.id)
    stock = await service.stock_items(db, user.id)
    sale_items, stock_rows = service.kpi_inputs(p, s, stock)
    returned = (
        await db.execute(
            select(func.count())
            .select_from(InventoryItem)
            .where(InventoryItem.user_id == user.id, InventoryItem.stage == "returned")
        )
    ).scalar_one()
    return _plain(
        kpi.kpis(
            sale_items,
            stock_rows,
            int(returned),
            today,
            since,
            weekly_hours=float(g.weekly_hours) if g.weekly_hours else None,
        )
    )


@router.get("/cashflow", response_model=dict[str, Any])
async def business_cashflow(user: CurrentUser, econ: Economics, db: DB) -> dict[str, Any]:
    """Cash forecast at 30, 60 and 90 days, and the same with sales halved, payments late and returns up."""
    g = await service.goals_row(db, user.id)
    p, s, e = await service.books(db, user.id)
    op = service.operating_from(p, s)
    stock = await service.stock_items(db, user.id)
    fc, unknown = service.stock_forecast(stock, econ.costs)
    out = cashflow.with_stress(
        await service.cash_now(db, user.id, g), fc, op.hold_days, service.monthly_running_costs(e, datetime.now(UTC).date()),
        float(g.reinvest_pct), float(g.min_reserve),
    )  # fmt: skip
    return {
        **_plain(out),
        "operating": op.as_dict(),
        "items_without_price": unknown,
        "basis": "cassa = capitale iniziale + movimenti del registro; incassi attesi dalle giacenze con prezzo noto",
    }


# ------------------------------------------------------------------ niches, reinvestment, risk
@router.get("/niches", response_model=dict[str, Any])
async def business_niches(user: CurrentUser, db: DB) -> dict[str, Any]:
    g = await service.goals_row(db, user.id)
    p, s, _ = await service.books(db, user.id)
    ns = service.niche_sales(p, s)
    st = niches.stats(ns)
    capital = float(g.max_capital or g.initial_capital or 0)
    alloc = niches.allocate(st, capital, explore_share=float(g.explore_share)) if capital > 0 else []
    books = {x.niche: [r for r in ns if r.niche == x.niche] for x in st}
    return _plain(
        {
            "niches": [{**x.__dict__, "declining": x.declining, "growing": x.growing} for x in st],
            "allocation": [a.__dict__ for a in alloc],
            "playbooks": {k: niches.playbook(v, k) for k, v in books.items()},
            "capital": capital,
            "note": "" if st else "Nessuna vendita chiusa: le nicchie si misurano sulle vendite reali.",
        }
    )


@router.get("/reinvestment", response_model=dict[str, Any])
async def business_reinvestment(user: CurrentUser, db: DB, min_withdrawn: float = 0.0) -> dict[str, Any]:
    g = await service.goals_row(db, user.id)
    goals = _need_target(g)
    p, s, _ = await service.books(db, user.id)
    op = service.operating_from(p, s)
    monthly_return = (op.avg_profit / op.avg_cost) * (30.0 / op.hold_days) * bplan.UTILISATION
    time_cap = (
        goals.weekly_hours * bplan.WEEKS_PER_MONTH * 60.0 / op.minutes_per_item * op.avg_profit
        if goals.weekly_hours
        else None
    )
    out = reinvest.propose(
        goals.initial_capital,
        monthly_return,
        goals.horizon_months,
        min_withdrawn,
        goals.max_capital,
        time_cap,
        float(g.min_reserve),
        goals.reinvest_pct,
    )
    return {**_plain(out), "operating": op.as_dict(), "monthly_return": round(monthly_return, 4)}


@router.get("/risk", response_model=dict[str, Any])
async def business_risk(user: CurrentUser, db: DB) -> dict[str, Any]:
    _p, s, _ = await service.books(db, user.id)
    by_platform: dict[str, float] = {}
    by_month: dict[str, list[float]] = {}
    for x in s:
        by_platform[x.platform] = by_platform.get(x.platform, 0.0) + float(x.sale_price)
        by_month.setdefault(x.sale_date.strftime("%Y-%m"), []).append(float(x.profit))
    margins = [sum(v) / len(v) for _, v in sorted(by_month.items())]
    returned = (
        await db.execute(
            select(func.count())
            .select_from(InventoryItem)
            .where(InventoryItem.user_id == user.id, InventoryItem.stage == "returned")
        )
    ).scalar_one()
    rate = returned / (len(s) + returned) if (len(s) + returned) else 0.0
    last_month = sum(by_platform.values()) / max(1, len(by_month)) if by_month else 0.0
    return {
        "platform": risk.platform_dependency(by_platform),
        "margin": risk.margin_alarm(margins, window=2),
        "reserve": {"amount": risk.reserve_needed(last_month, max(rate, 0.04), 0.02, 0.01), "return_rate": round(rate, 3),
                    "basis": "un mese di vendite medie x (resi, dispute 2%, perdite 1%): tassi di disputa e perdita ipotizzati"},
        "contingency": risk.CONTINGENCY,
        "insurance_rule": "assicura la spedizione sopra 50 € di valore o se non tracciata (soglia ipotizzata, modificabile)",
    }  # fmt: skip


# ------------------------------------------------------------------ fiscal thresholds
@router.get("/tax", response_model=dict[str, Any])
async def business_tax(user: CurrentUser, db: DB, notify: bool = False) -> dict[str, Any]:
    """Progress against the thresholds the user configured (with their source and date). ``notify`` raises the alerts."""
    g = await service.goals_row(db, user.id)
    notices = await service.tax_notices(db, user.id)
    raised = await service.raise_tax_alerts(db, user.id) if notify else 0
    await db.commit()
    return {"holder_status": g.holder_status, "notices": [n.as_dict() for n in notices], "alerts_raised": raised, "checklist": tax.PROFESSIONAL_CHECKLIST,
            "note": "Informativo, non consulenza fiscale. Nessuna soglia o aliquota è inserita dal sistema."}  # fmt: skip


# ------------------------------------------------------------------ the CEO report
@router.get("/report", response_model=dict[str, Any])
async def ceo(
    user: CurrentUser, econ: Economics, db: DB, period: Literal["week", "month"] = "week"
) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=6 if period == "week" else 29)
    g = await service.goals_row(db, user.id)
    p, s, e = await service.books(db, user.id)
    pr, sl, ex = service.accounting_books(p, s, e)
    summary = accounting.summarize(pr, sl, ex, start, today)
    stock = await service.stock_items(db, user.id)
    sale_items, stock_rows = service.kpi_inputs(p, s, stock)
    returned = (
        await db.execute(
            select(func.count())
            .select_from(InventoryItem)
            .where(InventoryItem.user_id == user.id, InventoryItem.stage == "returned")
        )
    ).scalar_one()
    k = kpi.kpis(
        sale_items,
        stock_rows,
        int(returned),
        today,
        start,
        weekly_hours=float(g.weekly_hours) if g.weekly_hours else None,
    )
    st = [x for x in niches.stats(service.niche_sales(p, s)) if x.n]
    best = [
        {"niche": x.niche, "profit": x.profit} for x in sorted(st, key=lambda x: -x.profit) if x.profit > 0
    ]
    worst = [
        {"niche": x.niche, "profit": x.profit} for x in sorted(st, key=lambda x: x.profit) if x.profit < 0
    ]
    fc, _ = service.stock_forecast(stock, econ.costs)
    op = service.operating_from(p, s)
    cf = cashflow.with_stress(
        await service.cash_now(db, user.id, g),
        fc,
        op.hold_days,
        service.monthly_running_costs(e, today),
        float(g.reinvest_pct),
        float(g.min_reserve),
    )
    notices = await service.tax_notices(db, user.id)
    by_month: dict[str, list[float]] = {}
    for x in s:
        by_month.setdefault(x.sale_date.strftime("%Y-%m"), []).append(float(x.profit))
    margin = risk.margin_alarm([sum(v) / len(v) for _, v in sorted(by_month.items())], window=2)
    listed_old = sum(1 for _, i in stock if i.stage == "listed" and selling.days_listed(i) >= 14)
    suggestions = len((await purchase_plan(user, econ, db)).selected)
    report = ceo_report.build(
        period=period, start=start, end=today, summary=summary, kpis=_plain(k),
        monthly_target=float(g.monthly_profit_target) if g.monthly_profit_target is not None else None,
        niche_best=best, niche_worst=worst, actions_by_status=await service.actions_by_status(db, user.id, datetime.combine(start, datetime.min.time(), UTC)),
        to_list=sum(1 for _, i in stock if i.stage == "to_list"), reprice_due=listed_old, budget_left=None, plan_suggestions=suggestions,
        cash_message=str(cf["message"]), tax_messages=[n.message for n in notices if n.level != "ok"], margin_alarm=margin,
    )  # fmt: skip
    return {**report, "reprice_due_basis": "annunci pubblicati da almeno 14 giorni"}


# ------------------------------------------------------------------ refurbishing and the scanner
class RefurbIn(Schema):
    purchase_cost: float = Field(gt=0, le=100_000)
    resale_clean: float = Field(gt=0, le=100_000)
    defects: list[str] = Field(min_length=1, max_length=10)
    resale_as_is: float | None = Field(default=None, gt=0)
    min_roi: float = Field(default=0.4, ge=0, le=20)
    hourly_value: float = Field(default=refurb.HOURLY_VALUE, ge=0, le=500)


@router.post("/refurb", response_model=dict[str, Any])
async def refurbish(body: RefurbIn, user: CurrentUser, econ: Economics) -> dict[str, Any]:
    """Is it worth buying to fix? Judged on the ROI after the restoration (materials and labour included)."""
    from app.profit.calculator import sale_revenue

    r = refurb.evaluate(
        purchase_cost=body.purchase_cost, resale_clean=body.resale_clean, defects=body.defects,
        net_of_price=lambda p: float(sale_revenue(Decimal(str(round(p, 2))), econ.costs).net),
        resale_as_is=body.resale_as_is, min_roi=body.min_roi, hourly_value=body.hourly_value,
    )  # fmt: skip
    return {
        **r.as_dict(),
        "known_remedies": sorted(refurb.REMEDIES),
        "assumption": "i recuperi di valore per tipo di difetto sono ipotesi, non misure",
    }


class ScanIn(Schema):
    shown_price: float = Field(gt=0, le=100_000)
    brand: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)
    size: str | None = Field(default=None, max_length=20)
    label_text: str | None = Field(
        default=None, max_length=1000, description="Text read from the label (by the device)"
    )


@router.post("/scan", response_model=dict[str, Any])
async def scan_item(body: ScanIn, user: CurrentUser, econ: Economics, db: DB) -> dict[str, Any]:
    """In-store scanner: identification, the most worth paying and a verdict from the stored market data."""
    t0 = time.perf_counter()
    brand, size = (
        (body.brand or "").strip().lower().replace(" ", "-") or None,
        (body.size or "").strip().upper() or None,
    )
    if body.label_text:
        from app.vision.ocr import OcrLine, parse_ocr

        facts = parse_ocr(
            [(0, [OcrLine(t, 0.9, [0, 0, 1, 0.1]) for t in body.label_text.splitlines() if t.strip()])]
        )
        size = size or facts.size
        if brand is None and facts.brands:
            brand = facts.brands[0].lower().replace(" ", "-")
    category = (body.category or "").strip().lower().replace(" ", "-") or None
    ref = await service.market_ref_for(db, brand, category, size)
    res = scanner.scan(shown_price=body.shown_price, brand=brand, category=category, size=size, ref=ref, costs=econ.costs,
                       min_profit=float(econ.targets.min_profit), min_roi=float(econ.targets.min_roi), started=t0)  # fmt: skip
    return {**res.as_dict(), "latency_target_ms": scanner.LATENCY_TARGET_MS,
            "limits": "foto e codice a barre non sono riconosciuti: inserisci o incolla il testo dell'etichetta"}  # fmt: skip


# ------------------------------------------------------------------ operations
@router.get("/sop", response_model=dict[str, Any])
async def procedures() -> dict[str, Any]:
    return {"sops": sop.SOPS, "roles": sop.ROLE_PERMISSIONS,
            "note": "I ruoli sono descritti ma l'app ha un solo account: i permessi non sono ancora applicati."}  # fmt: skip


@router.get("/sop/{role}", response_model=dict[str, str])
async def brief(role: str) -> dict[str, str]:
    try:
        return {"role": role, "instructions": sop.collaborator_brief(role)}
    except ValueError as exc:
        raise AppError(str(exc), code="unknown_role") from exc


@router.get("/capacity", response_model=dict[str, Any])
async def capacity(
    user: CurrentUser, db: DB, storage_shelves: int | None = Query(default=None, ge=1, le=1000)
) -> dict[str, Any]:
    g = await service.goals_row(db, user.id)
    p, s, _ = await service.books(db, user.id)
    op = service.operating_from(p, s)
    items_month = (
        float(g.monthly_profit_target) / op.avg_profit
        if g.monthly_profit_target and op.avg_profit > 0
        else float(len(s))
    )
    stock = await service.stock_items(db, user.id)
    return {
        **sop.capacity_plan(
            items_month,
            op.minutes_per_item,
            float(g.weekly_hours) if g.weekly_hours else None,
            len(stock),
            storage_shelves=storage_shelves,
        ),
        "operating": op.as_dict(),
    }


@router.get("/export", response_model=dict[str, Any])
async def export_all(user: CurrentUser, db: DB) -> dict[str, Any]:
    """Everything the business holds, as JSON: the periodic export of the contingency plan (platform dependency)."""
    purchases = (await db.execute(select(Purchase).where(Purchase.user_id == user.id))).scalars().all()
    sales = (await db.execute(select(Sale).where(Sale.user_id == user.id))).scalars().all()
    expenses = (await db.execute(select(Expense).where(Expense.user_id == user.id))).scalars().all()
    inv = (await db.execute(select(InventoryItem).where(InventoryItem.user_id == user.id))).scalars().all()
    d = lambda o, cols: {c: (str(getattr(o, c)) if getattr(o, c) is not None else None) for c in cols}  # noqa: E731
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "purchases": [d(p, ("id", "title", "brand_name", "category_name", "size", "purchase_price", "total_cost", "purchase_date", "expected_sale_price")) for p in purchases],
        "sales": [d(x, ("id", "purchase_id", "sale_price", "selling_fees", "shipping_cost", "packaging_cost", "profit", "sale_date", "platform")) for x in sales],
        "expenses": [d(x, ("id", "spent_on", "kind", "amount", "note")) for x in expenses],
        "inventory": [d(x, ("id", "purchase_id", "stage", "listed_price", "min_price", "listed_at")) for x in inv],
    }  # fmt: skip
