"""Selling cycle, accounting, learning from outcomes and the negotiation assistant."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import pairwise
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import Field, field_validator
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, Economics
from app.core.errors import AppError, NotFoundError
from app.db.models import (
    BusinessGoals,
    Expense,
    InventoryItem,
    Listing,
    ListingPriceHistory,
    Opportunity,
    PredictionOutcome,
    Purchase,
    Sale,
)
from app.negotiation import assistant
from app.profit.calculator import max_buy_price, profit_for
from app.schemas.common import Message, Money, Schema
from app.selling import accounting, learning, offers, repricing, service
from app.selling.stages import LABELS, StageError, check_move

router = APIRouter(tags=["selling"])


# ------------------------------------------------------------------ inventory by stage
class InventoryPatch(Schema):
    stage: str | None = None
    listed_price: Money | None = Field(default=None, gt=0, le=100_000)
    min_price: Money | None = Field(default=None, gt=0, le=100_000)
    views: int | None = Field(default=None, ge=0)
    favourites: int | None = Field(default=None, ge=0)
    listing_url: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=120)

    @field_validator("listing_url")
    @classmethod
    def _web_address(cls, v: str | None) -> str | None:
        """Only http(s) links: the field is shown as a link, and ``javascript:`` or ``data:`` would run in the page."""
        if v and not v.strip().lower().startswith(("https://", "http://")):
            raise ValueError("serve un indirizzo che inizi con http:// o https://")
        return v.strip() if v else v


async def _item(db: DB, user_id: uuid.UUID, purchase_id: uuid.UUID) -> tuple[Purchase, InventoryItem]:
    p = await db.get(Purchase, purchase_id)
    if p is None or p.user_id != user_id:
        raise NotFoundError("Acquisto non trovato.")
    if p.inventory_item is None:
        raise NotFoundError("Questo acquisto non ha una scheda di inventario.")
    return p, p.inventory_item


def _item_out(p: Purchase, i: InventoryItem) -> dict[str, Any]:
    return {
        "purchase_id": str(p.id),
        "title": p.title,
        "brand": p.brand_name,
        "stage": i.stage,
        "stage_label": LABELS[i.stage],
        "total_cost": float(p.total_cost),
        "listed_price": float(i.listed_price) if i.listed_price is not None else None,
        "initial_price": float(i.initial_price) if i.initial_price is not None else None,
        "min_price": float(i.min_price) if i.min_price is not None else None,
        "listed_at": i.listed_at.isoformat() if i.listed_at else None,
        "days_listed": round(service.days_listed(i), 1) if i.stage == "listed" else None,
        "views": i.views,
        "favourites": i.favourites,
        "listing_url": i.listing_url,
        "price_history": i.price_history or [],
        "expected_sale_price": float(p.expected_sale_price) if p.expected_sale_price is not None else None,
    }


@router.get("/selling/inventory", response_model=dict[str, Any])
async def selling_inventory(user: CurrentUser, db: DB) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(Purchase, InventoryItem)
            .join(InventoryItem, InventoryItem.purchase_id == Purchase.id)
            .where(Purchase.user_id == user.id)
            .order_by(Purchase.purchase_date.desc())
        )
    ).all()
    items = [_item_out(p, i) for p, i in rows]
    counts: dict[str, int] = {}
    for it in items:
        counts[it["stage"]] = counts.get(it["stage"], 0) + 1
    return {"items": items, "by_stage": counts, "stages": list(LABELS.items())}


@router.patch("/selling/inventory/{purchase_id}", response_model=dict[str, Any])
async def patch_inventory(
    purchase_id: uuid.UUID, body: InventoryPatch, user: CurrentUser, db: DB
) -> dict[str, Any]:
    p, item = await _item(db, user.id, purchase_id)
    now = datetime.now(UTC)
    if p.sale is not None and body.stage not in (None, "returned"):
        raise AppError("L'articolo è già venduto.", code="already_sold")
    if body.stage is not None and body.stage != item.stage:
        try:
            check_move(item.stage, body.stage)
        except StageError as exc:
            raise AppError(str(exc), code="invalid_stage_move") from exc
        service.set_stage(item, body.stage, now)
    if body.min_price is not None:
        item.min_price = body.min_price
    if body.listed_price is not None and body.listed_price != item.listed_price:
        service.add_price_event(item, body.listed_price, body.reason or "prezzo impostato", now)
    if body.views is not None:
        item.views = body.views
    if body.favourites is not None:
        item.favourites = body.favourites
    if body.listing_url is not None:
        item.listing_url = body.listing_url or None
    await db.commit()
    return _item_out(p, item)


@router.get("/selling/inventory/{purchase_id}/plan", response_model=dict[str, Any])
async def selling_plan(purchase_id: uuid.UUID, user: CurrentUser, econ: Economics, db: DB) -> dict[str, Any]:
    """The resale listing draft, the price and markdown plan, and what to do about the price today."""
    p, item = await _item(db, user.id, purchase_id)
    min_profit = float(econ.targets.min_profit)
    plan, fit, ref = await service.resale_plan(db, p, econ.costs, min_profit)
    draft = await service.draft_for(db, p)
    advice = None
    if plan is not None and item.stage == "listed" and item.listed_price is not None:
        floor = max(plan.floor, float(item.min_price)) if item.min_price is not None else plan.floor
        advice = repricing.advise(
            days_listed=service.days_listed(item),
            asking=float(item.listed_price),
            floor=floor,
            markdowns=plan.markdowns,
            views=item.views,
            favourites=item.favourites,
        ).as_dict()
    return {
        "item": _item_out(p, item),
        "reference_price": ref,
        "draft": draft.as_dict(),
        "plan": plan.as_dict() if plan else None,
        "plan_unavailable": None
        if plan
        else (
            "Nessun prezzo di riferimento: servono vendite di articoli simili o una stima fatta all'acquisto."
            if ref is None
            else "A nessun prezzo di mercato si raggiunge il profitto minimo."
        ),
        "survival": {"basis": fit.source, "reliable": fit.reliable, "events": fit.events, "n": fit.n},
        "reprice": advice,
    }


class OfferIn(Schema):
    offer: Money = Field(gt=0, le=100_000)
    buyer_name: str | None = Field(default=None, max_length=40)


@router.post("/selling/inventory/{purchase_id}/offer", response_model=dict[str, Any])
async def evaluate_buyer_offer(
    purchase_id: uuid.UUID, body: OfferIn, user: CurrentUser, econ: Economics, db: DB
) -> dict[str, Any]:
    p, item = await _item(db, user.id, purchase_id)
    if item.stage != "listed" or item.listed_price is None:
        raise AppError("L'articolo non è pubblicato con un prezzo.", code="not_listed")
    plan, fit, ref = await service.resale_plan(db, p, econ.costs, float(econ.targets.min_profit))
    if plan is None or ref is None:
        raise AppError("Senza un prezzo di riferimento non posso valutare l'offerta.", code="no_reference")
    floor = max(plan.floor, float(item.min_price)) if item.min_price is not None else plan.floor
    decision = offers.evaluate_offer(
        offer=float(body.offer),
        asking=float(item.listed_price),
        cost=float(p.total_cost),
        floor=floor,
        reference=ref,
        profit_at=service.profit_function(p, econ.costs),
        fit=fit,
        buyer_name=body.buyer_name,
    )
    return {**decision.as_dict(), "floor": floor, "asking": float(item.listed_price)}


# ------------------------------------------------------------------ accounting
class ExpenseIn(Schema):
    spent_on: date
    kind: Literal["materials", "packaging", "shipping", "labor", "refurb", "fees", "other"]
    amount: Money = Field(ge=0, le=100_000)
    note: str | None = Field(default=None, max_length=200)
    purchase_id: uuid.UUID | None = None


@router.post("/accounting/expenses", response_model=dict[str, Any], status_code=201)
async def add_expense(body: ExpenseIn, user: CurrentUser, db: DB) -> dict[str, Any]:
    if body.purchase_id is not None:
        owner = await db.get(Purchase, body.purchase_id)
        if owner is None or owner.user_id != user.id:
            raise NotFoundError("Acquisto non trovato.")
    e = Expense(user_id=user.id, **body.model_dump())
    db.add(e)
    await db.commit()
    return {"id": str(e.id), **body.model_dump(mode="json")}


@router.get("/accounting/expenses", response_model=list[dict[str, Any]])
async def list_expenses(user: CurrentUser, db: DB) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                select(Expense).where(Expense.user_id == user.id).order_by(Expense.spent_on.desc()).limit(500)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(e.id),
            "spent_on": e.spent_on.isoformat(),
            "kind": e.kind,
            "amount": float(e.amount),
            "note": e.note,
        }
        for e in rows
    ]


@router.delete("/accounting/expenses/{expense_id}", response_model=Message)
async def delete_expense(expense_id: uuid.UUID, user: CurrentUser, db: DB) -> Message:
    e = await db.get(Expense, expense_id)
    if e is None or e.user_id != user.id:
        raise NotFoundError("Spesa non trovata.")
    await db.delete(e)
    await db.commit()
    return Message(message="Spesa eliminata.")


async def load_books(
    db: DB, user_id: uuid.UUID
) -> tuple[list[accounting.PurchaseRec], list[accounting.SaleRec], list[accounting.ExpenseRec]]:
    purchases = (await db.execute(select(Purchase).where(Purchase.user_id == user_id))).scalars().all()
    sales = (await db.execute(select(Sale).where(Sale.user_id == user_id))).scalars().all()
    expenses = (await db.execute(select(Expense).where(Expense.user_id == user_id))).scalars().all()
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


Start = Annotated[date | None, Query(description="First day (default: January 1st of this year)")]
End = Annotated[date | None, Query(description="Last day (default: today)")]


def _period(start: date | None, end: date | None) -> tuple[date, date]:
    today = datetime.now(UTC).date()
    s, e = start or date(today.year, 1, 1), end or today
    if s > e:
        raise AppError("La data iniziale è dopo quella finale.", code="invalid_period")
    return s, e


@router.get("/accounting/summary", response_model=dict[str, Any])
async def accounting_summary(
    user: CurrentUser, db: DB, start: Start = None, end: End = None
) -> dict[str, Any]:
    s, e = _period(start, end)
    p, sl, ex = await load_books(db, user.id)
    return _plain(accounting.summarize(p, sl, ex, s, e))


@router.get("/accounting/ledger")
async def accounting_ledger(
    user: CurrentUser, db: DB, start: Start = None, end: End = None, format: Literal["json", "csv"] = "json"
) -> Any:
    s, e = _period(start, end)
    p, sl, ex = await load_books(db, user.id)
    rows = accounting.build_ledger(p, sl, ex, s, e)
    if format == "csv":
        return Response(
            accounting.to_csv(rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="registro-{s}-{e}.csv"'},
        )
    return [
        {
            "on": r.on.isoformat(),
            "kind": r.kind,
            "ref": r.ref,
            "description": r.description,
            "amount": float(r.amount),
        }
        for r in rows
    ]


@router.get("/accounting/accountant", response_model=dict[str, Any])
async def accountant(user: CurrentUser, db: DB, year: int | None = None) -> dict[str, Any]:
    """The summary for the accountant: realised profit, cash flow, stock at cost, the user's own thresholds."""
    y = year or datetime.now(UTC).year
    p, sl, ex = await load_books(db, user.id)
    summary = accounting.summarize(p, sl, ex, date(y, 1, 1), date(y, 12, 31))
    goals = await db.get(BusinessGoals, user.id)
    return _plain(
        accounting.accountant_report(
            summary,
            goals.holder_status if goals else "private",
            list(goals.tax_thresholds) if goals else [],
            summary["revenue"],
        )
    )


