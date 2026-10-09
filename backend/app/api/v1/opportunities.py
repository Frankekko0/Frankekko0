"""Opportunities: feed with filters/presets, quick stats, deal detail, user states, AI analysis."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.ai.service import run_ai_analysis
from app.api.deps import DB, CurrentUser, Economics
from app.core.cache import NS_FEED, cache
from app.core.errors import NotFoundError
from app.db.models import Favorite, Opportunity
from app.opportunities.queries import OpportunityQueries, acquisition_lines, sale_lines
from app.profit.evaluation import (
    TAX_NOTE,
    CostStatus,
    Restoration,
    evaluate_deal,
    recommended_max_buy_price,
)
from app.schemas.common import Message, Page
from app.schemas.opportunity import (
    EvaluatedCostOut,
    FavoriteOut,
    FavoriteStateIn,
    OpportunityCard,
    OpportunityDetail,
    OpportunityFilters,
    ProfitCalcIn,
    ProfitCalcOut,
    QuickStats,
)

router = APIRouter(tags=["opportunities"])


@router.get("/opportunities", response_model=Page[OpportunityCard])
async def list_opportunities(
    filters: Annotated[OpportunityFilters, Query()], user: CurrentUser, econ: Economics, db: DB
) -> Any:
    """Opportunity feed. Profit/ROI filters and values use *your* cost profile."""

    async def load() -> dict[str, Any]:
        cards, total = await OpportunityQueries(db, user.id, econ).feed(filters)
        return Page[OpportunityCard](
            items=cards,
            total=total,
            page=filters.page,
            page_size=filters.page_size,
            has_more=filters.page * filters.page_size < total,
        ).model_dump(mode="json")

    return await cache.get_or_set(NS_FEED, (str(user.id), filters.model_dump(mode="json")), 20, load)


@router.get("/opportunities/stats", response_model=QuickStats)
async def quick_stats(user: CurrentUser, econ: Economics, db: DB) -> Any:
    async def load() -> dict[str, Any]:
        return await OpportunityQueries(db, user.id, econ).quick_stats()

    return await cache.get_or_set(NS_FEED, ("stats", str(user.id)), 30, load)


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityDetail)
async def get_opportunity(
    opportunity_id: uuid.UUID, user: CurrentUser, econ: Economics, db: DB
) -> OpportunityDetail:
    detail = await OpportunityQueries(db, user.id, econ).detail(opportunity_id)
    detail.provenance = (
        await db.execute(
            select(Opportunity.score_breakdown["provenance"]).where(Opportunity.id == opportunity_id)
        )
    ).scalar()
    return detail


async def _opportunity(db: DB, opportunity_id: uuid.UUID) -> Opportunity:
    opp = await db.get(Opportunity, opportunity_id)
    if opp is None:
        raise NotFoundError("Opportunità non trovata.")
    return opp


@router.put("/opportunities/{opportunity_id}/state", response_model=FavoriteOut)
async def set_state(
    opportunity_id: uuid.UUID, body: FavoriteStateIn, user: CurrentUser, db: DB
) -> FavoriteOut:
    """Save / ignore / mark purchased / watching / sold. Ignoring teaches the recommendation engine."""
    opp = await _opportunity(db, opportunity_id)
    now = datetime.now(UTC)
    stmt = pg_insert(Favorite).values(
        id=uuid.uuid4(),
        user_id=user.id,
        listing_id=opp.listing_id,
        opportunity_id=opp.id,
        state=body.state,
        note=body.note,
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "listing_id"],
        set_={"state": body.state, "note": body.note, "opportunity_id": opp.id, "updated_at": now},
    ).returning(Favorite)
    fav = (await db.execute(stmt)).scalar_one()
    await db.commit()
    await cache.bump(NS_FEED)
    return FavoriteOut(
        listing_id=fav.listing_id,
        opportunity_id=fav.opportunity_id,
        state=fav.state,
        note=fav.note,
        updated_at=fav.updated_at,
    )


@router.delete("/opportunities/{opportunity_id}/state", response_model=Message)
async def clear_state(opportunity_id: uuid.UUID, user: CurrentUser, db: DB) -> Message:
    opp = await _opportunity(db, opportunity_id)
    await db.execute(
        delete(Favorite).where(Favorite.user_id == user.id, Favorite.listing_id == opp.listing_id)
    )
    await db.commit()
    await cache.bump(NS_FEED)
    return Message(message="Stato rimosso.")


@router.post("/opportunities/{opportunity_id}/ai-analysis", response_model=dict[str, Any])
async def refresh_ai_analysis(opportunity_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    """Run the AI Deal Analyst (Claude when configured, rule-based otherwise)."""
    opp = await _opportunity(db, opportunity_id)
    analysis = await run_ai_analysis(db, opp)
    await db.commit()
    return analysis


@router.get("/favorites", response_model=list[FavoriteOut])
async def list_favorites(user: CurrentUser, db: DB, state: str | None = None) -> list[FavoriteOut]:
    stmt = select(Favorite).where(Favorite.user_id == user.id).order_by(Favorite.updated_at.desc())
    if state:
        stmt = stmt.where(Favorite.state == state)
    rows = (await db.execute(stmt.limit(500))).scalars().all()
    return [
        FavoriteOut(
            listing_id=f.listing_id,
            opportunity_id=f.opportunity_id,
            state=f.state,
            note=f.note,
            updated_at=f.updated_at,
        )
        for f in rows
    ]


@router.post("/profit/calculate", response_model=ProfitCalcOut, tags=["profit"])
async def calculate_profit(body: ProfitCalcIn, user: CurrentUser, econ: Economics) -> ProfitCalcOut:
    """Profit calculator with your configured costs (what-if purchase/sale prices)."""
    restoration = (
        Restoration(body.restoration_cost, CostStatus.ESTIMATED)
        if body.restoration_cost is not None
        else None
    )
    ev = evaluate_deal(
        body.purchase_price,
        body.sale_price,
        econ.costs,
        listing_shipping=body.shipping_fee,
        restoration=restoration,
        contingency_pct=body.contingency_pct,
        profile_saved=econ.profile_saved,
    )
    r = ev.result
    return ProfitCalcOut(
        total_acquisition_cost=r.acquisition.total,
        net_sale_revenue=r.sale.net,
        net_profit=r.net_profit,
        roi=r.roi,
        acquisition_breakdown=acquisition_lines(r.acquisition),
        sale_breakdown=sale_lines(r.sale),
        max_buy_price=recommended_max_buy_price(
            body.sale_price,
            econ.costs,
            econ.targets.min_profit,
            econ.targets.min_roi,
            listing_shipping=body.shipping_fee,
            restoration=r.acquisition.restoration,
            contingency_pct=body.contingency_pct,
        ),
        margin_on_sale=ev.margin_on_sale,
        break_even_price=ev.break_even_price,
        capital_tied_up=ev.capital_tied_up,
        cost_status=ev.cost_status.value,
        unknown_costs=list(ev.unknown_costs),
        gross_of_unknown_costs=ev.gross_of_unknown_costs,
        lines=[
            EvaluatedCostOut(
                key=c.key, label=c.label, amount=c.amount, status=c.status.value, source=c.source, side=c.side
            )
            for c in ev.lines
        ],
        taxes_included=False,
        taxes_note=TAX_NOTE,
    )
