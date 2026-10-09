"""User settings: economic targets, cost profile, scoring weights, notification channels."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.alerts.channels.base import ChannelError, NotificationMessage
from app.alerts.service import reset_audience_cache, send_to_user_channel
from app.api.deps import DB, CurrentUser
from app.core.cache import NS_FEED, cache
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import NotificationSettings, UserPreferences
from app.schemas.common import Message
from app.schemas.settings import (
    ChannelStatus,
    NotificationSettingsIO,
    NotificationSettingsOut,
    PreferencesIO,
    TestNotificationIn,
    mask_secret_url,
)
from app.seed import ensure_user_defaults

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/preferences", response_model=PreferencesIO)
async def get_preferences(user: CurrentUser, db: DB) -> PreferencesIO:
    prefs = await db.get(UserPreferences, user.id)
    if prefs is None:
        await ensure_user_defaults(db, user)
        await db.commit()
        prefs = await db.get(UserPreferences, user.id)
    return PreferencesIO.model_validate(
        {
            **{c: getattr(prefs, c) for c in PreferencesIO.model_fields if c != "cost_profile"},
            "cost_profile": prefs.cost_profile or {},
        }
    )


@router.put("/preferences", response_model=PreferencesIO)
async def put_preferences(body: PreferencesIO, user: CurrentUser, db: DB) -> PreferencesIO:
    prefs = await db.get(UserPreferences, user.id)
    if prefs is None:
        prefs = UserPreferences(user_id=user.id)
        db.add(prefs)
    data = body.model_dump(mode="json")
    for key, value in data.items():
        if key in ("min_profit", "min_roi", "max_purchase_price", "total_budget"):
            value = getattr(body, key)
        setattr(prefs, key, value)
    await db.commit()
    await cache.bump(NS_FEED)
    reset_audience_cache()
    return body


def _channel_status(ns: NotificationSettings) -> ChannelStatus:
    s = get_settings()
    return ChannelStatus(
        in_app=True,
        web_push=bool(s.vapid_public_key and s.vapid_private_key),
        email=bool(s.smtp_host),
        telegram=bool(s.telegram_bot_token),
        discord=True,
    )


def _notifications_out(ns: NotificationSettings) -> NotificationSettingsOut:
    data: dict[str, Any] = {c: getattr(ns, c) for c in NotificationSettingsIO.model_fields}
    data["discord_webhook_url"] = mask_secret_url(ns.discord_webhook_url)
    return NotificationSettingsOut(
        **data, available_channels=_channel_status(ns), vapid_public_key=get_settings().vapid_public_key
    )


@router.get("/notifications", response_model=NotificationSettingsOut)
async def get_notifications(user: CurrentUser, db: DB) -> NotificationSettingsOut:
    ns = await db.get(NotificationSettings, user.id)
    if ns is None:
        await ensure_user_defaults(db, user)
        await db.commit()
        ns = await db.get(NotificationSettings, user.id)
    assert ns is not None
    return _notifications_out(ns)


@router.put("/notifications", response_model=NotificationSettingsOut)
async def put_notifications(
    body: NotificationSettingsIO, user: CurrentUser, db: DB
) -> NotificationSettingsOut:
    ns = await db.get(NotificationSettings, user.id)
    if ns is None:
        ns = NotificationSettings(user_id=user.id)
        db.add(ns)
    for key, value in body.model_dump().items():
        if key == "discord_webhook_url" and value and value.startswith("•"):
            continue  # masked value sent back unchanged: keep the stored secret
        setattr(ns, key, value)
    await db.commit()
    reset_audience_cache()
    return _notifications_out(ns)


@router.post("/notifications/test", response_model=Message)
async def test_notification(body: TestNotificationIn, user: CurrentUser, db: DB) -> Message:
    settings = get_settings()
    message = NotificationMessage(
        title="🔔 FlipFinder: notifica di prova",
        body="Le notifiche funzionano. Riceverai qui gli affari che superano i tuoi criteri.",
        app_url=settings.public_app_url,
        listing_url=None,
        priority="normal",
    )
    try:
        await send_to_user_channel(db, user.id, body.channel, message, settings)
    except ChannelError as exc:
        raise AppError(
            "Impossibile inviare la notifica di prova: controlla la configurazione del canale.",
            code="channel_failed",
            details={"channel": body.channel, "reason": str(exc)[:120]},
        ) from exc
    await db.commit()
    return Message(message="Notifica di prova inviata.")
