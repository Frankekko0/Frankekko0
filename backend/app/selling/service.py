"""Database side of the selling cycle: the data the pure modules need, and the records they produce."""

from __future__ import annotations

import statistics
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InventoryItem, Listing, Opportunity, PredictionOutcome, Purchase, Sale, SoldSale
from app.intelligence import survival
from app.opportunities.queries import price_band
from app.profit.calculator import CostProfile, sale_revenue
from app.selling import learning, listing_draft, pricing
from app.selling.stages import STAGES

DEFECT_IT = {
    "stain": "macchia", "halo": "alone", "hole": "foro", "tear": "strappo", "fading": "scolorimento",
    "pilling": "pilling", "snag": "filo tirato", "seam": "cucitura", "deformation": "deformazione",
    "collar_cuffs": "colletto o polsini consumati", "print_damage": "stampa rovinata",
}  # fmt: skip
SOLD_LIMIT = 600
STATUS_FOR_STAGE = {
    "identified": "in_stock", "purchased": "in_stock", "arriving": "in_stock", "to_list": "in_stock",
    "listed": "listed", "sold": "sold", "returned": "returned", "unsold": "in_stock",
}  # fmt: skip
assert set(STATUS_FOR_STAGE) == set(STAGES)


async def segment_observations(
    session: AsyncSession, brand_id: int | None, category_id: int | None, reference: float, now: datetime
) -> list[survival.Obs]:
    """Sold listings (how long they took) and listings still on sale (censored) of the same segment."""
    if reference <= 0 or (brand_id is None and category_id is None):
        return []
    seg = []
    if brand_id is not None:
        seg.append(SoldSale.brand_id == brand_id)
    if category_id is not None:
        seg.append(SoldSale.category_id == category_id)
    sold = (
        await session.execute(
            select(SoldSale.days_to_sell, SoldSale.price_eur)
            .where(*seg, SoldSale.is_outlier.is_(False), SoldSale.days_to_sell.is_not(None))
            .limit(SOLD_LIMIT)
        )
    ).all()
    obs = [
        survival.Obs(max(0.5, float(d)), True, float(p) / reference)
        for d, p in sold
        if d is not None and float(d) > 0
    ]
    lseg = []
    if brand_id is not None:
        lseg.append(Listing.brand_id == brand_id)
    if category_id is not None:
        lseg.append(Listing.category_id == category_id)
    active = (
        await session.execute(
            select(Listing.price, Listing.first_seen_at)
            .where(*lseg, Listing.status == "active")
            .limit(SOLD_LIMIT)
        )
    ).all()
    for price, first_seen in active:
        days = (now - first_seen).total_seconds() / 86400
        if days >= 0.5:
            obs.append(survival.Obs(days, False, float(price) / reference))
    return obs


