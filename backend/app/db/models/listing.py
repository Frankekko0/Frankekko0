from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow

if TYPE_CHECKING:
    from app.db.models.catalog import Brand, Category
    from app.db.models.seller import Seller


class Listing(Base):
    """A marketplace listing, normalized, enriched by identification, never hard-deleted."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("provider", "external_id"),
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("btrim(url) <> ''", name="url_not_empty"),
        CheckConstraint(
            "published_at_kind IN ('exact', 'relative', 'reported', 'unknown')",
            name="published_at_kind_valid",
        ),
        Index("ix_listings_segment", "brand_id", "category_id", "status"),
        Index("ix_listings_published_at", "published_at"),
        Index("ix_listings_status_last_seen", "status", "last_seen_at"),
        Index("ix_listings_brand_model_status", "brand_id", "model_name", "status"),
        Index(
            "ix_listings_sold_segment",
            "brand_id",
            "category_id",
            "sold_at",
            postgresql_where=text("status = 'sold'"),
        ),
        Index(
            "ix_listings_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    price: Mapped[Decimal] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), default="EUR", server_default="EUR")

    brand_raw: Mapped[str | None] = mapped_column(String(120))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="SET NULL"))
    category_raw: Mapped[str | None] = mapped_column(String(120))
    subcategory_raw: Mapped[str | None] = mapped_column(String(120))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    size_raw: Mapped[str | None] = mapped_column(String(60))
    size_normalized: Mapped[str | None] = mapped_column(String(20), index=True)
    condition_raw: Mapped[str | None] = mapped_column(String(60))
    condition: Mapped[str] = mapped_column(String(24), default="unknown", server_default="unknown")
    color_raw: Mapped[str | None] = mapped_column(String(60))
    color: Mapped[str | None] = mapped_column(String(30))
    material_raw: Mapped[str | None] = mapped_column(String(120))
    material: Mapped[str | None] = mapped_column(String(30))
    country: Mapped[str | None] = mapped_column(String(2))

    # Identification results (denormalized for fast comparable queries).
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    model_name: Mapped[str | None] = mapped_column(String(120))
    gender: Mapped[str | None] = mapped_column(String(10))
    is_vintage: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    product_code: Mapped[str | None] = mapped_column(String(64))
    identification_confidence: Mapped[int | None] = mapped_column(SmallInteger)
    identification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    seller_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sellers.id", ondelete="SET NULL"), index=True
    )
    buyer_protection_fee: Mapped[Decimal | None] = mapped_column()
    shipping_fee: Mapped[Decimal | None] = mapped_column()
    buyer_protection_available: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    favourite_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    view_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    photo_count: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")

    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    # Publication date, only when the source gave one. ``published_at_kind`` says how: exact (a
    # timestamp read from the page), relative ("3 days ago", approximate), reported (a source that
    # does not say how it knows) or unknown (NULL: never replaced by the moment we first saw it).
    published_at: Mapped[datetime | None] = mapped_column()
    published_at_kind: Mapped[str] = mapped_column(String(8), default="unknown", server_default="unknown")
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    # Last time a capture read the listing's state (price/status) from the page or a card; link-only
    # records and unreachable checks do not count.
    last_verified_at: Mapped[datetime | None] = mapped_column()
    status_changed_at: Mapped[datetime | None] = mapped_column()
    # Sale: estimated moment (midpoint of the window below), first observation of "sold",
    # last price seen while open, days from publication to the estimated sale.
    sold_at: Mapped[datetime | None] = mapped_column()
    sold_detected_at: Mapped[datetime | None] = mapped_column()
    last_active_at: Mapped[datetime | None] = mapped_column()
    last_active_price: Mapped[Decimal | None] = mapped_column()
    days_to_sell: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    removed_at: Mapped[datetime | None] = mapped_column()

    # Traceability: how the listing first reached FlipFinder and how much of it is known.
    acquisition_mode: Mapped[str] = mapped_column(
        String(24), default="provider_scan", server_default="provider_scan"
    )
    capture_level: Mapped[str] = mapped_column(String(8), default="full", server_default="full")
    # Tracking: set when the user captured/tracked it (periodic checks), null for market data
    # seen only while scrolling or scanned from the provider.
    tracked_at: Mapped[datetime | None] = mapped_column()
    last_checked_at: Mapped[datetime | None] = mapped_column()
    next_check_at: Mapped[datetime | None] = mapped_column(index=True)
    check_failures: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    unchanged_checks: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")

    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), index=True
    )
    title_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow, onupdate=utcnow)

    @hybrid_property
    def listed_at(self) -> datetime:
        """When it appeared: the publication date if known, else the first observation.

        For recency (sorting, "published within"). Never use it as a publication date: ages and
        days-to-sell come from ``published_at`` only.
        """
        return self.published_at or self.first_seen_at

    @listed_at.inplace.expression
    @classmethod
    def _listed_at_expression(cls) -> Any:
        return func.coalesce(cls.published_at, cls.first_seen_at)

    images: Mapped[list[ListingImage]] = relationship(
        back_populates="listing",
        cascade="all, delete-orphan",
        order_by="ListingImage.position",
        lazy="selectin",
    )
    brand: Mapped[Brand | None] = relationship(lazy="joined")
    category: Mapped[Category | None] = relationship(lazy="joined")
    seller: Mapped[Seller | None] = relationship(lazy="joined")


class ListingImage(Base):
    __tablename__ = "listing_images"
    __table_args__ = (UniqueConstraint("listing_id", "position"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(SmallInteger)
    url: Mapped[str] = mapped_column(Text)
    phash: Mapped[str | None] = mapped_column(String(16), index=True)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # Internal copy taken at capture time (the original link can disappear after a sale).
    # Internal use only: served to signed-in users, never republished.
    local_path: Mapped[str | None] = mapped_column(String(200))
    sha256: Mapped[str | None] = mapped_column(String(64))
    content_type: Mapped[str | None] = mapped_column(String(40))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    archive_status: Mapped[str | None] = mapped_column(String(16))  # ok | failed | skipped
    archive_error: Mapped[str | None] = mapped_column(String(200))
    archive_attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    archived_at: Mapped[datetime | None] = mapped_column()

    listing: Mapped[Listing] = relationship(back_populates="images")


class ListingSnapshot(Base):
    """One observation of a listing (append-only: a new check adds a row, never overwrites)."""

    __tablename__ = "listing_snapshots"
    __table_args__ = (Index("ix_listing_snapshots_listing_observed", "listing_id", "observed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    observed_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    acquisition_mode: Mapped[str] = mapped_column(String(24))
    capture_level: Mapped[str | None] = mapped_column(String(8))
    # Null when the observation could not tell (e.g. a price-only email, an unreachable page).
    status: Mapped[str | None] = mapped_column(String(16))
    price: Mapped[Decimal | None] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), default="EUR", server_default="EUR")
    favourite_count: Mapped[int | None] = mapped_column(Integer)
    view_count: Mapped[int | None] = mapped_column(Integer)
    photo_count: Mapped[int | None] = mapped_column(SmallInteger)
    note: Mapped[str | None] = mapped_column(String(200))
    # Why this row exists: first, price, status, favourites, photos, richer, heartbeat (comma list).
    reason: Mapped[str | None] = mapped_column(String(40))
    # The fields the observation contained, each typed observed / declared / inferred.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Photo keys in the original order, and whether the set is the item's complete gallery.
    image_set: Mapped[list[str] | None] = mapped_column(JSONB)
    image_set_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    extension_version: Mapped[str | None] = mapped_column(String(20))
    parser_version: Mapped[str | None] = mapped_column(String(24))


class ListingPriceHistory(Base):
    """Read-only view (migration 0011): the price points of ``listing_snapshots`` - the first one
    seen and every change. There is no second copy to keep in step; never insert here."""

    __tablename__ = "listing_price_history"
    __table_args__ = (Index("ix_price_history_listing_observed", "listing_id", "observed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    price: Mapped[Decimal] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), default="EUR", server_default="EUR")
    observed_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
