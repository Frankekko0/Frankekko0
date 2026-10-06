"""Marketplace provider abstraction.

The rest of the application depends only on :class:`MarketplaceProvider` and these DTOs.
Adding Vinted (via an authorized integration), eBay, Depop or Wallapop means writing one
adapter that maps the source's payloads to :class:`ProviderListing`; ingestion, analysis,
scoring, alerts and the UI do not change.

Adapters MUST respect the source's terms of service: no CAPTCHA solving, no anti-bot evasion,
no rate-limit circumvention, no unauthorized account access. ``ProviderCapabilities`` lets an
adapter declare what it can legitimately provide (e.g. sold data) so the engines adapt.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.domain.enums import ListingStatus


class ProviderImage(BaseModel):
    url: str
    phash: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    width: int | None = None
    height: int | None = None


class ProviderSeller(BaseModel):
    external_id: str
    username: str | None = None
    rating: Decimal | None = Field(default=None, ge=0, le=5)
    review_count: int = Field(default=0, ge=0)
    account_created_at: datetime | None = None
    item_count: int | None = Field(default=None, ge=0)
    sold_count: int | None = Field(default=None, ge=0)
    country: str | None = None
    last_active_at: datetime | None = None


class ProviderListing(BaseModel):
    """A listing as delivered by a provider (raw-ish, before normalization)."""

    external_id: str = Field(min_length=1, max_length=64)
    url: str
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    price: Decimal = Field(ge=0)
    currency: str = "EUR"
    brand: str | None = None
    category: str | None = None
    subcategory: str | None = None
    size: str | None = None
    condition: str | None = None
    color: str | None = None
    material: str | None = None
    images: list[ProviderImage] = Field(default_factory=list)
    seller: ProviderSeller | None = None
    country: str | None = None
    published_at: datetime | None = None
    status: ListingStatus = ListingStatus.ACTIVE
    sold_at: datetime | None = None
    buyer_protection_fee: Decimal | None = Field(default=None, ge=0)
    shipping_fee: Decimal | None = Field(default=None, ge=0)
    buyer_protection_available: bool = True
    favourite_count: int = Field(default=0, ge=0)
    view_count: int = Field(default=0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()[:3]


class PricePoint(BaseModel):
    price: Decimal
    currency: str = "EUR"
    observed_at: datetime


class SearchQuery(BaseModel):
    since: datetime | None = None
    until: datetime | None = None
    cursor: str | None = None
    page_size: int = Field(default=200, ge=1, le=1000)
    brands: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    max_price: Decimal | None = None


class SearchPage(BaseModel):
    listings: list[ProviderListing]
    next_cursor: str | None = None
    has_more: bool = False


class ComparableQuery(BaseModel):
    brand: str
    category: str | None = None
    model_name: str | None = None
    size: str | None = None
    since: datetime | None = None
    include_sold: bool = True
    limit: int = Field(default=200, ge=1, le=1000)


class ProviderCapabilities(BaseModel):
    search: bool = True
    sold_data: bool = False
    price_history: bool = False
    seller_details: bool = False
    comparables: bool = False
    realtime: bool = False


class ManualListingInput(BaseModel):
    """Data a user can paste for a listing they found (manual import)."""

    url: HttpUrl
    title: str = Field(min_length=3, max_length=300)
    price: Decimal = Field(gt=0, le=100000)
    brand: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    size: str | None = Field(default=None, max_length=60)
    condition: str | None = Field(default=None, max_length=60)
    color: str | None = Field(default=None, max_length=60)
    description: str = Field(default="", max_length=5000)
    image_urls: list[HttpUrl] = Field(default_factory=list, max_length=20)
    seller_username: str | None = Field(default=None, max_length=120)
    seller_rating: Decimal | None = Field(default=None, ge=0, le=5)
    seller_review_count: int | None = Field(default=None, ge=0)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    shipping_fee: Decimal | None = Field(default=None, ge=0, le=1000)


MAX_BATCH_IMPORT = 200


class BatchImportInput(BaseModel):
    """Several listings the user is looking at (e.g. a marketplace search-results page)."""

    items: list[ManualListingInput] = Field(min_length=1, max_length=MAX_BATCH_IMPORT)
    source: Literal["vinted_search", "manual"] = "manual"


class MarketplaceProvider(ABC):
    """Interface every marketplace adapter implements."""

    name: str = "abstract"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    @abstractmethod
    async def search_listings(self, query: SearchQuery) -> SearchPage:
        """Return listings published/updated in the query window, oldest first, paginated."""

    @abstractmethod
    async def get_listing(self, external_id: str) -> ProviderListing | None:
        """Return the current state of a listing, or ``None`` if it no longer exists."""

    async def get_seller(self, external_id: str) -> ProviderSeller | None:
        return None

    async def get_comparable_listings(self, query: ComparableQuery) -> list[ProviderListing]:
        """Provider-side comparable search (sold + active) when the source supports it."""
        return []

    async def get_listing_history(self, external_id: str) -> list[PricePoint]:
        return []

    async def close(self) -> None:  # noqa: B027 - optional hook
        """Release network resources."""
