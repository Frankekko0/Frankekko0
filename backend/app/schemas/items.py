"""Archive and tracking schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.schemas.common import Money, Ratio, Schema


class ItemOut(Schema):
    id: uuid.UUID
    vinted_id: str | None
    provider: str
    url: str
    title: str
    brand: str | None
    size: str | None
    condition: str
    price: Money
    currency: str
    status: str
    favourite_count: int
    image_url: str | None
    acquisition_mode: str
    capture_level: str
    tracked: bool
    first_seen_at: datetime
    last_checked_at: datetime | None
    last_verified_at: datetime | None = None
    next_check_at: datetime | None
    sold_at: datetime | None
    days_to_sell: float | None
    last_active_price: Money | None
    opportunity_id: uuid.UUID | None
    flip_score: int | None
    confidence_score: int | None
    risk_level: str | None
    data_quality: str | None
    expected_profit: Money | None
    expected_roi: Ratio | None
    fair_market_value: Money | None
    analyzed_at: datetime | None
    algorithm_version: str | None
    analysis_depth: str | None


class SnapshotOut(Schema):
    observed_at: datetime
    acquisition_mode: str
    capture_level: str | None
    status: str | None
    price: Money | None
    favourite_count: int | None
    view_count: int | None
    note: str | None


class AttemptOut(Schema):
    started_at: datetime
    mode: str
    action: str
    outcome: str
    http_status: int | None
    message: str | None
    duration_ms: int | None


class ItemImageOut(Schema):
    position: int
    url: str
    # Internal copy taken at capture time (works after the listing disappears); None until archived.
    local_url: str | None = None
    archive_status: str | None = None


class TrackingOut(Schema):
    tracked: bool
    tracked_at: datetime | None
    last_checked_at: datetime | None
    # Last time a capture confirmed the state; the basis of "da verificare".
    last_verified_at: datetime | None = None
    status_before_verify: str | None = None
    next_check_at: datetime | None
    check_failures: int
    status: str
    status_changed_at: datetime | None
    sold_at: datetime | None
    sold_detected_at: datetime | None
    last_active_at: datetime | None
    last_active_price: Money | None
    days_to_sell: float | None
    removed_at: datetime | None
    refresh_modes: list[str]


class ItemDetailOut(Schema):
    item: ItemOut
    description: str
    category: str | None
    color: str | None
    material: str | None
    view_count: int
    shipping_fee: Money | None
    buyer_protection_fee: Money | None
    published_at: datetime | None
    seller: dict[str, Any] | None
    images: list[ItemImageOut]
    tracking: TrackingOut
    snapshots: list[SnapshotOut]
    attempts: list[AttemptOut]
    analysis: dict[str, Any] | None
