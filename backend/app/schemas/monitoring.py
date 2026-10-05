from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from app.schemas.common import Money, Ratio, Schema

CONDITIONS = {"new_with_tags", "new_without_tags", "very_good", "good", "satisfactory"}


class WatchlistIn(Schema):
    name: str = Field(min_length=1, max_length=120)
    query: str | None = Field(default=None, max_length=200)
    brand_slugs: list[str] = Field(default_factory=list, max_length=50)
    category_slugs: list[str] = Field(default_factory=list, max_length=50)
    sizes: list[str] = Field(default_factory=list, max_length=30)
    conditions: list[str] = Field(default_factory=list, max_length=5)
    countries: list[str] = Field(default_factory=list, max_length=30)
    vintage_only: bool = False
    max_buy_price: Money | None = Field(default=None, gt=0, le=100_000)
    min_profit: Money | None = Field(default=None, ge=-1000, le=100_000)
    min_roi: Ratio | None = Field(default=None, ge=-1, le=100)
    min_flip_score: int | None = Field(default=None, ge=0, le=100)
    min_confidence: int | None = Field(default=None, ge=0, le=100)
    max_risk_score: int | None = Field(default=None, ge=0, le=100)
    is_active: bool = True
    notify: bool = True

    @field_validator("conditions")
    @classmethod
    def _conditions(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in CONDITIONS]
        if bad:
            raise ValueError("condizione non valida")
        return v

    @field_validator("countries")
    @classmethod
    def _countries(cls, v: list[str]) -> list[str]:
        return [c.upper()[:2] for c in v]

    @field_validator("sizes")
    @classmethod
    def _sizes(cls, v: list[str]) -> list[str]:
        return [s.upper()[:20] for s in v]


class WatchlistOut(WatchlistIn):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    last_matched_at: datetime | None
    match_count: int | None = None


class AlertOut(Schema):
    id: uuid.UUID
    type: str
    priority: str
    title: str
    body: str
    payload: dict[str, Any]
    opportunity_id: uuid.UUID | None
    listing_id: uuid.UUID | None
    watchlist_id: uuid.UUID | None
    read_at: datetime | None
    created_at: datetime
    deliveries: list[dict[str, Any]] = Field(default_factory=list)


class UnreadCount(Schema):
    unread: int
    latest_high_priority: AlertOut | None = None


class PushSubscriptionIn(Schema):
    endpoint: str = Field(min_length=10, max_length=2000)
    keys: dict[str, str]

    @field_validator("endpoint")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("endpoint non valido")
        return v

    @field_validator("keys")
    @classmethod
    def _keys(cls, v: dict[str, str]) -> dict[str, str]:
        if not v.get("p256dh") or not v.get("auth"):
            raise ValueError("chiavi mancanti")
        return v
