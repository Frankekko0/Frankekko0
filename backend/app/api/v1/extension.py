"""Browser extension: pairing keys, shared parser configuration and captures.

The extension sends what the user's own browser shows on Vinted pages they open (cards seen
while scrolling, item pages, deep analyses they ask for) and gets back a quick evaluation per
item. It authenticates with a FlipFinder key created here; Vinted cookies or tokens are never
read or sent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy import func, select

from app.acquisition.evaluations import quick_evaluations
from app.acquisition.identity import listing_identity
from app.acquisition.market_cache import build_market_cache, identify_card
from app.acquisition.service import import_links
from app.acquisition.vinted_parser import config_json, load_config
from app.api.deps import DB, CaptureEconomics, CaptureUser, CurrentUser
from app.api.v1.items import item_detail
from app.api.v1.listings import manual_to_provider, persist_observations
from app.core.cache import NS_FEED, cache
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.rate_limit import RateLimit
from app.core.security import hash_api_key, new_api_key
from app.db.models import ApiKey, Listing, Opportunity, SystemState
from app.domain.enums import AcquisitionMode, CaptureLevel
from app.ingestion.catalog import load_catalog
from app.market.model_stats import lookup_stats
from app.media.archive import schedule_archive
from app.schemas.extension import (
    ApiKeyCreated,
    ApiKeyIn,
    ApiKeyOut,
    CaptureCardsIn,
    CaptureCardsOut,
    CaptureItemIn,
    CaptureItemOut,
    EvaluationsIn,
    PageStatsIn,
    PageStatsOut,
    QuickEval,
    TrackIn,
)
from app.tracking.actions import set_tracked
from app.tracking.summary import analysis_summary
from app.workers.vision_queue import queue_vision_safely, vision_order

log = get_logger(__name__)
router = APIRouter(tags=["extension"])

MAX_ACTIVE_KEYS = 10
capture_limit = RateLimit("capture", per_minute=90)
item_limit = RateLimit("capture-item", per_minute=40)
page_stats_limit = RateLimit("page-stats", per_minute=60)


# ------------------------------------------------------------------ pairing (web app session)
@router.get("/extension/keys", response_model=list[ApiKeyOut])
async def list_keys(user: CurrentUser, db: DB) -> list[ApiKey]:
    return list(
        (
            await db.execute(
                select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )


@router.post("/extension/keys", response_model=ApiKeyCreated, status_code=201)
async def create_key(body: ApiKeyIn, user: CurrentUser, db: DB) -> ApiKeyCreated:
    """A new key for the extension. The key is returned once; only its hash is stored."""
    active = (
        await db.execute(select(func.count()).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None)))
    ).scalar_one()
    if active >= MAX_ACTIVE_KEYS:
        raise ConflictError(
            f"Hai già {MAX_ACTIVE_KEYS} chiavi attive: revocane una prima di crearne un'altra."
        )
    key = new_api_key()
    row = ApiKey(user_id=user.id, name=body.name.strip(), prefix=key[:12], key_hash=hash_api_key(key))
    db.add(row)
    await db.commit()
    await db.refresh(row)
    log.info("extension.key_created", key_prefix=row.prefix)
    return ApiKeyCreated(**ApiKeyOut.model_validate(row).model_dump(), key=key)


@router.delete("/extension/keys/{key_id}", response_model=ApiKeyOut)
async def revoke_key(key_id: uuid.UUID, user: CurrentUser, db: DB) -> ApiKey:
    row = await db.get(ApiKey, key_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Chiave non trovata.")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        log.info("extension.key_revoked", key_prefix=row.prefix)
    return row


# ------------------------------------------------------------------ extension
@router.get("/extension/ping", response_model=dict[str, Any])
async def ping(user: CaptureUser) -> dict[str, Any]:
    """Pairing check from the extension's options page."""
    prefs = user.preferences
    return {
        "ok": True,
        "account": user.display_name or user.email,
        "parser_version": load_config().version,
        "algorithm_version": get_settings().algorithm_version,
        "targets": {
            "min_profit": float(prefs.min_profit) if prefs else 10.0,
            "min_roi": float(prefs.min_roi) if prefs else 0.4,
            "max_purchase_price": float(prefs.max_purchase_price)
            if prefs and prefs.max_purchase_price is not None
            else None,
        },
    }


