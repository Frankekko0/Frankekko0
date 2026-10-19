"""Browser extension: pairing keys, captures and the quick evaluations sent back to it."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.marketplace.base import ManualListingInput
from app.schemas.common import Money, Ratio, Schema

# Only pages the user opened and scrolled: the extension reads nothing on its own.
PageType = Literal["catalog", "closet", "favourites", "item", "other"]


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


class CardError(BaseModel):
    """A card of a capture that was not stored, and why (the others of the same request were)."""

    vinted_id: str | None = None
    message: str


class CaptureCardsIn(BaseModel):
    page_type: PageType = "catalog"
    page_url: str = Field(default="", max_length=2000)
    items: list[ManualListingInput] = Field(min_length=1, max_length=100)
    extension_version: str | None = Field(default=None, max_length=20)
    parser_version: str | None = Field(default=None, max_length=24)
    # Cards that did not pass validation (filled by the validator below, never read from the request).
    rejected: list[CardError] = Field(default_factory=list, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _one_bad_card_is_not_the_page(cls, data: object) -> object:
        """A card that does not validate (a price of zero, a title that is too short...) is left out and named in the
        answer; the other cards of the request are stored. Only when no card is valid does the request fail."""
        if not isinstance(data, dict) or not isinstance(data.get("items"), list) or len(data["items"]) < 2:
            return data
        good: list[object] = []
        rejected: list[dict[str, str | None]] = []
        for raw in data["items"]:
            try:
                ManualListingInput.model_validate(raw)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(p) for p in first.get("loc", ()))
                rejected.append(
                    {
                        "vinted_id": _vinted_id(raw),
                        "message": f"Scheda non valida ({where}): {first['msg']}"[:300],
                    }
                )
            else:
                good.append(raw)
        if not good or not rejected:
            return data
        return {**data, "items": good, "rejected": rejected}


def _vinted_id(raw: object) -> str | None:
    url = raw.get("url") if isinstance(raw, dict) else None
    found = re.search(r"/items/(\d{1,20})", url) if isinstance(url, str) else None
    return found.group(1) if found else None


class CaptureCardsOut(BaseModel):
    received: int
    stored: int
    evaluations: list[QuickEval]
    # Cards left out (invalid, or the server could not process them): the rest of the request was stored.
    errors: list[CardError] = []


class CaptureItemIn(BaseModel):
    item: ManualListingInput
    # An item page the user opened (the extension reads nothing else).
    mode: Literal["extension_item"] = "extension_item"
    # None keeps the default of the mode.
    track: bool | None = None
    extension_version: str | None = Field(default=None, max_length=20)
    parser_version: str | None = Field(default=None, max_length=24)


class CaptureItemOut(BaseModel):
    evaluation: QuickEval | None
    analysis: dict[str, object] | None
    # Photos of this item the server has no copy of yet (``image_key``, ``position``, ``url``): the
    # extension uploads the ones its page has loaded. The server never downloads from Vinted.
    photos_wanted: list[dict[str, object]] = []
    # True while the strong model's second opinion on this item is still to come: the verdict shown is the rules'
    # (the model can only lower it), so it is provisional until the review lands.
    review_pending: bool = False


class GoneIn(BaseModel):
    """The user's browser opened a listing page that says the item is no longer there."""

    vinted_id: str = Field(pattern=r"^\d{1,20}$")
    # When the page was read (the extension queues what it cannot send; the server clamps it to the last 48 hours).
    observed_at: datetime | None = None
    extension_version: str | None = Field(default=None, max_length=20)
    parser_version: str | None = Field(default=None, max_length=24)


class TrackIn(BaseModel):
    url: str = Field(min_length=10, max_length=2000)
    track: bool = True


class EvaluationsIn(BaseModel):
    vinted_ids: list[str] = Field(min_length=1, max_length=200)


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
