from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import BrandRef, CategoryRef, Money, Ratio, Schema

Preset = Literal[
    "best_deals",
    "high_profit",
    "high_roi",
    "fast_flip",
    "low_risk",
    "just_listed",
    "hidden_gems",
    "under_20",
    "ultra",
]
SortKey = Literal[
    "flip",
    "personal",
    "profit",
    "roi",
    "newest",
    "discount",
    "confidence",
    "velocity",
    "price_asc",
    "risk_asc",
]


class OpportunityFilters(Schema):
    """Feed filters. Money/ROI thresholds use the requesting user's cost profile."""

    q: str | None = Field(default=None, max_length=120, description="Free text on titles")
    brands: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    sizes: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    demand_levels: list[str] = Field(default_factory=list)
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    min_profit: float | None = None
    min_roi: float | None = Field(default=None, description="Ratio, 0.5 = 50%")
    min_flip: int | None = Field(default=None, ge=0, le=100)
    min_confidence: int | None = Field(default=None, ge=0, le=100)
    max_risk: int | None = Field(default=None, ge=0, le=100)
    min_velocity: int | None = Field(default=None, ge=0, le=100)
    published_within_hours: float | None = Field(default=None, gt=0, le=24 * 365)
    published_after: datetime | None = None
    published_before: datetime | None = None
    vintage_only: bool = False
    ultra_only: bool = False
    include_inactive: bool = False
    include_ignored: bool = False
    state: Literal["saved", "ignored", "purchased", "watching", "sold"] | None = None
    preset: Preset | None = None
    sort: SortKey = "flip"
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=24, ge=1, le=100)


class Reason(Schema):
    type: str
    code: str
    label: str
    impact: float | None = None


class OpportunityCard(Schema):
    id: uuid.UUID
    listing_id: uuid.UUID
    title: str
    brand: BrandRef | None
    category: CategoryRef | None
    model_name: str | None
    size: str | None
    condition: str
    country: str | None
    image_url: str | None
    url: str
    is_active: bool
    listing_status: str
    listing_price: Money
    currency: str
    total_acquisition_cost: Money
    fair_market_value: Money | None
    expected_sale_price: Money | None
    expected_profit: Money | None
    expected_roi: Ratio | None
    discount_vs_market: Ratio | None
    flip_score: int
    personal_flip_score: int | None
    confidence_score: int
    risk_score: int
    risk_level: str
    demand_level: str | None
    velocity_score: int | None
    estimated_days_to_sell: float | None
    velocity_bucket: str | None
    deal_tier: str
    is_ultra_deal: bool
    verdict: str
    recommended_action: str
    published_at: datetime | None
    analyzed_at: datetime
    favorite_state: str | None = None
    previous_price: Money | None = None
    top_reasons: list[Reason] = Field(default_factory=list)


class QuickStats(Schema):
    opportunities_today: int
    average_expected_roi: float | None
    potential_profit: float
    ultra_deals: int
    listings_analyzed: int
    active_opportunities: int
    listings_tracked: int
    last_scan_at: datetime | None


class ImageOut(Schema):
    url: str
    position: int


class SellerOut(Schema):
    """Only what the analysis needs: no username or other personal data."""

    rating: float | None
    review_count: int
    account_created_at: datetime | None
    item_count: int | None
    sold_count: int | None
    country: str | None
    reliability_score: int | None
    reliability: dict[str, Any] | None


class ListingDetailOut(Schema):
    id: uuid.UUID
    external_id: str
    provider: str
    url: str
    title: str
    description: str
    price: Money
    currency: str
    brand_raw: str | None
    category_raw: str | None
    size_raw: str | None
    condition_raw: str | None
    condition: str
    color: str | None
    material: str | None
    country: str | None
    status: str
    published_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    favourite_count: int
    view_count: int
    shipping_fee: Money | None
    buyer_protection_fee: Money | None
    buyer_protection_available: bool
    is_vintage: bool
    images: list[ImageOut]
    seller: SellerOut | None
    duplicate_of_id: uuid.UUID | None


class ComparableOut(Schema):
    listing_id: uuid.UUID
    title: str
    url: str
    price: Money
    adjusted_price: Money
    condition: str
    size: str | None
    country: str | None
    status: str
    similarity: float
    is_sold: bool
    included: bool
    exclusion_reason: str | None
    listing_date: datetime | None
    sold_at: datetime | None
    image_url: str | None


class CostLine(Schema):
    label: str
    amount: Money


class ScenarioOut(Schema):
    name: Literal["conservative", "expected", "optimistic"]
    sale_price: Money
    total_acquisition_cost: Money
    net_sale_revenue: Money
    net_profit: Money
    roi: Ratio
    estimated_days: float | None
    acquisition_breakdown: list[CostLine]
    sale_breakdown: list[CostLine]


class SmartBuyOut(Schema):
    max_buy_price: Money | None
    good_buy_price: Money | None
    suggested_offer: Money | None
    listed_price: Money
    action: str
    rationale: str
    min_profit: Money
    min_roi: Ratio


class PricePoint(Schema):
    price: Money
    observed_at: datetime


class OpportunityDetail(Schema):
    card: OpportunityCard
    listing: ListingDetailOut
    identification: dict[str, Any] | None
    market: dict[str, Any]
    comparables: list[ComparableOut]
    demand: dict[str, Any]
    velocity: dict[str, Any]
    scenarios: list[ScenarioOut]
    smart_buy: SmartBuyOut
    risk: dict[str, Any]
    score: dict[str, Any]
    explanation: list[Reason]
    ai_analysis: dict[str, Any] | None
    price_history: list[PricePoint]
    recommended_action: str


class FavoriteStateIn(Schema):
    state: Literal["saved", "ignored", "purchased", "watching", "sold"]
    note: str | None = Field(default=None, max_length=500)


class FavoriteOut(Schema):
    listing_id: uuid.UUID
    opportunity_id: uuid.UUID | None
    state: str
    note: str | None
    updated_at: datetime


class ProfitCalcIn(Schema):
    purchase_price: Money = Field(ge=0, le=100_000)
    sale_price: Money = Field(ge=0, le=100_000)
    shipping_fee: Money | None = Field(default=None, ge=0, le=1000)


class ProfitCalcOut(Schema):
    total_acquisition_cost: Money
    net_sale_revenue: Money
    net_profit: Money
    roi: Ratio
    acquisition_breakdown: list[CostLine]
    sale_breakdown: list[CostLine]
    max_buy_price: Money | None