@router.get("/extension/market-cache", response_model=dict[str, Any])
async def market_cache(
    user: CaptureUser, econ: CaptureEconomics, db: DB, response: Response
) -> dict[str, Any]:
    """Market summary for the instant verdict on search pages (scored in the extension)."""
    response.headers["Cache-Control"] = "private, max-age=600"
    catalog = await load_catalog(db)
    return await cache.get_or_set(
        NS_FEED, ("market-cache", str(user.id)), 900, lambda: build_market_cache(db, catalog, econ)
    )


@router.post(
    "/extension/page-stats",
    response_model=PageStatsOut,
    dependencies=[Depends(page_stats_limit)],
)
async def page_stats(body: PageStatsIn, user: CaptureUser, db: DB) -> dict[str, Any]:
    """Pre-computed price statistics for every card of a search page, in ONE query.

    The server recognises brand, category and model from each card's title and brand (the same
    engine as a capture) and answers from ``model_price_stats`` with the most specific segment
    that has data (model + size + condition down to brand + category, concluded sales first).
    Nothing is stored, analysed or searched: the extension refines its instant verdicts with it
    while the full analyses are on their way."""
    catalog = await load_catalog(db)
    cards = {
        i.vinted_id: identify_card(i.vinted_id, i.title, i.brand, i.size, i.condition, catalog)
        for i in body.items
    }
    stats = await lookup_stats(db, [c.query for c in cards.values()])
    for ref, st in stats.items():
        card = cards[ref]
        st["brand"] = st["brand"] or card.brand
        st["category"] = st["category"] or card.category
    return {"generated_at": datetime.now(UTC), "stats": stats}


@router.get("/extension/parser-config", response_model=dict[str, Any])
async def parser_config(user: CaptureUser, response: Response) -> dict[str, Any]:
    """The Vinted selectors/labels/patterns shared with the server parser. Fixing them here (or in
    the file at PARSER_CONFIG_PATH) updates every extension without publishing a new version."""
    response.headers["Cache-Control"] = "private, max-age=300"
    return config_json()


async def _touch_sync(db: DB, version: str | None, kind: str, count: int) -> None:
    state = await db.get(SystemState, "extension_sync")
    now = datetime.now(UTC).isoformat()
    value = dict(state.value or {}) if state else {}
    value.update(last_sync=now, version=version or value.get("version"))
    value[f"{kind}_total"] = int(value.get(f"{kind}_total", 0)) + count
    if state is None:
        db.add(SystemState(key="extension_sync", value=value))
    else:
        state.value = value


async def _after_capture(archive_ids: list[uuid.UUID], vision_ids: list[str]) -> None:
    """Photo checks (best first) and photo copies, queued once the response is on its way."""
    await queue_vision_safely(vision_ids)
    await schedule_archive(archive_ids)


def _vinted_only(items: list[Any]) -> list[Any]:
    return [i for i in items if listing_identity(str(i.url))[0] == "vinted"]


@router.post(
    "/capture/cards",
    response_model=CaptureCardsOut,
    dependencies=[Depends(capture_limit)],
)
async def capture_cards(
    body: CaptureCardsIn, user: CaptureUser, econ: CaptureEconomics, db: DB, background: BackgroundTasks
) -> CaptureCardsOut:
    """Cards the user scrolled past on a search, closet or favourites page ("visto in
    scorrimento"). Each is stored (a snapshot per sighting, status updated when seen again),
    quickly analysed from the card data, and evaluated with the user's costs.

    Cards seen again unchanged keep their recent analysis; photo copies and photo checks (best
    candidates first) are queued after the response is sent."""
    items = _vinted_only(body.items)
    mode = AcquisitionMode.EXTENSION_CARD
    by_id = {
        pl.external_id: pl for pl in (manual_to_provider(i, mode.value, CaptureLevel.CARD) for i in items)
    }
    result, outcomes = await persist_observations(
        db,
        list(by_id.values()),
        mode,
        track=False,
        reuse_recent=True,
        extension_version=body.extension_version,
        parser_version=body.parser_version,
    )
    vision = vision_order(outcomes)
    await _touch_sync(db, body.extension_version, "cards", len(by_id))
    await db.commit()
    await cache.bump(NS_FEED)
    ids = [result.ids_by_external[vid] for vid in by_id if vid in result.ids_by_external]
    evaluations = await quick_evaluations(db, user.id, econ, ids)
    background.add_task(_after_capture, list(result.enriched_ids or result.new_ids), vision)
    return CaptureCardsOut(received=len(body.items), stored=len(ids), evaluations=evaluations)


