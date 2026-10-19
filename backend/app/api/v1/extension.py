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

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy import func, select

from app.acquisition.evaluations import quick_evaluations
from app.acquisition.identity import listing_identity
from app.acquisition.market_cache import build_market_cache, identify_card
from app.acquisition.service import import_links
from app.acquisition.vinted_parser import config_json, load_config
from app.ai.queue import kick as kick_ai_review
from app.ai.queue import qualifies_for_strong_ai
from app.ai.verdicts import has_valid_review
from app.alerts.service import alerts_for_outcomes
from app.api.capture_pipeline import prepare_pools
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
from app.domain.enums import AcquisitionMode, CaptureLevel, ListingStatus, StatusEvidence
from app.ingestion.catalog import load_catalog
from app.ingestion.normalizer import market_currency, market_from_url
from app.ingestion.service import observation_time
from app.market.model_stats import lookup_stats
from app.media.archive import photo_state, photos_wanted, register_upload
from app.schemas.extension import (
    ApiKeyCreated,
    ApiKeyIn,
    ApiKeyOut,
    CaptureCardsIn,
    CaptureCardsOut,
    CaptureItemIn,
    CaptureItemOut,
    CardError,
    EvaluationsIn,
    GoneIn,
    PageStatsIn,
    PageStatsOut,
    QuickEval,
    TrackIn,
)
from app.tracking.actions import set_tracked
from app.tracking.service import TrackingService
from app.tracking.status import Observation
from app.tracking.summary import analysis_summary
from app.workers.queue import enqueue
from app.workers.vision_queue import queue_vision_if_worth, queue_vision_safely, vision_order

log = get_logger(__name__)
router = APIRouter(tags=["extension"])

MAX_ACTIVE_KEYS = 10
capture_limit = RateLimit("capture", per_minute=90)
item_limit = RateLimit("capture-item", per_minute=40)
page_stats_limit = RateLimit("page-stats", per_minute=60)
photo_limit = RateLimit("capture-photo", per_minute=240)


class InvalidPhotoError(AppError):
    status_code = 422
    code = "invalid_photo"
    message = "La foto non è un'immagine valida."


class PhotoTooLargeError(AppError):
    status_code = 413
    code = "photo_too_large"
    message = "File troppo grande."


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


async def _after_capture(
    vision_ids: list[str],
    ai_kicks: list[tuple[int, uuid.UUID]] | None = None,
    alerts: list[tuple[uuid.UUID, str]] | None = None,
) -> None:
    """Queued once the response is on its way (and the capture is committed): the photo checks (best first), which
    run on the photos the browser has uploaded - a listing without copies is checked when its photos arrive -, the
    strong model's review of what the user is looking at now, and the delivery of the alerts the capture raised."""
    await queue_vision_safely(vision_ids)
    if ai_kicks:
        await kick_ai_review(ai_kicks, interactive=True)  # best effort: the minute sweep finds the rest
    for alert_id, channel in alerts or []:
        try:
            await enqueue(
                "deliver_alert_task",
                str(alert_id),
                channel,
                high=True,
                job_id=f"deliver:{alert_id}:{channel}",
            )
        except Exception:  # the alert is stored and shown in the app; only the push/mail/chat copy is late
            log.warning("capture.alert_delivery_not_queued", alert_id=str(alert_id), channel=channel)


async def _signals(
    db: DB, outcomes: list[Any]
) -> tuple[list[tuple[int, uuid.UUID]], list[tuple[uuid.UUID, str]]]:
    """What a capture sets in motion besides its own answer: the strong model's review of the new analyses that
    qualify, and the alerts (new find, price drop, watchlist, Ultra Deal). The extension is the only source of
    listings in this deployment: waiting for a worker pass to notice them would be hours late. The alerts are written
    in the capture's transaction (in a savepoint: a failure there never costs the capture) and delivered after it."""
    settings = get_settings()
    kicks = [
        (o.result.flip.score, o.opportunity_id)
        for o in outcomes
        if o.ai_pending
        and qualifies_for_strong_ai(
            settings,
            flip_score=o.result.flip.score,
            data_quality=o.result.data_quality,
            decision_verdict=o.result.decision.verdict.value if o.result.decision else None,
            risk_adjusted_profit=o.result.risk_adjusted_profit,
        )
    ]
    alerts: list[tuple[uuid.UUID, str]] = []
    if outcomes:
        try:
            async with db.begin_nested():
                alerts = await alerts_for_outcomes(db, outcomes, await load_catalog(db))
        except Exception:
            log.exception("capture.alerts_failed")
    return kicks, alerts


