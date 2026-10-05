"""Watchlists, alerts (in-app notification center) and Web Push subscriptions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.alerts.service import reset_audience_cache
from app.api.deps import DB, CurrentUser, Economics
from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.db.models import Alert, AlertDelivery, PushSubscription, Watchlist
from app.opportunities.queries import OpportunityQueries
from app.schemas.common import Message, Page
from app.schemas.monitoring import AlertOut, PushSubscriptionIn, UnreadCount, WatchlistIn, WatchlistOut
from app.schemas.opportunity import OpportunityCard, OpportunityFilters

router = APIRouter(tags=["monitoring"])


async def _watchlist(db: DB, user_id: uuid.UUID, watchlist_id: uuid.UUID) -> Watchlist:
    wl = await db.get(Watchlist, watchlist_id)
    if wl is None or wl.user_id != user_id:
        raise NotFoundError("Watchlist non trovata.")
    return wl


def watchlist_filters(wl: Watchlist, page: int = 1, page_size: int = 24) -> OpportunityFilters:
    min_flip = wl.min_flip_score
    if min_flip is None and wl.min_profit is None and wl.min_roi is None:
        min_flip = 60  # same quality floor used by watchlist alerts
    return OpportunityFilters(
        q=wl.query,
        brands=list(wl.brand_slugs or []),
        categories=list(wl.category_slugs or []),
        sizes=list(wl.sizes or []),
        conditions=list(wl.conditions or []),
        countries=list(wl.countries or []),
        vintage_only=wl.vintage_only,
        max_price=float(wl.max_buy_price) if wl.max_buy_price is not None else None,
        min_profit=float(wl.min_profit) if wl.min_profit is not None else None,
        min_roi=float(wl.min_roi) if wl.min_roi is not None else None,
        min_flip=min_flip,
        min_confidence=wl.min_confidence,
        max_risk=wl.max_risk_score,
        page=page,
        page_size=page_size,
    )


@router.get("/watchlists", response_model=list[WatchlistOut])
async def list_watchlists(user: CurrentUser, econ: Economics, db: DB) -> list[WatchlistOut]:
    rows = (
        (
            await db.execute(
                select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.created_at)
            )
        )
        .scalars()
        .all()
    )
    q = OpportunityQueries(db, user.id, econ)
    out = []
    for wl in rows:
        stmt, _ = q.build(watchlist_filters(wl))
        count = (
            await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
        ).scalar_one()
        item = WatchlistOut.model_validate(wl)
        item.match_count = count
        out.append(item)
    return out


@router.post("/watchlists", response_model=WatchlistOut, status_code=201)
async def create_watchlist(body: WatchlistIn, user: CurrentUser, db: DB) -> WatchlistOut:
    count = (
        await db.execute(select(func.count()).select_from(Watchlist).where(Watchlist.user_id == user.id))
    ).scalar_one()
    if count >= 50:
        raise AppError("Hai raggiunto il numero massimo di watchlist (50).", code="limit_reached")
    wl = Watchlist(user_id=user.id, **body.model_dump())
    db.add(wl)
    await db.commit()
    await db.refresh(wl)
    reset_audience_cache()
    return WatchlistOut.model_validate(wl)


@router.patch("/watchlists/{watchlist_id}", response_model=WatchlistOut)
async def update_watchlist(
    watchlist_id: uuid.UUID, body: WatchlistIn, user: CurrentUser, db: DB
) -> WatchlistOut:
    wl = await _watchlist(db, user.id, watchlist_id)
    for k, v in body.model_dump().items():
        setattr(wl, k, v)
    await db.commit()
    await db.refresh(wl)
    reset_audience_cache()
    return WatchlistOut.model_validate(wl)


@router.delete("/watchlists/{watchlist_id}", response_model=Message)
async def delete_watchlist(watchlist_id: uuid.UUID, user: CurrentUser, db: DB) -> Message:
    wl = await _watchlist(db, user.id, watchlist_id)
    await db.delete(wl)
    await db.commit()
    reset_audience_cache()
    return Message(message="Watchlist eliminata.")


@router.get("/watchlists/{watchlist_id}/matches", response_model=Page[OpportunityCard])
async def watchlist_matches(
    watchlist_id: uuid.UUID,
    user: CurrentUser,
    econ: Economics,
    db: DB,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
) -> Page[OpportunityCard]:
    wl = await _watchlist(db, user.id, watchlist_id)
    f = watchlist_filters(wl, page, page_size)
    cards, total = await OpportunityQueries(db, user.id, econ).feed(f)
    return Page[OpportunityCard](
        items=cards, total=total, page=page, page_size=page_size, has_more=page * page_size < total
    )


# ------------------------------------------------------------------------------- alerts
async def _alert_out(db: DB, alerts: list[Alert]) -> list[AlertOut]:
    ids = [a.id for a in alerts]
    deliveries: dict[uuid.UUID, list[dict[str, Any]]] = {}
    if ids:
        rows = (
            (await db.execute(select(AlertDelivery).where(AlertDelivery.alert_id.in_(ids)))).scalars().all()
        )
        for d in rows:
            deliveries.setdefault(d.alert_id, []).append(
                {"channel": d.channel, "status": d.status, "attempts": d.attempts, "sent_at": d.sent_at}
            )
    out = []
    for a in alerts:
        item = AlertOut.model_validate(a)
        item.deliveries = deliveries.get(a.id, [])
        out.append(item)
    return out


@router.get("/alerts", response_model=Page[AlertOut])
async def list_alerts(
    user: CurrentUser,
    db: DB,
    unread_only: bool = False,
    type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
) -> Page[AlertOut]:
    stmt = select(Alert).where(Alert.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Alert.read_at.is_(None))
    if type:
        stmt = stmt.where(Alert.type == type)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    alerts = list(
        (
            await db.execute(
                stmt.order_by(Alert.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return Page[AlertOut](
        items=await _alert_out(db, alerts),
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


@router.get("/alerts/unread-count", response_model=UnreadCount)
async def unread_count(user: CurrentUser, db: DB) -> UnreadCount:
    n = (
        await db.execute(
            select(func.count()).select_from(Alert).where(Alert.user_id == user.id, Alert.read_at.is_(None))
        )
    ).scalar_one()
    latest = (
        await db.execute(
            select(Alert)
            .where(Alert.user_id == user.id, Alert.read_at.is_(None), Alert.priority == "high")
            .order_by(Alert.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return UnreadCount(unread=n, latest_high_priority=(await _alert_out(db, [latest]))[0] if latest else None)


@router.post("/alerts/{alert_id}/read", response_model=Message)
async def mark_read(alert_id: uuid.UUID, user: CurrentUser, db: DB) -> Message:
    res = await db.execute(
        update(Alert)
        .where(Alert.id == alert_id, Alert.user_id == user.id, Alert.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()
    if res.rowcount == 0:  # type: ignore[attr-defined]
        exists = (
            await db.execute(select(Alert.id).where(Alert.id == alert_id, Alert.user_id == user.id))
        ).first()
        if not exists:
            raise NotFoundError("Notifica non trovata.")
    return Message(message="Segnata come letta.")


@router.post("/alerts/read-all", response_model=Message)
async def mark_all_read(user: CurrentUser, db: DB) -> Message:
    await db.execute(
        update(Alert)
        .where(Alert.user_id == user.id, Alert.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()
    return Message(message="Tutte le notifiche sono state lette.")


# ---------------------------------------------------------------------------- web push
@router.get("/notifications/push/public-key", response_model=dict[str, str | None])
async def push_public_key() -> dict[str, str | None]:
    return {"public_key": get_settings().vapid_public_key}


@router.post("/notifications/push/subscribe", response_model=Message, status_code=201)
async def push_subscribe(
    body: PushSubscriptionIn, user: CurrentUser, db: DB, user_agent: str | None = None
) -> Message:
    stmt = pg_insert(PushSubscription).values(
        id=uuid.uuid4(),
        user_id=user.id,
        endpoint=body.endpoint,
        p256dh=body.keys["p256dh"],
        auth=body.keys["auth"],
        user_agent=(user_agent or "")[:255] or None,
        created_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["endpoint"],
        set_={"user_id": user.id, "p256dh": body.keys["p256dh"], "auth": body.keys["auth"]},
    )
    await db.execute(stmt)
    await db.commit()
    reset_audience_cache()
    return Message(message="Notifiche push attivate su questo dispositivo.")


@router.post("/notifications/push/unsubscribe", response_model=Message)
async def push_unsubscribe(body: PushSubscriptionIn, user: CurrentUser, db: DB) -> Message:
    await db.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == user.id, PushSubscription.endpoint == body.endpoint
        )
    )
    await db.commit()
    reset_audience_cache()
    return Message(message="Notifiche push disattivate su questo dispositivo.")
