"""Autonomy limits and actions, business goals, expenses, forecast outcomes and the experiment registry."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Ratio, UUIDPk, utcnow


class AutonomySettings(Base):
    """What the system may do on its own for a user, and the switches that stop it."""

    __tablename__ = "autonomy_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    mode: Mapped[str] = mapped_column(String(12), default="dry_run", server_default="dry_run")
    dry_run_until: Mapped[datetime | None] = mapped_column()
    killed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    suspended_at: Mapped[datetime | None] = mapped_column()
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow, onupdate=utcnow)


class AutonomyAction(UUIDPk, Base):
    """One thing the system decided to do: carried out through a channel, recorded as a dry run, or blocked."""

    __tablename__ = "autonomy_actions"
    __table_args__ = (Index("ix_autonomy_actions_user", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL")
    )
    inventory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory.id", ondelete="SET NULL"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(16))  # dry_run | blocked | pending_user | done | failed
    reasons: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    channel: Mapped[str] = mapped_column(String(16))
    verifier: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column()


class PredictionOutcome(UUIDPk, Base):
    """Forecast against reality for one closed sale (closed loop learning)."""

    __tablename__ = "prediction_outcomes"
    __table_args__ = (Index("ix_prediction_outcomes_user", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"), unique=True)
    purchase_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL")
    )
    brand_name: Mapped[str | None] = mapped_column(String(120))
    category_name: Mapped[str | None] = mapped_column(String(120))
    price_band: Mapped[str | None] = mapped_column(String(12))
    verdict_at_buy: Mapped[str | None] = mapped_column(String(24))
    predicted_price: Mapped[Decimal | None] = mapped_column()
    predicted_days: Mapped[Decimal | None] = mapped_column()
    predicted_profit: Mapped[Decimal | None] = mapped_column()
    predicted_p_sale: Mapped[Decimal | None] = mapped_column(Ratio)
    actual_price: Mapped[Decimal] = mapped_column()
    actual_days: Mapped[int] = mapped_column(Integer)
    actual_profit: Mapped[Decimal] = mapped_column()
    price_error_pct: Mapped[Decimal | None] = mapped_column(Ratio)
    days_error: Mapped[Decimal | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)


class BusinessGoals(Base):
    __tablename__ = "business_goals"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    monthly_profit_target: Mapped[Decimal | None] = mapped_column()
    initial_capital: Mapped[Decimal | None] = mapped_column()
    max_capital: Mapped[Decimal | None] = mapped_column()
    weekly_hours: Mapped[int | None] = mapped_column(SmallInteger)
    horizon_months: Mapped[int] = mapped_column(SmallInteger, default=12, server_default="12")
    reinvest_pct: Mapped[Decimal] = mapped_column(Ratio, default=Decimal("0.7"), server_default="0.7")
    min_reserve: Mapped[Decimal] = mapped_column(default=Decimal("0"), server_default="0")
    explore_share: Mapped[Decimal] = mapped_column(Ratio, default=Decimal("0.10"), server_default="0.10")
    holder_status: Mapped[str] = mapped_column(String(16), default="private", server_default="private")
    # [{"name", "amount", "period", "source", "as_of"}]: entered by the user from an official source.
    tax_thresholds: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow, onupdate=utcnow)


class Expense(UUIDPk, Base):
    """A cost that is not a purchase: materials, packaging, shipping supplies, labour, refurbishing."""

    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        Index("ix_expenses_user_date", "user_id", "spent_on"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    spent_on: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column()
    note: Mapped[str | None] = mapped_column(Text)
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchases.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)


class Experiment(UUIDPk, Base):
    """Registry of hypotheses and their results: a failed experiment is not repeated."""

    __tablename__ = "experiments"

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    hypothesis: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16))  # running | promoted | rejected | inconclusive
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column()