@router.post(
    "/capture/item",
    response_model=CaptureItemOut,
    dependencies=[Depends(item_limit)],
)
async def capture_item(
    body: CaptureItemIn, user: CaptureUser, econ: CaptureEconomics, db: DB, background: BackgroundTasks
) -> CaptureItemOut:
    """A whole item page ("analizzato a fondo"): every field and image, full analysis."""
    if listing_identity(str(body.item.url))[0] != "vinted":
        raise AppError("Solo annunci Vinted.", code="not_vinted")
    mode = AcquisitionMode(body.mode)
    pl = manual_to_provider(body.item, mode.value, CaptureLevel.FULL)
    result, outcomes = await persist_observations(
        db,
        [pl],
        mode,
        track=body.track,
        extension_version=body.extension_version,
        parser_version=body.parser_version,
    )
    await _touch_sync(db, body.extension_version, "items", 1)
    await db.commit()
    await cache.bump(NS_FEED)
    listing_id = result.ids_by_external[pl.external_id]
    background.add_task(_after_capture, [listing_id], vision_order(outcomes))
    evaluation = (await quick_evaluations(db, user.id, econ, [listing_id]) or [None])[0]
    outcome = next((o for o in outcomes if o.listing_id == listing_id), None)
    analysis = None
    if outcome is not None:
        opp = await db.get(Opportunity, outcome.opportunity_id)
        listing = await db.get(Listing, listing_id)
        if opp is not None and listing is not None:
            analysis = analysis_summary(opp, listing)
    return CaptureItemOut(evaluation=evaluation, analysis=analysis)


@router.post("/capture/evaluations", response_model=list[QuickEval])
async def evaluations(
    body: EvaluationsIn, user: CaptureUser, econ: CaptureEconomics, db: DB
) -> list[QuickEval]:
    """Current evaluations of known items (badges after a reload, without a new capture)."""
    ids = [v for v in body.vinted_ids if v.isdigit()]
    rows = (
        (
            await db.execute(
                select(Listing.id).where(Listing.provider == "vinted", Listing.external_id.in_(ids))
            )
        )
        .scalars()
        .all()
    )
    return await quick_evaluations(db, user.id, econ, list(rows))


@router.post("/capture/track", response_model=dict[str, Any])
async def capture_track(body: TrackIn, user: CaptureUser, econ: CaptureEconomics, db: DB) -> dict[str, Any]:
    """ "Traccia" from a card or an item page: start (or stop) periodic status checks."""
    provider, vid = listing_identity(body.url)
    if provider != "vinted":
        raise AppError("Solo annunci Vinted.", code="not_vinted")
    listing = (
        await db.execute(select(Listing).where(Listing.provider == "vinted", Listing.external_id == vid))
    ).scalar_one_or_none()
    if listing is None and body.track:
        res = await import_links(db, [(body.url.split("?")[0].split("#")[0], vid)], track=True)
        listing = await db.get(Listing, res.ids_by_vinted[vid])
    if listing is None:
        raise NotFoundError("Articolo non trovato.")
    await set_tracked(db, user.id, listing, body.track)
    await db.commit()
    await cache.bump(NS_FEED)
    evaluation = (await quick_evaluations(db, user.id, econ, [listing.id]) or [None])[0]
    return {
        "vinted_id": vid,
        "listing_id": str(listing.id),
        "tracked": body.track,
        "evaluation": evaluation.model_dump(mode="json") if evaluation else None,
    }


@router.get("/capture/items/{vinted_id}", response_model=dict[str, Any])
async def capture_item_detail(
    vinted_id: str, user: CaptureUser, econ: CaptureEconomics, db: DB
) -> dict[str, Any]:
    """Item detail for the live panel: evaluation, analysis summary, every photo, history."""
    if not vinted_id.isdigit():
        raise NotFoundError("Articolo non trovato.")
    detail = await item_detail(vinted_id, user, db)
    evaluation = (await quick_evaluations(db, user.id, econ, [detail.item.id]) or [None])[0]
    out = detail.model_dump(mode="json")
    # Photos as published on Vinted (the internal copies are served to signed-in web sessions).
    out["images"] = [{"position": i["position"], "url": i["url"]} for i in out["images"]]
    out["evaluation"] = evaluation.model_dump(mode="json") if evaluation else None
    return out
