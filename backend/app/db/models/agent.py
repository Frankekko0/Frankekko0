"""AI spend, agent runs and the append-only event log (audit trail)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class AiUsage(Base):
    """One row per paid model call: what it cost, so a daily and a monthly cap can stop spending."""

    __tablename__ = "ai_usage"
    __table_args__ = (Index("ix_ai_usage_day", "day"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    day: Mapped[date] = mapped_column(Date)
    purpose: Mapped[str] = mapped_column(String(48))
    model: Mapped[str] = mapped_column(String(64))
    tier: Mapped[str] = mapped_column(String(8))  # cheap | strong
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    ref: Mapped[str | None] = mapped_column(String(64))  # the listing / run the call was for


class AgentRun(Base):
    """One run of the agent loop: its steps (the trace), cost and validated result."""

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_started", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    # running | succeeded | stopped (budget, step limit) | failed
    status: Mapped[str] = mapped_column(String(12))
    provider: Mapped[str] = mapped_column(String(16))  # anthropic | rules | scripted
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column()
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))
    error: Mapped[str | None] = mapped_column(Text)


class Event(Base):
    """Append-only audit log: who did what to what, and why. Rows are never changed or deleted."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_kind_at", "kind", "at"),
        Index("ix_events_subject", "subject_type", "subject_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    kind: Mapped[str] = mapped_column(String(48))
    actor: Mapped[str] = mapped_column(String(32))  # system | agent | user | worker
    subject_type: Mapped[str | None] = mapped_column(String(24))
    subject_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
