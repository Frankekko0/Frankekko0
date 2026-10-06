"""Browser extension: pairing keys, shared parser configuration and captures.

The extension sends what the user's own browser shows on Vinted pages they open (cards seen
while scrolling, item pages, deep analyses they ask for) and gets back a quick evaluation per
item. It authenticates with a FlipFinder key created here; Vinted cookies or tokens are never
read or sent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select, update

from app.acquisition.evaluations import quick_evaluations
from app.acquisition.identity import listing_identity
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
from app.domain.enums import OPEN_STATUSES, AcquisitionMode, CaptureLevel, StatusEvidence
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
    QuickEval,
    RefreshResultIn,
    TrackIn,
)
from app.tracking.actions import set_tracked
from app.tracking.service import Attempt, TrackingService, record_attempts
from app.tracking.status import Observation
from app.tracking.summary import analysis_summary

log = get_logger(__name__)
router = APIRouter(tags=["extension"])

MAX_ACTIVE_KEYS = 10
REFRESH_LEASE = timedelta(minutes=30)
capture_limit = RateLimit("capture", per_minute=90)
item_limit = RateLimit("capture-item", per_minute=40)


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


def _vinted_only(items: list[Any]) -> list[Any]:
    return [i for i in items if listing_identity(str(i.url))[0] == "vinted"]


@router.post(
    "/capture/cards",
    response_model=CaptureCardsOut,
    dependencies=[Depends(capture_limit)],
)
async def capture_cards(
    body: CaptureCardsIn, user: CaptureUser, econ: CaptureEconomics, db: DB
) -> CaptureCardsOut:
    """Cards the user scrolled past on a search, closet or favourites page ("visto in
    scorrimento"). Each is stored (a snapshot per sighting, status updated when seen again),
    quickly analysed from the card data, and evaluated with the user's costs."""
    items = _vinted_only(body.items)
    by_id = {
        pl.external_id: pl
        for pl in (manual_to_provider(i, "extension_card", CaptureLevel.CARD) for i in items)
    }
    result, _outcomes = await persist_observations(
        db, list(by_id.values()), AcquisitionMode.EXTENSION_CARD, track=False
    )
    await _touch_sync(db, body.extension_version, "cards", len(by_id))
    await db.commit()
    await cache.bump(NS_FEED)
    await schedule_archive(list(result.enriched_ids or result.new_ids))
    ids = [result.ids_by_external[vid] for vid in by_id if vid in result.ids_by_external]
    evaluations = await quick_evaluations(db, user.id, econ, ids)
    return CaptureCardsOut(received=len(body.items), stored=len(ids), evaluations=evaluations)


@router.post(
    "/capture/item",
    response_model=CaptureItemOut,
    dependencies=[Depends(item_limit)],
)
async def capture_item(
    body: CaptureItemIn, user: CaptureUser, econ: CaptureEconomics, db: DB
) -> CaptureItemOut:
    """A whole item page ("analizzato a fondo"): every field and image, full analysis."""
    if listing_identity(str(body.item.url))[0] != "vinted":
        raise AppError("Solo annunci Vinted.", code="not_vinted")
    mode = AcquisitionMode(body.mode)
    pl = manual_to_provider(body.item, mode.value, CaptureLevel.FULL)
    result, outcomes = await persist_observations(db, [pl], mode, track=body.track)
    await _touch_sync(db, body.extension_version, "items", 1)
    await db.commit()
    await cache.bump(NS_FEED)
    listing_id = result.ids_by_external[pl.external_id]
    await schedule_archive([listing_id])
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


@router.get("/capture/refresh-queue", response_model=dict[str, Any])
async def refresh_queue(user: CaptureUser, db: DB, limit: int = Query(3, ge=1, le=5)) -> dict[str, Any]:
    """A few tracked items due for a status check, for the extension's optional slow refresh.

    Handed out items are leased for 30 minutes, so they are not handed out again meanwhile.
    """
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(Listing.id, Listing.external_id, Listing.url)
            .where(
                Listing.provider == "vinted",
                Listing.tracked_at.is_not(None),
                Listing.status.in_([s.value for s in OPEN_STATUSES]),
                Listing.next_check_at <= now,
            )
            .order_by(Listing.next_check_at)
            .limit(limit)
        )
    ).all()
    if rows:
        await db.execute(
            update(Listing)
            .where(Listing.id.in_([r.id for r in rows]))
            .values(next_check_at=now + REFRESH_LEASE)
        )
        await db.commit()
    return {"items": [{"vinted_id": r.external_id, "url": r.url} for r in rows]}


@router.post("/capture/refresh-result", response_model=dict[str, Any])
async def refresh_result(body: RefreshResultIn, user: CaptureUser, db: DB) -> dict[str, Any]:
    """A status check by the extension that did not yield a page: gone (404/410) or not readable.

    "Gone" marks the item removed, never sold. A block or an error changes nothing but the
    failure count (the next check is pushed back).
    """
    listing = (
        await db.execute(
            select(Listing).where(Listing.provider == "vinted", Listing.external_id == body.vinted_id)
        )
    ).scalar_one_or_none()
    if listing is None:
        raise NotFoundError("Articolo non trovato.")
    now = datetime.now(UTC)
    evidence = StatusEvidence.NOT_FOUND if body.outcome == "not_found" else StatusEvidence.UNREACHABLE
    note = {
        "not_found": "Annuncio non più disponibile (controllo dell'estensione).",
        "blocked": "Vinted ha rifiutato la lettura: nessun nuovo tentativo immediato.",
        "error": "Pagina non leggibile dall'estensione.",
    }[body.outcome]
    updates = await TrackingService(db).observe(
        {listing.id: Observation(observed_at=now, evidence=evidence, note=note)},
        AcquisitionMode.EXTENSION_REFRESH,
    )
    attempt = Attempt(
        mode=AcquisitionMode.EXTENSION_REFRESH.value,
        action="refresh",
        listing_id=listing.id,
        vinted_id=body.vinted_id,
        http_status=body.http_status,
        started_at=now,
    )
    if body.outcome != "not_found":
        attempt.fail(body.outcome, body.message or note)
    else:
        attempt.message = note
    await record_attempts(db, [attempt])
    await db.commit()
    await cache.bump(NS_FEED)
    upd = updates.get(listing.id)
    return {"vinted_id": body.vinted_id, "status": upd.status.value if upd else listing.status}
