"""Favourites and purchases on Vinted, started from FlipFinder and done by the browser extension
in the user's own session (one click, one action). Here they are only recorded: the server never
acts on Vinted and never sees the user's Vinted cookies or tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import UserEconomics
from app.db.models import Listing, MarketplaceAction, Opportunity, Purchase
from app.profit.calculator import acquisition_cost
from app.schemas.portfolio import PurchaseIn

KINDS = ("favourite", "checkout_opened", "purchased")


async def vinted_state(session: AsyncSession, user_id: uuid.UUID, listing_id: uuid.UUID) -> dict[str, Any]:
    """Latest favourite state, checkout opened and purchase recorded for this listing."""
    rows = (
        await session.execute(
            select(MarketplaceAction)
            .where(MarketplaceAction.user_id == user_id, MarketplaceAction.listing_id == listing_id)
            .order_by(MarketplaceAction.created_at.desc())
        )
    ).scalars()
    latest: dict[str, MarketplaceAction] = {}
    for a in rows:
        latest.setdefault(a.kind, a)

    def out(a: MarketplaceAction | None) -> dict[str, Any] | None:
        if a is None:
            return None
        return {
            "value": a.value,
            "price": float(a.price) if a.price is not None else None,
            "source": a.source,
            "at": a.created_at.isoformat(),
            "detail": a.detail or {},
        }

    return {k: out(latest.get(k)) for k in KINDS}


async def record_action(
    session: AsyncSession,
    user_id: uuid.UUID,
    econ: UserEconomics,
    listing: Listing,
    kind: str,
    *,
    value: bool | None = None,
    price: Decimal | None = None,
    source: str = "click",
    detail: dict[str, Any] | None = None,
) -> MarketplaceAction:
    action = MarketplaceAction(
        id=uuid.uuid4(),
        user_id=user_id,
        listing_id=listing.id,
        kind=kind,
        value=value,
        price=price,
        source=source,
        detail=detail or None,
        created_at=datetime.now(UTC),
    )
    session.add(action)
    if kind == "purchased":
        await _purchase_once(session, user_id, econ, listing, price, detail or {})
    await session.flush()
    return action


async def _purchase_once(
    session: AsyncSession,
    user_id: uuid.UUID,
    econ: UserEconomics,
    listing: Listing,
    paid: Decimal | None,
    detail: dict[str, Any],
) -> None:
    """The purchase in My Flips, once per listing, with the price paid when it is known."""
    from app.api.v1.portfolio import record_purchase

    existing = (
        await session.execute(
            select(Purchase.id).where(Purchase.user_id == user_id, Purchase.listing_id == listing.id)
        )
    ).first()
    if existing:
        return
    item_price = Decimal(str(detail.get("item_price") or listing.price))
    default = acquisition_cost(item_price, econ.costs, listing.shipping_fee, listing.buyer_protection_fee)
    shipping = None
    if paid is not None and paid >= item_price + default.buyer_protection:
        # Total paid known: what is not price or buyer protection is shipping (and extras).
        shipping = (paid - item_price - default.buyer_protection).quantize(Decimal("0.01"))
    opp_id = (
        await session.execute(select(Opportunity.id).where(Opportunity.listing_id == listing.id))
    ).scalar_one_or_none()
    await record_purchase(
        session,  # type: ignore[arg-type]
        user_id,
        econ,
        PurchaseIn(
            title=listing.title[:300],
            opportunity_id=opp_id,
            listing_id=None if opp_id else listing.id,
            purchase_price=item_price,
            buyer_protection_fee=default.buyer_protection,
            shipping_cost=shipping,
            purchase_date=datetime.now(UTC).date(),
            notes=f"Acquistato su Vinted dal tasto Acquista di FlipFinder; totale pagato €{paid}."
            if paid
            else None,
        ),
    )