async def _bump_if_changed(result: Any, outcomes: list[Any]) -> None:
    """The dashboards' cache is emptied only when the capture changed what they show: a new listing, a new price or
    status, an analysis written. A page seen again unchanged leaves the cached feed, stats and plan alone."""
    if (
        result.new_ids
        or result.price_changes
        or result.status_changes
        or result.enriched_ids
        or any(o.written for o in outcomes)
    ):
        await cache.bump(NS_FEED)


def _vinted_only(items: list[Any]) -> list[Any]:
    """Vinted cards in euro: a card from a market with another currency (vinted.pl, vinted.co.uk...) is skipped."""
    return [
        i
        for i in items
        if listing_identity(str(i.url))[0] == "vinted"
        and market_currency(market_from_url(str(i.url))) == "EUR"
    ]


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
    errors = list(body.rejected)
    by_id: dict[str, Any] = {}
    for item in items:
        try:
            pl = manual_to_provider(item, mode.value, CaptureLevel.CARD)
        except AppError as exc:  # one card this server cannot take (e.g. another currency): the others go on
            errors.append(CardError(vinted_id=listing_identity(str(item.url))[1], message=exc.message))
            continue
        by_id[pl.external_id] = pl
    failures: dict[str, str] = {}
    result, outcomes = await persist_observations(
        db,
        list(by_id.values()),
        mode,
        track=False,
        reuse_recent=True,
        extension_version=body.extension_version,
        parser_version=body.parser_version,
        failures=failures,
    )
    errors += [CardError(vinted_id=vid, message=why) for vid, why in failures.items()]
    vision = vision_order(outcomes)
    ai_kicks, alerts = await _signals(db, outcomes)
    await _touch_sync(db, body.extension_version, "cards", len(by_id))
    await db.commit()
    await _bump_if_changed(result, outcomes)
    ids = [result.ids_by_external[vid] for vid in by_id if vid in result.ids_by_external]
    evaluations = await quick_evaluations(db, user.id, econ, ids)
    background.add_task(_after_capture, vision, ai_kicks, alerts)
    return CaptureCardsOut(
        received=len(body.items) + len(body.rejected), stored=len(ids), evaluations=evaluations, errors=errors
    )


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
        # The same page opened again, unchanged, within a couple of hours keeps its analysis (and its photo
        # findings): the stored analysis is returned, nothing is computed or rewritten for it.
        reuse_recent=True,
        extension_version=body.extension_version,
        parser_version=body.parser_version,
    )
    ai_kicks, alerts = await _signals(db, outcomes)
    await _touch_sync(db, body.extension_version, "items", 1)
    await db.commit()
    await _bump_if_changed(result, outcomes)
    listing_id = result.ids_by_external[pl.external_id]
    background.add_task(_after_capture, vision_order(outcomes), ai_kicks, alerts)
    wanted = (await photos_wanted(db, [listing_id])).get(listing_id, [])
    evaluation = (await quick_evaluations(db, user.id, econ, [listing_id]) or [None])[0]
    opp = (
        await db.execute(select(Opportunity).where(Opportunity.listing_id == listing_id))
    ).scalar_one_or_none()
    listing = await db.get(Listing, listing_id)
    analysis = analysis_summary(opp, listing) if opp is not None and listing is not None else None
    return CaptureItemOut(
        evaluation=evaluation,
        analysis=analysis,
        photos_wanted=wanted,
        review_pending=bool(opp is not None and _review_pending(opp)),
    )


def _review_pending(opp: Opportunity) -> bool:
    """The strong model has not yet given its second opinion on the analysis the user is looking at."""
    return bool(
        opp.is_active
        and not has_valid_review(opp)
        and qualifies_for_strong_ai(
            get_settings(),
            flip_score=opp.flip_score,
            data_quality=opp.data_quality,
            decision_verdict=opp.decision_verdict,
            risk_adjusted_profit=opp.risk_adjusted_profit,
        )
    )


