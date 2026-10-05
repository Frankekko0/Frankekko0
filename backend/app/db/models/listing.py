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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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
        Index("ix_listings_segment", "brand_id", "category_id", "status"),
        Index("ix_listings_published_at", "published_at"),
        Index("ix_listings_status_last_seen", "status", "last_seen_at"),
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
    published_at: Mapped[datetime | None] = mapped_column()
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    status_changed_at: Mapped[datetime | None] = mapped_column()
    sold_at: Mapped[datetime | None] = mapped_column()
    removed_at: Mapped[datetime | None] = mapped_column()

    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), index=True
    )
    title_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow, onupdate=utcnow)

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

    listing: Mapped[Listing] = relationship(back_populates="images")


class ListingPriceHistory(Base):
    __tablename__ = "listing_price_history"
    __table_args__ = (Index("ix_price_history_listing_observed", "listing_id", "observed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    price: Mapped[Decimal] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), default="EUR", server_default="EUR")
    observed_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
