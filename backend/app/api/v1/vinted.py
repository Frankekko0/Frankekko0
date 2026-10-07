"""Vinted favourites and purchases: state per listing, recorded actions."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from app.acquisition.vinted_actions import record_action, vinted_state
from app.api.deps import DB, CaptureEconomics, CaptureUser, CurrentUser, Economics
from app.api.v1.portfolio import sync_own_safely
from app.core.cache import NS_FEED, cache
from app.core.errors import NotFoundError
from app.db.models import Listing, Purchase
from app.schemas.vinted import CaptureVintedActionIn, VintedActionIn

router = APIRouter(tags=["vinted"])


async def _sync_purchase(db: DB, user_id: uuid.UUID, listing_id: uuid.UUID, kind: str) -> None:
    """A purchase recorded from Vinted joins the concluded sales right away (see portfolio)."""
    if kind != "purchased":
        return
    ids = list(
        (
            await db.execute(
                select(Purchase.id).where(Purchase.user_id == user_id, Purchase.listing_id == listing_id)
            )
        ).scalars()
    )
    await sync_own_safely(db, ids)


@router.get("/listings/{listing_id}/vinted", response_model=dict[str, Any])
async def get_state(listing_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    if await db.get(Listing, listing_id) is None:
        raise NotFoundError("Annuncio non trovato.")
    return await vinted_state(db, user.id, listing_id)


@router.post("/listings/{listing_id}/vinted", response_model=dict[str, Any], status_code=201)
async def add_action(
    listing_id: uuid.UUID, body: VintedActionIn, user: CurrentUser, econ: Economics, db: DB
) -> dict[str, Any]:
    """Record what the extension did on Vinted after the user's click (or a manual confirmation)."""
    listing = await db.get(Listing, listing_id)
    if listing is None:
        raise NotFoundError("Annuncio non trovato.")
    await record_action(
        db,
        user.id,
        econ,
        listing,
        body.kind,
        value=body.value,
        price=body.price,
        source=body.source,
        detail=body.detail,
    )
    await db.commit()
    await _sync_purchase(db, user.id, listing.id, body.kind)
    await cache.bump(NS_FEED)
    return await vinted_state(db, user.id, listing_id)


@router.post("/capture/vinted-actions", response_model=dict[str, Any], status_code=201)
async def capture_action(
    body: CaptureVintedActionIn, user: CaptureUser, econ: CaptureEconomics, db: DB
) -> dict[str, Any]:
    """From the extension: a favourite state seen on an item page the user opened, or a purchase
    completed in the checkout the user confirmed."""
    listing = (
        await db.execute(
            select(Listing).where(
                Listing.provider == "vinted",
                Listing.external_id == body.vinted_id,
                Listing.duplicate_of_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if listing is None:
        raise NotFoundError("Annuncio non presente in FlipFinder.")
    current = await vinted_state(db, user.id, listing.id)
    if (
        body.kind == "favourite"
        and body.source == "page"
        and (current["favourite"] or {}).get("value") is body.value
    ):
        return {"listing_id": str(listing.id), **current}  # seen again, unchanged
    await record_action(
        db,
        user.id,
        econ,
        listing,
        body.kind,
        value=body.value,
        price=body.price,
        source=body.source,
        detail=body.detail,
    )
    await db.commit()
    await _sync_purchase(db, user.id, listing.id, body.kind)
    await cache.bump(NS_FEED)
    return {"listing_id": str(listing.id), **(await vinted_state(db, user.id, listing.id))}
