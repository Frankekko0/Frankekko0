from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Ratio, utcnow


class MarketComparable(Base):
    """A comparable listing used in the market valuation of a subject listing."""

    __tablename__ = "market_comparables"
    __table_args__ = (UniqueConstraint("listing_id", "comparable_listing_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    comparable_listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    similarity: Mapped[Decimal] = mapped_column(Ratio)
    price: Mapped[Decimal] = mapped_column()
    adjusted_price: Mapped[Decimal] = mapped_column()
    weight: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    is_sold: Mapped[bool] = mapped_column(Boolean, default=False)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    exclusion_reason: Mapped[str | None] = mapped_column(String(40))
    computed_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)


class MarketStatistic(Base):
    """Market database: aggregated price/demand statistics for a product segment."""

    __tablename__ = "market_statistics"
    __table_args__ = (
        UniqueConstraint("segment_key"),
        Index("ix_market_statistics_brand_category", "brand_id", "category_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    segment_key: Mapped[str] = mapped_column(String(255))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    model_name: Mapped[str | None] = mapped_column(String(120))
    size_normalized: Mapped[str | None] = mapped_column(String(20))
    sample_size: Mapped[int] = mapped_column(Integer)
    sold_count: Mapped[int] = mapped_column(Integer)
    active_count: Mapped[int] = mapped_column(Integer)
    median_price: Mapped[Decimal] = mapped_column()
    mean_price: Mapped[Decimal] = mapped_column()
    p25_price: Mapped[Decimal] = mapped_column()
    p75_price: Mapped[Decimal] = mapped_column()
    min_reasonable_price: Mapped[Decimal] = mapped_column()
    max_reasonable_price: Mapped[Decimal] = mapped_column()
    avg_listing_price: Mapped[Decimal] = mapped_column()
    median_sold_price: Mapped[Decimal | None] = mapped_column()
    ask_to_sale_ratio: Mapped[Decimal | None] = mapped_column(Ratio)
    sell_through_rate: Mapped[Decimal] = mapped_column(Ratio)
    avg_days_to_sale: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    window_days: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
