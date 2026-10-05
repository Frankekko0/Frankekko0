from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Ratio, Timestamped, UUIDPk, utcnow


class User(UUIDPk, Timestamped, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_login_at: Mapped[datetime | None] = mapped_column()

    preferences: Mapped[UserPreferences] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )
    notification_settings: Mapped[NotificationSettings] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    locale: Mapped[str] = mapped_column(String(8), default="it", server_default="it")
    currency: Mapped[str] = mapped_column(String(3), default="EUR", server_default="EUR")
    preferred_brands: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list, server_default="{}")
    preferred_categories: Mapped[list[str]] = mapped_column(
        ARRAY(String(80)), default=list, server_default="{}"
    )
    sizes: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list, server_default="{}")
    min_profit: Mapped[Decimal] = mapped_column(default=Decimal("10"), server_default="10")
    min_roi: Mapped[Decimal] = mapped_column(Ratio, default=Decimal("0.40"), server_default="0.40")
    max_purchase_price: Mapped[Decimal | None] = mapped_column()
    min_flip_score: Mapped[int | None] = mapped_column(SmallInteger)
    max_risk_score: Mapped[int | None] = mapped_column(SmallInteger)
    min_confidence: Mapped[int | None] = mapped_column(SmallInteger)
    # Validated by app.schemas.settings.CostProfile; JSONB keeps the profile evolvable.
    cost_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    score_weights: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    personalization_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="preferences")


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    web_push_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    email_address: Mapped[str | None] = mapped_column(String(320))
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    discord_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Discord webhook URLs are bearer secrets: never logged, masked in API responses.
    discord_webhook_url: Mapped[str | None] = mapped_column(Text)

    new_opportunity_alerts: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    ultra_deal_alerts: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    price_drop_alerts: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    watchlist_alerts: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    alert_min_flip_score: Mapped[int] = mapped_column(SmallInteger, default=85, server_default="85")
    alert_min_roi: Mapped[Decimal] = mapped_column(Ratio, default=Decimal("0.50"), server_default="0.50")
    alert_min_profit: Mapped[Decimal] = mapped_column(default=Decimal("15"), server_default="15")
    alert_min_confidence: Mapped[int] = mapped_column(SmallInteger, default=70, server_default="70")
    alert_max_risk_score: Mapped[int | None] = mapped_column(SmallInteger, default=60, server_default="60")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="notification_settings")


class PushSubscription(UUIDPk, Base):
    __tablename__ = "push_subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
