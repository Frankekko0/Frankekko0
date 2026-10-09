"""Alert evaluation (after each analysis) and per-channel delivery."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.channels.base import ChannelError, NotificationChannel, NotificationMessage
from app.alerts.channels.discord import DiscordChannel
from app.alerts.channels.email import EmailChannel
from app.alerts.channels.telegram import TelegramChannel
from app.alerts.channels.webpush import SubscriptionGone, WebPushChannel
from app.alerts.rules import AlertCandidate, Thresholds, WatchlistRule, decide_alerts
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import (
    Alert,
    AlertDelivery,
    Listing,
    ListingImage,
    NotificationSettings,
    PushSubscription,
    User,
    UserPreferences,
    Watchlist,
)
from app.domain.enums import DeliveryStatus, NotificationChannelType
from app.ingestion.catalog import Catalog
from app.opportunities.pipeline import AnalysisOutcome
from app.profit.calculator import CostProfile, profit_for

log = get_logger(__name__)
AUDIENCE_TTL = 60.0


@dataclass
class Audience:
    user_id: uuid.UUID
    email: str
    settings: NotificationSettings
    costs: CostProfile
    watchlists: list[WatchlistRule]
    has_push: bool


_audience_cache: tuple[float, list[Audience]] | None = None


def reset_audience_cache() -> None:
    global _audience_cache
    _audience_cache = None


def watchlist_rule(w: Watchlist) -> WatchlistRule:
    return WatchlistRule(
        id=w.id,
        name=w.name,
        query=w.query,
        brands=tuple(w.brand_slugs or ()),
        categories=tuple(w.category_slugs or ()),
        sizes=tuple(w.sizes or ()),
        conditions=tuple(w.conditions or ()),
        countries=tuple(w.countries or ()),
        vintage_only=w.vintage_only,
        max_buy_price=w.max_buy_price,
        min_profit=w.min_profit,
        min_roi=w.min_roi,
        min_flip=w.min_flip_score,
        min_confidence=w.min_confidence,
        max_risk=w.max_risk_score,
    )


async def load_audience(session: AsyncSession) -> list[Audience]:
    global _audience_cache
    if _audience_cache and time.monotonic() - _audience_cache[0] < AUDIENCE_TTL:
        return _audience_cache[1]
    rows = (
        await session.execute(
            select(User, NotificationSettings, UserPreferences)
            .join(NotificationSettings, NotificationSettings.user_id == User.id)
            .outerjoin(UserPreferences, UserPreferences.user_id == User.id)
            .where(User.is_active.is_(True))
        )
    ).all()
    watchlists = (
        (
            await session.execute(
                select(Watchlist).where(Watchlist.is_active.is_(True), Watchlist.notify.is_(True))
            )
        )
        .scalars()
        .all()
    )
    by_user: dict[uuid.UUID, list[WatchlistRule]] = {}
    for w in watchlists:
        by_user.setdefault(w.user_id, []).append(watchlist_rule(w))
    push_users = set((await session.execute(select(PushSubscription.user_id).distinct())).scalars().all())
    audience = [
        Audience(
            user_id=u.id,
            email=u.email,
            settings=ns,
            costs=CostProfile.model_validate(prefs.cost_profile or {}) if prefs else CostProfile(),
            watchlists=by_user.get(u.id, []),
            has_push=u.id in push_users,
        )
        for u, ns, prefs in rows
    ]
    _audience_cache = (time.monotonic(), audience)
    return audience


def enabled_channels(a: Audience, settings: Settings) -> list[str]:
    ns = a.settings
    channels = [NotificationChannelType.IN_APP.value] if ns.in_app_enabled else []
    if ns.web_push_enabled and a.has_push and settings.vapid_public_key and settings.vapid_private_key:
        channels.append(NotificationChannelType.WEB_PUSH.value)
    if ns.email_enabled and (ns.email_address or a.email) and settings.smtp_host:
        channels.append(NotificationChannelType.EMAIL.value)
    if ns.telegram_enabled and ns.telegram_chat_id and settings.telegram_bot_token:
        channels.append(NotificationChannelType.TELEGRAM.value)
    if ns.discord_enabled and ns.discord_webhook_url:
        channels.append(NotificationChannelType.DISCORD.value)
    return channels


async def evaluate_alerts(
    session: AsyncSession,
    outcome: AnalysisOutcome,
    listing: Listing,
    catalog: Catalog,
    now: datetime | None = None,
) -> list[tuple[uuid.UUID, str]]:
    """Create alerts for every interested user; return (alert_id, channel) pairs to deliver."""
    settings = get_settings()
    r = outcome.result
    expected = r.scenario("expected")
    if r.data_quality == "insufficient":
        return []  # no reliable estimate: never alert on a guess
    if expected is None and not r.ultra:
        return []
    audience = await load_audience(session)
    if not audience:
        return []
    category_slug = catalog.category_slug(listing.category_id)
    published = listing.published_at or listing.first_seen_at
    age_hours = ((now or datetime.now(UTC)) - published).total_seconds() / 3600 if published else None
    pending: list[tuple[uuid.UUID, str]] = []
    now = datetime.now(UTC)
    for a in audience:
        profit = roi = None
        if expected is not None:
            pr = profit_for(
                listing.price,
                expected.sale_price,
                a.costs,
                listing.shipping_fee,
                listing.buyer_protection_fee,
            )
            profit, roi = pr.net_profit, pr.roi
        cand = AlertCandidate(
            opportunity_id=outcome.opportunity_id,
            listing_id=listing.id,
            root_listing_id=listing.duplicate_of_id or listing.id,
            title=listing.title,
            brand=catalog.brand_slug(listing.brand_id),
            category=category_slug,
            parent_category=catalog.parent_slug(category_slug),
            size=listing.size_normalized,
            condition=listing.condition,
            country=listing.country,
            is_vintage=listing.is_vintage,
            price=listing.price,
            previous_price=outcome.previous_price,
            expected_profit=profit,
            expected_roi=roi,
            flip_score=r.flip.score,
            confidence=r.confidence.score,
            risk=r.risk.score,
            is_ultra=r.ultra,
            is_new=outcome.is_new,
            listing_age_hours=age_hours,
            decision_verdict=r.decision.verdict.value if r.decision else None,
        )
        ns = a.settings
        decisions = decide_alerts(
            cand,
            Thresholds(
                ns.alert_min_flip_score,
                Decimal(ns.alert_min_roi),
                Decimal(ns.alert_min_profit),
                ns.alert_min_confidence,
                ns.alert_max_risk_score,
            ),
            a.watchlists,
            ultra_enabled=ns.ultra_deal_alerts,
            price_drop_enabled=ns.price_drop_alerts,
            watchlist_enabled=ns.watchlist_alerts,
            new_opportunity_enabled=ns.new_opportunity_alerts,
            max_listing_age_hours=settings.alert_max_listing_age_hours,
        )
        if not decisions:
            continue
        channels = enabled_channels(a, settings)
        for d in decisions:
            stmt = (
                pg_insert(Alert)
                .values(
                    id=uuid.uuid4(),
                    user_id=a.user_id,
                    type=d.type.value,
                    priority=d.priority.value,
                    opportunity_id=outcome.opportunity_id,
                    listing_id=listing.id,
                    watchlist_id=d.watchlist_id,
                    title=d.title,
                    body=d.body,
                    payload={
                        "flip_score": cand.flip_score,
                        "confidence": cand.confidence,
                        "risk": cand.risk,
                        "price": float(cand.price),
                        "previous_price": float(cand.previous_price) if cand.previous_price else None,
                        "expected_profit": float(profit) if profit is not None else None,
                        "expected_roi": float(roi) if roi is not None else None,
                        "url": listing.url,
                    },
                    dedupe_key=d.dedupe_key,
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
                .returning(Alert.id)
            )
            alert_id = (await session.execute(stmt)).scalar_one_or_none()
            if alert_id is None:
                continue  # already alerted for this item
            if d.watchlist_id:
                await session.execute(
                    Watchlist.__table__.update()
                    .where(Watchlist.id == d.watchlist_id)
                    .values(last_matched_at=now)
                )
            for channel in channels:
                in_app = channel == NotificationChannelType.IN_APP.value
                await session.execute(
                    pg_insert(AlertDelivery).values(
                        alert_id=alert_id,
                        channel=channel,
                        status=DeliveryStatus.SENT.value if in_app else DeliveryStatus.PENDING.value,
                        attempts=1 if in_app else 0,
                        sent_at=now if in_app else None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                if not in_app:
                    pending.append((alert_id, channel))
            log.info(
                "alerts.created", alert_type=d.type.value, priority=d.priority.value, user_id=str(a.user_id)
            )
    return pending


def build_channel(channel: str, settings: Settings) -> NotificationChannel:
    if channel == NotificationChannelType.TELEGRAM.value:
        if not settings.telegram_bot_token:
            raise ChannelError("telegram not configured", transient=False)
        return TelegramChannel(settings.telegram_bot_token.get_secret_value())
    if channel == NotificationChannelType.DISCORD.value:
        return DiscordChannel()
    if channel == NotificationChannelType.EMAIL.value:
        return EmailChannel(settings)
    if channel == NotificationChannelType.WEB_PUSH.value:
        return WebPushChannel(settings)
    raise ChannelError(f"unsupported channel {channel}", transient=False)


async def message_for_alert(session: AsyncSession, alert: Alert, settings: Settings) -> NotificationMessage:
    image = None
    if alert.listing_id:
        image = (
            await session.execute(
                select(ListingImage.url)
                .where(ListingImage.listing_id == alert.listing_id)
                .order_by(ListingImage.position)
                .limit(1)
            )
        ).scalar_one_or_none()
    app_url = (
        f"{settings.public_app_url.rstrip('/')}/deals/{alert.opportunity_id}"
        if alert.opportunity_id
        else settings.public_app_url
    )
    return NotificationMessage(
        title=alert.title,
        body=alert.body,
        app_url=app_url,
        listing_url=(alert.payload or {}).get("url"),
        priority=alert.priority,
        image_url=image if image and image.startswith("https://") else None,
    )


async def send_to_user_channel(
    session: AsyncSession, user_id: uuid.UUID, channel: str, message: NotificationMessage, settings: Settings
) -> None:
    """Send one message on one channel to one user (used by deliveries and test notifications)."""
    ns = await session.get(NotificationSettings, user_id)
    user = await session.get(User, user_id)
    if ns is None or user is None:
        raise ChannelError("user settings missing", transient=False)
    impl = build_channel(channel, settings)
    if channel == NotificationChannelType.WEB_PUSH.value:
        subs = (
            (await session.execute(select(PushSubscription).where(PushSubscription.user_id == user_id)))
            .scalars()
            .all()
        )
        if not subs:
            raise ChannelError("no push subscriptions", transient=False)
        errors: list[ChannelError] = []
        for sub in subs:
            target = json.dumps({"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}})
            try:
                await impl.send(message, target)
            except SubscriptionGone:
                await session.execute(delete(PushSubscription).where(PushSubscription.id == sub.id))
            except ChannelError as exc:
                errors.append(exc)
        if errors and len(errors) == len(subs):
            raise errors[0]
        return
    target: str | None = {
        NotificationChannelType.EMAIL.value: ns.email_address or user.email,
        NotificationChannelType.TELEGRAM.value: ns.telegram_chat_id,
        NotificationChannelType.DISCORD.value: ns.discord_webhook_url,
    }.get(channel)
    if not target:
        raise ChannelError(f"{channel} target missing", transient=False)
    await impl.send(message, target)


async def deliver_alert(session: AsyncSession, alert_id: uuid.UUID, channel: str) -> None:
    """Deliver an alert on a channel; raises ChannelError(transient=True) to request a retry."""
    settings = get_settings()
    alert = await session.get(Alert, alert_id)
    delivery = (
        await session.execute(
            select(AlertDelivery).where(AlertDelivery.alert_id == alert_id, AlertDelivery.channel == channel)
        )
    ).scalar_one_or_none()
    if alert is None or delivery is None or delivery.status == DeliveryStatus.SENT.value:
        return
    delivery.attempts += 1
    try:
        message = await message_for_alert(session, alert, settings)
        await send_to_user_channel(session, alert.user_id, channel, message, settings)
    except ChannelError as exc:
        delivery.status = DeliveryStatus.FAILED.value
        delivery.last_error = str(exc)[:500]
        log.warning(
            "alerts.delivery_failed",
            channel=channel,
            alert_id=str(alert_id),
            transient=exc.transient,
            attempts=delivery.attempts,
            error=str(exc)[:200],
        )
        if exc.transient:
            raise
        return
    delivery.status = DeliveryStatus.SENT.value
    delivery.sent_at = datetime.now(UTC)
    delivery.last_error = None
    log.info("alerts.delivered", channel=channel, alert_id=str(alert_id))
