"""My Flips: purchases, sales, inventory and portfolio analytics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.analytics.learning import recompute_user_affinities
from app.analytics.portfolio import portfolio_summary, sale_figures
from app.api.deps import DB, CurrentUser, Economics, UserEconomics
from app.core.cache import NS_FEED, cache
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.db.models import Brand, Category, Favorite, InventoryItem, Listing, Opportunity, Purchase, Sale
from app.domain.enums import FavoriteState, InventoryStatus
from app.identification.taxonomy import fold
from app.market.sold_sales import sync_own_records
from app.profit.calculator import acquisition_cost
from app.schemas.common import Message
from app.schemas.portfolio import FlipOut, PurchaseIn, PurchaseUpdate, SaleIn, SaleOut

log = get_logger(__name__)
router = APIRouter(tags=["portfolio"])


def flip_out(p: Purchase) -> FlipOut:
    sale = p.sale
    inv = p.inventory_item
    status = "sold" if sale else (inv.status if inv else InventoryStatus.IN_STOCK.value)
    return FlipOut(
        purchase_id=p.id,
        title=p.title,
        brand=p.brand_name,
        category=p.category_name,
        size=p.size,
        condition=p.condition,
        purchase_price=p.purchase_price,
        total_cost=p.total_cost,
        purchase_date=p.purchase_date,
        expected_sale_price=p.expected_sale_price,
        opportunity_id=p.opportunity_id,
        status=status,
        listed_price=inv.listed_price if inv else None,
        sale=SaleOut.model_validate(sale) if sale else None,
        profit=sale.profit if sale else None,
        roi=sale.roi if sale else None,
        holding_days=sale.holding_days if sale else None,
        notes=p.notes,
    )


async def _purchase(db: DB, user_id: uuid.UUID, purchase_id: uuid.UUID) -> Purchase:
    p = await db.get(Purchase, purchase_id)
    if p is None or p.user_id != user_id:
        raise NotFoundError("Acquisto non trovato.")
    return p


async def sync_own_safely(db: DB, purchase_ids: list[uuid.UUID]) -> None:
    """Own purchases and resales are the most reliable concluded sales: the price evidence
    (``sold_sales``) gets them right away. A failure is logged, never shown to the user: the
    30-minute evidence sync records them anyway."""
    try:
        await sync_own_records(db, purchase_ids)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        log.warning("portfolio.own_records_not_synced", error=type(exc).__name__, purchases=len(purchase_ids))


async def _refresh_learning(db: DB, user_id: uuid.UUID) -> None:
    await recompute_user_affinities(db, user_id)
    await db.commit()
    await cache.bump(NS_FEED)


@router.get("/flips", response_model=list[FlipOut])
async def list_flips(user: CurrentUser, db: DB, status: str | None = None) -> list[FlipOut]:
    rows = (
        (
            await db.execute(
                select(Purchase)
                .where(Purchase.user_id == user.id)
                .order_by(Purchase.purchase_date.desc(), Purchase.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    flips = [flip_out(p) for p in rows]
    return [f for f in flips if status is None or f.status == status]


@router.post("/purchases", response_model=FlipOut, status_code=201)
async def create_purchase(body: PurchaseIn, user: CurrentUser, econ: Economics, db: DB) -> FlipOut:
    purchase = await record_purchase(db, user.id, econ, body)
    await db.commit()
    await db.refresh(purchase)
    await _refresh_learning(db, user.id)
    await sync_own_safely(db, [purchase.id])
    await db.refresh(purchase)
    return flip_out(purchase)


async def record_purchase(db: DB, user_id: uuid.UUID, econ: UserEconomics, body: PurchaseIn) -> Purchase:
    """Record a purchase with its inventory item and mark the listing as purchased (no commit).
    Linked to an opportunity it inherits brand/category/size and expected resale."""
    listing: Listing | None = None
    opp: Opportunity | None = None
    if body.opportunity_id:
        opp = await db.get(Opportunity, body.opportunity_id)
        if opp is None:
            raise NotFoundError("Opportunità non trovata.")
        listing = await db.get(Listing, opp.listing_id)
    elif body.listing_id:
        listing = await db.get(Listing, body.listing_id)
    brand_id = listing.brand_id if listing else None
    category_id = listing.category_id if listing else None
    brand_name = body.brand
    if body.brand and brand_id is None:
        b = (
            await db.execute(select(Brand).where((Brand.slug == body.brand) | (Brand.name.ilike(body.brand))))
        ).scalar_one_or_none()
        if b:
            brand_id, brand_name = b.id, b.name
    elif brand_id is not None:
        b = await db.get(Brand, brand_id)
        brand_name = b.name if b else body.brand
    category_name = body.category
    if body.category and category_id is None:
        c = (
            await db.execute(select(Category).where(Category.slug == fold(body.category).replace(" ", "-")))
        ).scalar_one_or_none()
        if c:
            category_id, category_name = c.id, c.name_it
    elif category_id is not None:
        c = await db.get(Category, category_id)
        category_name = c.name_it if c else body.category

    shipping_listing = listing.shipping_fee if listing else None
    default_acq = acquisition_cost(body.purchase_price, econ.costs, shipping_listing)
    bp = body.buyer_protection_fee if body.buyer_protection_fee is not None else default_acq.buyer_protection
    shipping = body.shipping_cost if body.shipping_cost is not None else default_acq.shipping
    total = body.purchase_price + bp + shipping + body.other_costs
    purchase = Purchase(
        user_id=user_id,
        listing_id=listing.id if listing else None,
        opportunity_id=opp.id if opp else None,
        title=body.title,
        brand_id=brand_id,
        brand_name=brand_name,
        category_id=category_id,
        category_name=category_name,
        size=body.size or (listing.size_normalized if listing else None),
        condition=body.condition or (listing.condition if listing else None),
        purchase_price=body.purchase_price,
        buyer_protection_fee=bp,
        shipping_cost=shipping,
        other_costs=body.other_costs,
        total_cost=total,
        purchase_date=body.purchase_date,
        expected_sale_price=body.expected_sale_price or (opp.expected_sale_price if opp else None),
        notes=body.notes,
    )
    db.add(purchase)
    await db.flush()
    db.add(
        InventoryItem(
            user_id=user_id,
            purchase_id=purchase.id,
            status=InventoryStatus.IN_STOCK.value,
            estimated_value=purchase.expected_sale_price,
        )
    )
    if listing is not None:
        now = datetime.now(UTC)
        stmt = pg_insert(Favorite).values(
            id=uuid.uuid4(),
            user_id=user_id,
            listing_id=listing.id,
            opportunity_id=opp.id if opp else None,
            state=FavoriteState.PURCHASED.value,
            created_at=now,
            updated_at=now,
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["user_id", "listing_id"],
                set_={"state": FavoriteState.PURCHASED.value, "updated_at": now},
            )
        )
    return purchase


@router.patch("/purchases/{purchase_id}", response_model=FlipOut)
async def update_purchase(purchase_id: uuid.UUID, body: PurchaseUpdate, user: CurrentUser, db: DB) -> FlipOut:
    p = await _purchase(db, user.id, purchase_id)
    if body.title is not None:
        p.title = body.title
    if body.notes is not None:
        p.notes = body.notes
    if body.expected_sale_price is not None:
        p.expected_sale_price = body.expected_sale_price
    inv = p.inventory_item
    if inv is not None and p.sale is None:
        if body.inventory_status:
            inv.status = body.inventory_status
            if body.inventory_status == InventoryStatus.LISTED.value and inv.listed_at is None:
                inv.listed_at = datetime.now(UTC)
        if body.listed_price is not None:
            inv.listed_price = body.listed_price
        if body.expected_sale_price is not None:
            inv.estimated_value = body.expected_sale_price
    await db.commit()
    await sync_own_safely(db, [purchase_id])
    await db.refresh(p)
    return flip_out(p)


@router.delete("/purchases/{purchase_id}", response_model=Message)
async def delete_purchase(purchase_id: uuid.UUID, user: CurrentUser, db: DB) -> Message:
    p = await _purchase(db, user.id, purchase_id)
    await db.delete(p)
    await db.commit()  # its concluded-sale rows go with it (foreign key cascade)
    await _refresh_learning(db, user.id)
    return Message(message="Acquisto eliminato.")


@router.post("/sales", response_model=FlipOut, status_code=201)
async def create_sale(body: SaleIn, user: CurrentUser, db: DB) -> FlipOut:
    p = await _purchase(db, user.id, body.purchase_id)
    if p.sale is not None:
        raise ConflictError("Questo articolo risulta già venduto.", code="already_sold")
    if body.sale_date < p.purchase_date:
        raise AppError("La data di vendita non può precedere l'acquisto.", code="invalid_sale_date")
    fig = sale_figures(
        p.total_cost,
        body.sale_price,
        body.selling_fees,
        body.shipping_cost,
        body.packaging_cost,
        body.other_costs,
        p.purchase_date,
        body.sale_date,
    )
    sale = Sale(
        user_id=user.id,
        purchase_id=p.id,
        sale_price=body.sale_price,
        selling_fees=body.selling_fees,
        shipping_cost=body.shipping_cost,
        packaging_cost=body.packaging_cost,
        other_costs=body.other_costs,
        net_revenue=fig.net_revenue,
        profit=fig.profit,
        roi=fig.roi,
        holding_days=fig.holding_days,
        sale_date=body.sale_date,
        platform=body.platform,
        notes=body.notes,
    )
    db.add(sale)
    if p.inventory_item is not None:
        p.inventory_item.status = InventoryStatus.SOLD.value
    if p.listing_id is not None:
        fav = (
            await db.execute(
                select(Favorite).where(Favorite.user_id == user.id, Favorite.listing_id == p.listing_id)
            )
        ).scalar_one_or_none()
        if fav is not None:
            fav.state = FavoriteState.SOLD.value
    await db.commit()
    await _refresh_learning(db, user.id)
    await sync_own_safely(db, [p.id])
    await db.refresh(p)
    return flip_out(p)


@router.get("/sales", response_model=list[SaleOut])
async def list_sales(user: CurrentUser, db: DB) -> list[SaleOut]:
    rows = (
        (await db.execute(select(Sale).where(Sale.user_id == user.id).order_by(Sale.sale_date.desc())))
        .scalars()
        .all()
    )
    return [SaleOut.model_validate(s) for s in rows]


@router.delete("/sales/{sale_id}", response_model=Message)
async def delete_sale(sale_id: uuid.UUID, user: CurrentUser, db: DB) -> Message:
    sale = await db.get(Sale, sale_id)
    if sale is None or sale.user_id != user.id:
        raise NotFoundError("Vendita non trovata.")
    purchase_id = sale.purchase_id
    purchase = await db.get(Purchase, purchase_id)
    await db.delete(sale)
    if purchase is not None and purchase.inventory_item is not None:
        purchase.inventory_item.status = InventoryStatus.IN_STOCK.value
    await db.commit()
    await _refresh_learning(db, user.id)
    # The resale row went with the sale; the purchase counts again as a purchase.
    await sync_own_safely(db, [purchase_id])
    return Message(message="Vendita eliminata: l'articolo torna in inventario.")


@router.get("/inventory", response_model=list[FlipOut])
async def inventory(user: CurrentUser, db: DB) -> list[FlipOut]:
    rows = (
        (
            await db.execute(
                select(Purchase).where(Purchase.user_id == user.id).order_by(Purchase.purchase_date.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        flip_out(p)
        for p in rows
        if p.sale is None and (p.inventory_item is None or p.inventory_item.status != "returned")
    ]


@router.get("/analytics/portfolio", response_model=dict[str, Any], tags=["analytics"])
async def portfolio(user: CurrentUser, db: DB) -> dict[str, Any]:
    return await portfolio_summary(db, user.id)
