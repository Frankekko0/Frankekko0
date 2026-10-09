"""Price evidence beyond the listings themselves: concluded sales, external price references and
pre-computed statistics per brand, model, size and condition.

* ``SoldSale`` - one concluded sale, from the most to the least reliable source: the user's own
  purchases and resales (the price really paid or received), Vinted listings seen turning to
  "sold" (the last price seen, not necessarily the price paid), sales published by other
  marketplaces found by the external search.
* ``ExternalPrice`` - one price found by the external search for an exact model: new (retail),
  asked (second-hand on sale) or sold, always with source, date, currency and condition.
* ``ExternalSearch`` - the per-model cache of the external search: when it was last searched and
  when it is due again (never searched while a page is analysed).
* ``ModelPriceStat`` - statistics pre-computed per brand, category, model, size and condition
  (``*`` = any), read with one query per page.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Ratio, utcnow

# Sources of a concluded sale, most reliable first (``reliability`` = position, 1 = best).
SOLD_SOURCES = ("own_sale", "own_purchase", "vinted_sold", "external_sold")
# What the stored price is: really paid / really received / last price seen before the sale /
# price reported by another marketplace.
PRICE_KINDS = ("paid", "received", "last_seen", "reported")
EXTERNAL_KINDS = ("new", "asking", "sold")


class SoldSale(Base):
    """A concluded sale. ``dedupe_key`` makes every sync idempotent
    (``sale:<id>``, ``purchase:<id>``, ``vinted:<external_id>``, ``ext:<external_price_id>``)."""

    __tablename__ = "sold_sales"
    __table_args__ = (
        UniqueConstraint("dedupe_key"),
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint(
            "(price_kind = 'last_seen' AND asking_price IS NOT NULL AND realized_price IS NULL)"
            " OR (price_kind IN ('paid', 'received') AND realized_price IS NOT NULL AND asking_price IS NULL)"
            " OR (price_kind = 'reported' AND asking_price IS NULL AND realized_price IS NULL)",
            name="price_kind_columns",
        ),
        Index(
            "ix_sold_sales_segment", "brand_id", "category_id", "model_name", "size_normalized", "condition"
        ),
        Index("ix_sold_sales_brand_model", "brand_id", "model_name"),
        Index("ix_sold_sales_sold_at", "sold_at"),
        Index("ix_sold_sales_source", "source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(16))  # one of SOLD_SOURCES
    reliability: Mapped[int] = mapped_column(SmallInteger)  # 1 own sale ... 4 external sold
    price_kind: Mapped[str] = mapped_column(String(12))  # one of PRICE_KINDS
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), index=True
    )
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"))
    sale_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"))
    external_price_id: Mapped[int | None] = mapped_column(
        ForeignKey("external_prices.id", ondelete="CASCADE")
    )

    title: Mapped[str] = mapped_column(String(300))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="SET NULL"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    model_name: Mapped[str | None] = mapped_column(String(120))
    size_normalized: Mapped[str | None] = mapped_column(String(20))
    condition: Mapped[str] = mapped_column(String(24), default="unknown", server_default="unknown")

    price: Mapped[Decimal] = mapped_column()  # the evidence figure, in ``currency``; see ``price_kind``
    # Never mixed: what was asked (last price seen on sale) vs what was really paid or received.
    asking_price: Mapped[Decimal | None] = mapped_column()
    realized_price: Mapped[Decimal | None] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), default="EUR", server_default="EUR")
    price_eur: Mapped[Decimal] = mapped_column()  # converted (same as price for EUR)
    sold_at: Mapped[datetime] = mapped_column()
    published_at: Mapped[datetime | None] = mapped_column()
    days_to_sell: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    # A sale noticed on Vinted happened between these two moments; ``sold_at`` is their midpoint.
    window_start: Mapped[datetime | None] = mapped_column()
    window_end: Mapped[datetime | None] = mapped_column()
    # First sighting -> sale: a lower bound of the days online (the publication date may be unknown).
    observed_days: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    source_name: Mapped[str] = mapped_column(String(80))  # "tracking", "vinted", "ebay.it", ...
    source_url: Mapped[str | None] = mapped_column(Text)
    # Outlier cleaning (robust, per model segment): excluded rows stay for traceability.
    is_outlier: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow, onupdate=utcnow)


class ExternalPrice(Base):
    """A price found by the external search for an exact model (never for a different model, a
    kids' size, a replica or a lot: those are rejected before storing)."""

    __tablename__ = "external_prices"
    __table_args__ = (
        UniqueConstraint("dedupe_key"),
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("kind IN ('new', 'asking', 'sold')", name="kind_valid"),
        Index("ix_external_prices_model_kind", "model_key", "kind"),
        Index("ix_external_prices_brand_model", "brand_id", "model_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(80))  # sha256(kind|url|price)[:40]
    model_key: Mapped[str] = mapped_column(String(160))  # "<brand_slug>|<model name folded>"
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    model_name: Mapped[str] = mapped_column(String(120))

    kind: Mapped[str] = mapped_column(String(8))  # new | asking | sold
    price: Mapped[Decimal] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3))
    price_eur: Mapped[Decimal] = mapped_column()
    condition: Mapped[str] = mapped_column(String(24), default="unknown", server_default="unknown")
    size: Mapped[str | None] = mapped_column(String(20))

    source: Mapped[str] = mapped_column(String(80))  # domain or shop name, e.g. "ebay.it"
    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(300))
    snippet: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(16))  # "serper"
    query: Mapped[str] = mapped_column(String(300))
    observed_at: Mapped[datetime] = mapped_column()  # when the search found it
    # When the source dates it (a sale date, a publication date); null when it does not.
    source_date: Mapped[datetime | None] = mapped_column()
    match_score: Mapped[Decimal] = mapped_column(Ratio)
    match: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # tokens matched, checks passed
    is_outlier: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)


