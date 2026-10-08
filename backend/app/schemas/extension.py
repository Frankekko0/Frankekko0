"""Browser extension: pairing keys, captures and the quick evaluations sent back to it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.marketplace.base import ManualListingInput
from app.schemas.common import Money, Ratio, Schema

# "scan": a saved search read by the extension's optional automatic scanner (not a page the user opened).
PageType = Literal["catalog", "closet", "favourites", "item", "other", "scan"]


class ApiKeyIn(BaseModel):
    name: str = Field(default="Browser extension", min_length=1, max_length=80)


class ApiKeyOut(Schema):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    # Shown once: only its hash is stored.
    key: str


class QuickEval(Schema):
    """What the extension shows on a card and in the live panel."""

    vinted_id: str
    listing_id: uuid.UUID
    opportunity_id: uuid.UUID | None
    url: str
    title: str
    brand: str | None
    size: str | None
    image_url: str | None
    status: str
    tracked: bool
    capture_level: str
    # quick = search-card data only ("visto in scorrimento"), full = whole listing ("analizzato a fondo").
    analysis_depth: str | None
    data_quality: str | None
    insufficient_reason: str | None
    # None when the data cannot support a score ("dati insufficienti") or nothing was analysed.
    flip_score: int | None
    confidence: int | None
    risk_level: str | None
    # Counterfeit risk from the risk signals: none | low | medium | high | unknown.
    fake_risk: str
    price: Money
    currency: str
    total_cost: Money | None
    resale_expected: Money | None
    net_margin: Money | None
    roi: Ratio | None
    days_to_sell: float | None
    # Net margin x P(sold within 30 days) x P(authentic): the ranking key.
    risk_adjusted_profit: Money | None = None
    sale_probability: Ratio | None = None
    authenticity_probability: Ratio | None = None
    authenticity_verdict: str | None = None
    reason: str | None
    analyzed_at: datetime | None


class CaptureCardsIn(BaseModel):
    page_type: PageType = "catalog"
    page_url: str = Field(default="", max_length=2000)
    items: list[ManualListingInput] = Field(min_length=1, max_length=100)
    extension_version: str | None = Field(default=None, max_length=20)


class CaptureCardsOut(BaseModel):
    received: int
    stored: int
    evaluations: list[QuickEval]


class CaptureItemIn(BaseModel):
    item: ManualListingInput
    # extension_item: an item page the user opened; extension_deep: deep analysis the user asked
    # for (or a slow automatic one on a top candidate); extension_refresh: status check of a
    # tracked item.
    mode: Literal["extension_item", "extension_deep", "extension_refresh"] = "extension_item"
    # None keeps the default of the mode (deep analyses on command are tracked).
    track: bool | None = None
    extension_version: str | None = Field(default=None, max_length=20)


class CaptureItemOut(BaseModel):
    evaluation: QuickEval | None
    analysis: dict[str, object] | None


class TrackIn(BaseModel):
    url: str = Field(min_length=10, max_length=2000)
    track: bool = True


class EvaluationsIn(BaseModel):
    vinted_ids: list[str] = Field(min_length=1, max_length=200)


class RefreshResultIn(BaseModel):
    vinted_id: str = Field(pattern=r"^\d{1,20}$")
    outcome: Literal["not_found", "blocked", "error"]
    http_status: int | None = Field(default=None, ge=100, le=599)
    message: str | None = Field(default=None, max_length=300)


MAX_PAGE_STATS_ITEMS = 120


class PageStatsItem(BaseModel):
    """A card as the search page shows it (brand, size and condition labels as printed)."""

    vinted_id: str = Field(pattern=r"^\d{1,20}$")
    title: str = Field(default="", max_length=300)
    brand: str | None = Field(default=None, max_length=120)
    size: str | None = Field(default=None, max_length=60)
    condition: str | None = Field(default=None, max_length=60)


class PageStatsIn(BaseModel):
    items: list[PageStatsItem] = Field(min_length=1, max_length=MAX_PAGE_STATS_ITEMS)


class PageStat(BaseModel):
    """Pre-computed price statistics of the most specific segment with data (``model_price_stats``)."""

    segment: str
    level: Literal["model_size_condition", "model_condition", "model_size", "model", "brand_category"]
    basis: Literal["sold", "asking"]
    median: float
    low: float
    high: float
    n_sales: int
    n_own: int
    n_vinted_sold: int
    n_external_sold: int
    n_asking: int
    days: float | None
    sell_through: float | None
    new_price: float | None
    model: str | None
    brand: str | None
    category: str | None


class PageStatsOut(BaseModel):
    generated_at: datetime
    # Vinted id -> statistics; items without data are omitted.
    stats: dict[str, PageStat]