@router.post("/capture/gone", response_model=dict[str, Any], dependencies=[Depends(item_limit)])
async def capture_gone(
    body: GoneIn, user: CaptureUser, econ: CaptureEconomics, db: DB, background: BackgroundTasks
) -> dict[str, Any]:
    """The user's browser opened a listing page that says the item is gone ("questo articolo non è più
    disponibile"). Only a listing already known is updated: it becomes ``removed`` (no sale is inferred), its
    opportunity stops being shown as active, the move is recorded in its history and the dashboards are refreshed.
    The page was read by the user's browser; nothing is sent to Vinted. An item FlipFinder has never seen is left
    alone (``known: false``)."""
    listing = (
        await db.execute(
            select(Listing).where(Listing.provider == "vinted", Listing.external_id == body.vinted_id)
        )
    ).scalar_one_or_none()
    if listing is None:
        return {"known": False, "vinted_id": body.vinted_id}
    now = datetime.now(UTC)
    seen = observation_time(body.observed_at, now)
    if listing.last_seen_at is not None and seen < listing.last_seen_at:
        # Read before a later capture of the same item (it was on sale again, or was seen after): not applied.
        return {"known": True, "vinted_id": body.vinted_id, "status": listing.status, "changed": False}
    updates = await TrackingService(db).observe(
        {
            listing.id: Observation(
                seen, StatusEvidence.NOT_FOUND, ListingStatus.REMOVED, note="Pagina: non disponibile."
            )
        },
        AcquisitionMode.EXTENSION_ITEM,
    )
    update = updates[listing.id]
    if update.changed:
        await prepare_pools(db, True)  # the market lost an ask: the shared comparables pools are dropped
    await _touch_sync(db, body.extension_version, "items", 1)
    await db.commit()
    if update.changed:
        await cache.bump(NS_FEED)
    evaluation = (await quick_evaluations(db, user.id, econ, [listing.id]) or [None])[0]
    return {
        "known": True,
        "vinted_id": body.vinted_id,
        "status": update.status.value,
        "changed": update.changed,
        "evaluation": evaluation.model_dump(mode="json") if evaluation else None,
    }


@router.put(
    "/capture/photos/{vinted_id}/{image_key}",
    response_model=dict[str, Any],
    dependencies=[Depends(photo_limit)],
)
async def upload_photo(
    vinted_id: str,
    image_key: str,
    request: Request,
    user: CaptureUser,
    db: DB,
    background: BackgroundTasks,
) -> dict[str, Any]:
    """One photo of an item page, as the user's browser loaded it (raw image bytes in the body).

    The server never downloads from Vinted: this is how it gets the photos it analyses. Only a photo
    the server asked for (see ``photos_wanted`` in the item capture) is accepted. The last one to
    arrive queues the photo check of the listing."""
    if not (vinted_id.isdigit() and len(vinted_id) <= 20 and len(image_key) == 16 and image_key.isalnum()):
        raise InvalidPhotoError("Identificativi non validi.")
    limit = get_settings().image_archive_max_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise PhotoTooLargeError()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise PhotoTooLargeError()
        chunks.append(chunk)
    res = await register_upload(
        db, vinted_id, image_key, b"".join(chunks), request.headers.get("content-type")
    )
    if not res.stored:
        if res.listing_id is None:
            raise NotFoundError("Foto non richiesta per questo annuncio.")
        raise InvalidPhotoError(res.error or None)
    await db.commit()
    if res.remaining == 0 and res.listing_id is not None:
        background.add_task(queue_vision_if_worth, [str(res.listing_id)])
    return {"stored": True, "remaining": res.remaining, "sha256": res.sha256}


@router.post(
    "/capture/photos/{vinted_id}/complete",
    response_model=dict[str, Any],
    dependencies=[Depends(photo_limit)],
)
async def photos_complete(
    vinted_id: str, user: CaptureUser, db: DB, background: BackgroundTasks
) -> dict[str, Any]:
    """The browser has sent every photo it could read. Photos it could not read stay missing (and are
    reported as such in the dossier); the photo check runs on the ones the server has."""
    if not (vinted_id.isdigit() and len(vinted_id) <= 20):
        raise InvalidPhotoError("Identificativo non valido.")
    state = await photo_state(db, vinted_id)
    if state is None:
        raise NotFoundError("Annuncio non trovato.")
    listing_id, have, total = state
    if have:
        background.add_task(queue_vision_if_worth, [str(listing_id)])
    return {"photos": have, "of": total, "queued": bool(have)}


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