class ExternalSearch(Base):
    """Per-model cache of the external search (one row per model, refreshed periodically)."""

    __tablename__ = "external_searches"

    model_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    model_name: Mapped[str] = mapped_column(String(120))
    # How often the model was seen in analyses since the last search (refresh priority).
    demand: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    last_searched_at: Mapped[datetime | None] = mapped_column()
    next_refresh_at: Mapped[datetime | None] = mapped_column(index=True)
    queries_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    results: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # counts kept/rejected per reason
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow, onupdate=utcnow)


class ModelPriceStat(Base):
    """Pre-computed price statistics of one segment (``*`` = any in ``segment_key``).

    Concluded sales are weighted by source (own > Vinted sold > external sold); asking prices
    only fill in when sales are missing (``price_basis = 'asking'``)."""

    __tablename__ = "model_price_stats"
    __table_args__ = (
        UniqueConstraint("segment_key"),
        Index("ix_model_price_stats_lookup", "brand_id", "model_name", "size_normalized", "condition"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # "<brand_id>|<category_id>|<model folded>|<size>|<condition>", "*" for any.
    segment_key: Mapped[str] = mapped_column(String(255))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    model_name: Mapped[str | None] = mapped_column(String(120))
    size_normalized: Mapped[str | None] = mapped_column(String(20))
    condition: Mapped[str | None] = mapped_column(String(24))

    price_basis: Mapped[str] = mapped_column(String(8))  # sold | asking
    median_price: Mapped[Decimal] = mapped_column()
    low_price: Mapped[Decimal] = mapped_column()  # weighted P25
    high_price: Mapped[Decimal] = mapped_column()  # weighted P75
    n_samples: Mapped[int] = mapped_column(Integer)  # rows behind median/low/high
    n_sales: Mapped[int] = mapped_column(Integer)  # concluded sales (all sources)
    n_own: Mapped[int] = mapped_column(Integer)
    n_vinted_sold: Mapped[int] = mapped_column(Integer)
    n_external_sold: Mapped[int] = mapped_column(Integer)
    n_asking: Mapped[int] = mapped_column(Integer)
    n_outliers: Mapped[int] = mapped_column(Integer)
    avg_days_to_sell: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    median_days_to_sell: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    n_seen: Mapped[int] = mapped_column(Integer)  # Vinted listings seen in the window
    sell_through: Mapped[Decimal | None] = mapped_column(Ratio)  # sold / seen (Vinted)
    median_new_price: Mapped[Decimal | None] = mapped_column()  # external "new" prices
    n_new: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    negotiation_discount: Mapped[Decimal | None] = mapped_column(Ratio)  # applied to last_seen
    sources: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # per-source counts, weights
    window_days: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
