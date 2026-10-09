"""The purchase plan: what to buy with the user's budget (exact knapsack) and where the capital stands."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select

from app.analytics.portfolio import portfolio_summary
from app.api.deps import DB, CurrentUser, Economics
from app.db.models import Listing, ListingImage, Opportunity
from app.decision.allocation import Candidate, CapitalRules, allocate_capital
from app.decision.engine import DecisionVerdict
from app.schemas.common import Money, Schema

router = APIRouter(prefix="/plan", tags=["plan"])

POOL = 60  # best candidates by risk-adjusted profit that compete for the budget


class PlanItem(Schema):
    opportunity_id: uuid.UUID
    title: str
    image_url: str | None
    verdict: str
    price: Money
    total_cost: Money
    risk_adjusted_profit: Money | None
    risk_score: int
    last_seen_at: str


class PlanOut(Schema):
    budget: Money | None
    max_owned_items: int | None
    invested: Money = Field(description="Cost of what you hold and have not sold yet (from your purchases)")
    available: Money | None = Field(
        description="Budget minus the cost of what you hold; None without a budget"
    )
    owned_items: int
    realized_profit: Money = Field(description="Profit of closed sales only")
    potential_profit: Money = Field(
        description="Risk-adjusted profit of the plan: a forecast, never a result"
    )
    selected: list[PlanItem]
    total_cost: Money
    left: Money | None
    considered: int
    not_selected: list[dict[str, str]]
    reason: str | None = None
    method: str = "zaino 0/1 esatto sul profitto corretto per il rischio"


@router.get("", response_model=PlanOut)
async def plan(user: CurrentUser, econ: Economics, db: DB) -> PlanOut:
    """Which purchases fit the budget best. Only STRONG BUY and BUY compete (a NEGOTIATE needs a lower
    price first); the profit counted is risk-adjusted, and what you already hold is taken out of the budget."""
    prefs = econ.preferences
    budget = Decimal(prefs.total_budget) if prefs and prefs.total_budget else None
    max_items = prefs.max_owned_items if prefs else None
    book = await portfolio_summary(db, user.id)
    invested = Decimal(str(book["inventory_cost"]))
    owned = int(book["inventory_items"])
    realized = Decimal(str(book["profit"]))
    available = budget - invested if budget is not None else None

    rows = (
        await db.execute(
            select(Opportunity, Listing)
            .join(Listing, Listing.id == Opportunity.listing_id)
            .where(
                Opportunity.is_active.is_(True),
                Opportunity.decision_verdict.in_(
                    [DecisionVerdict.STRONG_BUY.value, DecisionVerdict.BUY.value]
                ),
                Opportunity.risk_adjusted_profit.is_not(None),
            )
            .order_by(Opportunity.risk_adjusted_profit.desc())
            .limit(POOL)
        )
    ).all()
    by_id = {str(o.id): (o, li) for o, li in rows}
    cands = [
        Candidate(
            id=str(o.id),
            cost=o.total_acquisition_cost,
            value=float(o.risk_adjusted_profit or 0),
            verdict=DecisionVerdict(str(o.decision_verdict)),
            risk=o.risk_score,
        )
        for o, _ in rows
    ]

    reason: str | None = None
    out = None
    if budget is None:
        reason = "Imposta un budget in Impostazioni per vedere cosa conviene comprare."
    elif available is not None and available <= 0:
        reason = "Il capitale è già tutto impegnato nelle giacenze."
    elif max_items is not None and owned >= max_items:
        reason = f"Hai già {owned} articoli in giacenza: il limite è {max_items}."
    elif not cands:
        reason = "Nessun Acquisto o Acquisto forte verificato al momento."
    else:
        slots = max_items - owned if max_items is not None else None
        out = allocate_capital(
            cands,
            CapitalRules(
                budget=available or Decimal(0),
                max_per_item=Decimal(prefs.max_purchase_price)
                if prefs and prefs.max_purchase_price
                else None,
                max_items=slots,
                max_risk=prefs.max_risk_score if prefs else None,
            ),
        )
        if not out.selected:
            reason = "Con questo budget e questi limiti nessun acquisto verificato è conveniente."

    selected: list[PlanItem] = []
    if out is not None and out.selected:
        images = dict(
            (
                await db.execute(
                    select(ListingImage.listing_id, ListingImage.url).where(
                        ListingImage.listing_id.in_([by_id[c.id][1].id for c in out.selected]),
                        ListingImage.removed_at.is_(None),
                        ListingImage.position == 0,
                    )
                )
            ).all()
        )
        for c in out.selected:
            o, li = by_id[c.id]
            selected.append(
                PlanItem(
                    opportunity_id=o.id,
                    title=li.title,
                    image_url=images.get(li.id),
                    verdict=str(o.decision_verdict),
                    price=o.listing_price,
                    total_cost=o.total_acquisition_cost,
                    risk_adjusted_profit=o.risk_adjusted_profit,
                    risk_score=o.risk_score,
                    last_seen_at=li.last_seen_at.isoformat(),
                )
            )
    return PlanOut(
        budget=budget,
        max_owned_items=max_items,
        invested=invested,
        available=available,
        owned_items=owned,
        realized_profit=realized,
        potential_profit=Decimal(str(round(out.total_value, 2))) if out else Decimal(0),
        selected=selected,
        total_cost=out.total_cost if out else Decimal(0),
        left=out.left if out else None,
        considered=len(cands),
        not_selected=[{"opportunity_id": i, "why": why} for i, why in (out.rejected if out else [])][:20],
        reason=reason,
    )