def _plain(obj: Any) -> Any:
    """Decimals to numbers, for JSON."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


# ------------------------------------------------------------------ forecast against reality
@router.get("/learning/outcomes", response_model=dict[str, Any])
async def learning_outcomes(user: CurrentUser, db: DB) -> dict[str, Any]:
    rows = (
        (
            await db.execute(
                select(PredictionOutcome)
                .where(PredictionOutcome.user_id == user.id)
                .order_by(PredictionOutcome.created_at)
            )
        )
        .scalars()
        .all()
    )
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    data = [
        learning.OutcomeRow(
            o.brand_name, o.category_name, f(o.predicted_price), f(o.predicted_days), f(o.predicted_profit),
            float(o.actual_price), float(o.actual_days), float(o.actual_profit),
        )
        for o in rows
    ]  # fmt: skip
    return {**learning.accuracy(data), "sales": len(rows)}


# ------------------------------------------------------------------ the negotiation assistant (§4.16)
@router.get("/opportunities/{opportunity_id}/negotiation", response_model=dict[str, Any])
async def negotiation(
    opportunity_id: uuid.UUID, user: CurrentUser, econ: Economics, db: DB
) -> dict[str, Any]:
    o = await db.get(Opportunity, opportunity_id)
    if o is None:
        raise NotFoundError("Opportunità non trovata.")
    li = await db.get(Listing, o.listing_id)
    if li is None:
        raise NotFoundError("Annuncio non trovato.")
    asked = o.listing_price
    sale = o.expected_sale_price
    max_buy = (
        max_buy_price(sale, econ.costs, econ.targets.min_profit, econ.targets.min_roi, li.shipping_fee)
        if sale
        else None
    )
    ideal = (o.decision or {}).get("threshold_price")

    def profit_at(price: float) -> tuple[float, float | None]:
        if sale is None:
            return 0.0, None
        r = profit_for(
            Decimal(str(round(price, 2))), sale, econ.costs, li.shipping_fee, li.buyer_protection_fee
        )
        return float(r.net_profit), float(r.roi) if r.roi is not None else None

    now = datetime.now(UTC)
    start = li.published_at or li.first_seen_at
    drops = (
        (
            await db.execute(
                select(ListingPriceHistory.price)
                .where(ListingPriceHistory.listing_id == li.id)
                .order_by(ListingPriceHistory.observed_at)
            )
        )
        .scalars()
        .all()
    )
    n_drops = sum(1 for a, b in pairwise(drops) if b < a)
    others = (
        (
            await db.execute(
                select(func.count())
                .select_from(Listing)
                .where(Listing.seller_id == li.seller_id, Listing.status == "active", Listing.id != li.id)
            )
        ).scalar_one()
        if li.seller_id
        else 0
    )
    sold_ok = (o.sold_comparables_count or 0) >= 3 and o.fair_market_value is not None
    plan = assistant.build_plan(
        title=li.title,
        asked=float(asked),
        profit_at=profit_at,
        max_buy=float(max_buy) if max_buy is not None else None,
        ideal_offer=float(ideal) if ideal else (float(max_buy) if max_buy is not None else None),
        similar_sold_median=float(o.fair_market_value)
        if sold_ok and o.fair_market_value is not None
        else None,
        signals=assistant.SellerSignals(
            days_online=(now - start).total_seconds() / 86400,
            price_drops=n_drops,
            favourites=li.favourite_count,
            views=li.view_count,
            bundle_possible=others >= 2,
        ),
    )
    return {"opportunity_id": str(o.id), **plan.as_dict()}


# ------------------------------------------------------------------ learning on real records
@router.get("/learning/report", response_model=dict[str, Any])
async def learning_report(
    user: CurrentUser, econ: Economics, db: DB, register: bool = False
) -> dict[str, Any]:
    """False negatives, drift, probability calibration and the baseline comparison, on real records.
    What cannot be measured says so. ``register`` writes the evaluations to the experiment registry."""
    from app.intelligence import learning_report as lr

    args = (db, user.id, float(econ.targets.min_profit), float(econ.targets.min_roi))
    out = await (lr.run_and_register(*args) if register else _built(lr, *args))
    if register:
        await db.commit()
    return out


async def _built(lr: Any, db: Any, user_id: uuid.UUID, min_profit: float, min_roi: float) -> dict[str, Any]:
    return lr._plain(await lr.build(db, user_id, min_profit, min_roi))


@router.get("/learning/experiments", response_model=list[dict[str, Any]])
async def experiments(user: CurrentUser, db: DB) -> list[dict[str, Any]]:
    from app.db.models import Experiment

    rows = (
        (
            await db.execute(
                select(Experiment)
                .where(Experiment.user_id == user.id)
                .order_by(Experiment.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(e.id),
            "name": e.name,
            "hypothesis": e.hypothesis,
            "kind": e.kind,
            "status": e.status,
            "config": e.config,
            "created_at": e.created_at.isoformat(),
        }
        for e in rows
    ]


@router.get("/selling/liquidation", response_model=dict[str, Any])
async def liquidation(user: CurrentUser, econ: Economics, db: DB, threshold_days: int = 45) -> dict[str, Any]:
    """Items that have sat too long: a firmer markdown (down to the floor), or a bundle, to free the capital."""
    rows = (
        await db.execute(
            select(Purchase, InventoryItem)
            .join(InventoryItem, InventoryItem.purchase_id == Purchase.id)
            .where(Purchase.user_id == user.id, InventoryItem.stage.in_(("listed", "unsold")))
        )
    ).all()
    out: list[dict[str, Any]] = []
    for p, i in rows:
        held = (datetime.now(UTC).date() - p.purchase_date).days
        if held < threshold_days or p.sale is not None:
            continue
        plan, _fit, _ref = await service.resale_plan(db, p, econ.costs, float(econ.targets.min_profit))
        floor = float(i.min_price) if i.min_price is not None else (plan.floor if plan else None)
        out.append(
            {
                "purchase_id": str(p.id), "title": p.title, "days_held": held, "cost": float(p.total_cost),
                "listed_price": float(i.listed_price) if i.listed_price is not None else None, "floor": floor,
                "action": "ribasso fino al minimo, oppure inseriscilo in un lotto" if floor is not None else "nessun prezzo di riferimento: valuta un lotto o la rimozione",
                "capital_freed": float(p.total_cost),
            }
        )  # fmt: skip
    out.sort(key=lambda x: -x["days_held"])
    return {
        "threshold_days": threshold_days,
        "items": out,
        "capital_tied_up": round(sum(x["cost"] for x in out), 2),
    }