async def reference_price(session: AsyncSession, p: Purchase) -> float | None:
    """What this kind of item sells for: the forecast made at purchase, else the median of sold prices."""
    if p.expected_sale_price:
        return float(p.expected_sale_price)
    if p.brand_id is None and p.category_id is None:
        return None
    seg = []
    if p.brand_id is not None:
        seg.append(SoldSale.brand_id == p.brand_id)
    if p.category_id is not None:
        seg.append(SoldSale.category_id == p.category_id)
    prices = (
        (
            await session.execute(
                select(SoldSale.price_eur).where(*seg, SoldSale.is_outlier.is_(False)).limit(SOLD_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    return float(statistics.median(float(x) for x in prices)) if len(prices) >= 5 else None


def profit_function(p: Purchase, costs: CostProfile):
    cost = p.total_cost

    def profit_at(price: float) -> float:
        return float(sale_revenue(Decimal(str(round(price, 2))), costs).net - cost)

    return profit_at


async def resale_plan(
    session: AsyncSession, p: Purchase, costs: CostProfile, min_profit: float, now: datetime | None = None
) -> tuple[pricing.ResalePlan | None, survival.SurvivalFit, float | None]:
    now = now or datetime.now(UTC)
    ref = await reference_price(session, p)
    if ref is None:
        return None, survival.prior_fit(), None
    obs = await segment_observations(session, p.brand_id, p.category_id, ref, now)
    fit = survival.fit_hazard(obs)
    return (
        pricing.plan_resale(
            reference=ref, profit_at=profit_function(p, costs), fit=fit, min_profit=min_profit
        ),
        fit,
        ref,
    )


async def draft_for(session: AsyncSession, p: Purchase) -> listing_draft.ListingDraft:
    listing = await session.get(Listing, p.listing_id) if p.listing_id else None
    vision: dict[str, Any] = ((listing.identification or {}).get("vision") or {}) if listing else {}
    inspected = vision.get("analyzer") not in (None, "heuristic")
    defects = tuple(
        listing_draft.Defect(
            DEFECT_IT.get(d.get("kind", ""), str(d.get("kind", "difetto"))),
            d.get("zone"),
            (d["photo"] + 1) if isinstance(d.get("photo"), int) else None,
            d.get("severity", "minor"),
        )
        for d in vision.get("defects", [])
        if d.get("certainty") != "unverifiable"
    )
    facts = listing_draft.ItemFacts(
        brand=p.brand_name,
        model=listing.model_name if listing else None,
        category=p.category_name,
        size=p.size,
        color=listing.color if listing else None,
        material=listing.material if listing else None,
        condition=p.condition or "unknown",
        defects=defects,
        defects_checked=inspected,
        label_visible_in_photos=bool((vision.get("photo_quality") or {}).get("has_label_photo")),
        photo_roles=tuple(r.get("role", "") for r in vision.get("photo_roles", [])),
    )
    return listing_draft.build_draft(facts)


async def record_outcome(session: AsyncSession, sale: Sale, p: Purchase) -> PredictionOutcome | None:
    """Put what was forecast at purchase next to what happened. Idempotent per sale."""
    existing = (
        await session.execute(select(PredictionOutcome).where(PredictionOutcome.sale_id == sale.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    opp = await session.get(Opportunity, p.opportunity_id) if p.opportunity_id else None
    predicted_price = float(p.expected_sale_price) if p.expected_sale_price else None
    predicted_days = (
        float(opp.estimated_days_to_sell) if opp and opp.estimated_days_to_sell is not None else None
    )
    predicted_profit = float(opp.expected_profit) if opp and opp.expected_profit is not None else None
    out = PredictionOutcome(
        id=uuid.uuid4(),
        user_id=sale.user_id,
        sale_id=sale.id,
        purchase_id=p.id,
        opportunity_id=p.opportunity_id,
        brand_name=p.brand_name,
        category_name=p.category_name,
        price_band=price_band(float(p.purchase_price)),
        verdict_at_buy=opp.decision_verdict if opp else None,
        predicted_price=Decimal(str(predicted_price)) if predicted_price is not None else None,
        predicted_days=learning.to_decimal(predicted_days, "0.01"),
        predicted_profit=Decimal(str(predicted_profit)) if predicted_profit is not None else None,
        predicted_p_sale=opp.sale_probability if opp and opp.sale_probability is not None else None,
        actual_price=sale.sale_price,
        actual_days=sale.holding_days,
        actual_profit=sale.profit,
        price_error_pct=learning.to_decimal(
            learning.price_error_pct(predicted_price, float(sale.sale_price))
        ),
        days_error=learning.to_decimal(learning.days_error(predicted_days, float(sale.holding_days)), "0.01"),
    )
    session.add(out)
    return out


def set_stage(item: InventoryItem, stage: str, now: datetime) -> None:
    """Move an item (already checked) and keep the legacy ``status`` and the listing dates in step."""
    item.stage = stage
    item.status = STATUS_FOR_STAGE[stage]
    if stage == "listed" and item.listed_at is None:
        item.listed_at = now
    if stage in ("arriving", "to_list") and item.received_at is None and stage == "to_list":
        item.received_at = now


def add_price_event(item: InventoryItem, price: Decimal, reason: str, now: datetime) -> None:
    history = list(item.price_history or [])
    history.append({"at": now.isoformat(), "price": str(price), "reason": reason})
    item.price_history = history
    item.listed_price = price
    if item.initial_price is None:
        item.initial_price = price


def days_listed(item: InventoryItem, today: date | None = None) -> float:
    if item.listed_at is None:
        return 0.0
    return max(
        0.0,
        (
            (datetime.now(UTC) if today is None else datetime.combine(today, datetime.min.time(), UTC))
            - item.listed_at
        ).total_seconds()
        / 86400,
    )
