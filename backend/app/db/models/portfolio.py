from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Ratio, Timestamped, UUIDPk, utcnow


class Favorite(UUIDPk, Timestamped, Base):
    """User state on a listing: saved / ignored / purchased / watching / sold."""

    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "listing_id"),
        Index("ix_favorites_user_state", "user_id", "state"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL")
    )
    state: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text)


class Purchase(UUIDPk, Timestamped, Base):
    __tablename__ = "purchases"
    __table_args__ = (
        CheckConstraint("purchase_price >= 0", name="purchase_price_non_negative"),
        Index("ix_purchases_user_date", "user_id", "purchase_date"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    listing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("listings.id", ondelete="SET NULL"))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="SET NULL"))
    brand_name: Mapped[str | None] = mapped_column(String(120))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    category_name: Mapped[str | None] = mapped_column(String(120))
    size: Mapped[str | None] = mapped_column(String(20))
    condition: Mapped[str | None] = mapped_column(String(24))
    purchase_price: Mapped[Decimal] = mapped_column()
    buyer_protection_fee: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    shipping_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    other_costs: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    total_cost: Mapped[Decimal] = mapped_column()
    purchase_date: Mapped[date] = mapped_column(Date)
    expected_sale_price: Mapped[Decimal | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)

    sale: Mapped[Sale | None] = relationship(back_populates="purchase", uselist=False, lazy="selectin")
    inventory_item: Mapped[InventoryItem | None] = relationship(
        back_populates="purchase", uselist=False, lazy="selectin", cascade="all, delete-orphan"
    )


class Sale(UUIDPk, Timestamped, Base):
    __tablename__ = "sales"
    __table_args__ = (
        UniqueConstraint("purchase_id"),
        CheckConstraint("sale_price >= 0", name="sale_price_non_negative"),
        Index("ix_sales_user_date", "user_id", "sale_date"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purchase_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"))
    sale_price: Mapped[Decimal] = mapped_column()
    selling_fees: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    shipping_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    packaging_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    other_costs: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    net_revenue: Mapped[Decimal] = mapped_column()
    profit: Mapped[Decimal] = mapped_column()
    roi: Mapped[Decimal] = mapped_column(Ratio)
    holding_days: Mapped[int] = mapped_column()
    sale_date: Mapped[date] = mapped_column(Date)
    platform: Mapped[str] = mapped_column(String(32), default="vinted")
    notes: Mapped[str | None] = mapped_column(Text)

    purchase: Mapped[Purchase] = relationship(back_populates="sale")


class InventoryItem(UUIDPk, Timestamped, Base):
    __tablename__ = "inventory"
    __table_args__ = (UniqueConstraint("purchase_id"), Index("ix_inventory_user_status", "user_id", "status"))

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purchase_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="in_stock")
    listed_price: Mapped[Decimal | None] = mapped_column()
    estimated_value: Mapped[Decimal | None] = mapped_column()
    listed_at: Mapped[datetime | None] = mapped_column()

    purchase: Mapped[Purchase] = relationship(back_populates="inventory_item")


class UserAffinity(Base):
    """Learning engine output: how a user performs/behaves per brand, category, size, price band."""

    __tablename__ = "user_affinities"
    __table_args__ = (UniqueConstraint("user_id", "dimension", "key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dimension: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(120))
    flips_count: Mapped[int] = mapped_column(default=0)
    wins: Mapped[int] = mapped_column(default=0)
    avg_roi: Mapped[Decimal | None] = mapped_column(Ratio)
    avg_profit: Mapped[Decimal | None] = mapped_column()
    avg_holding_days: Mapped[Decimal | None] = mapped_column(Ratio)
    ignored_count: Mapped[int] = mapped_column(default=0)
    saved_count: Mapped[int] = mapped_column(default=0)
    adjustment: Mapped[Decimal] = mapped_column(Ratio, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column()


class MarketplaceAction(UUIDPk, Base):
    """Legacy history of what the extension did on Vinted for a listing (favourite added or removed,
    checkout opened, purchase completed). Nothing writes it any more: FlipFinder no longer clicks on
    Vinted (decision Q1) and the user records a purchase by hand. Kept so the history is not lost."""

    __tablename__ = "marketplace_actions"
    __table_args__ = (Index("ix_marketplace_actions_lookup", "user_id", "listing_id", "kind", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))  # favourite | checkout_opened | purchased
    value: Mapped[bool | None] = mapped_column(Boolean)  # favourite on/off
    price: Mapped[Decimal | None] = mapped_column()
    source: Mapped[str] = mapped_column(String(16))  # click | page | checkout | manual
    detail: Mapped[dict | None] = mapped_column(JSONB)  # type: ignore[type-arg]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
