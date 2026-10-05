from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Ratio, Timestamped, UUIDPk, utcnow


class Alert(UUIDPk, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key"),
        Index("ix_alerts_user_created", "user_id", text("created_at DESC")),
        Index("ix_alerts_user_unread", "user_id", postgresql_where=text("read_at IS NULL")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(24))
    priority: Mapped[str] = mapped_column(String(8), default="normal")
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL")
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("listings.id", ondelete="SET NULL"))
    watchlist_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("watchlists.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    read_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)


class AlertDelivery(Timestamped, Base):
    __tablename__ = "alert_deliveries"
    __table_args__ = (UniqueConstraint("alert_id", "channel"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column()


class Watchlist(UUIDPk, Timestamped, Base):
    """A personal buying strategy: filters + economic targets + notification switch."""

    __tablename__ = "watchlists"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    query: Mapped[str | None] = mapped_column(String(200))
    brand_slugs: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list, server_default="{}")
    category_slugs: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list, server_default="{}")
    sizes: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list, server_default="{}")
    conditions: Mapped[list[str]] = mapped_column(ARRAY(String(24)), default=list, server_default="{}")
    countries: Mapped[list[str]] = mapped_column(ARRAY(String(2)), default=list, server_default="{}")
    vintage_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    max_buy_price: Mapped[Decimal | None] = mapped_column()
    min_profit: Mapped[Decimal | None] = mapped_column()
    min_roi: Mapped[Decimal | None] = mapped_column(Ratio)
    min_flip_score: Mapped[int | None] = mapped_column(SmallInteger)
    min_confidence: Mapped[int | None] = mapped_column(SmallInteger)
    max_risk_score: Mapped[int | None] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notify: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_matched_at: Mapped[datetime | None] = mapped_column()
