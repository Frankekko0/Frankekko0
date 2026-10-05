from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Integer, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Ratio, utcnow


class Brand(Base):
    __tablename__ = "brands"
    __table_args__ = (
        CheckConstraint("counterfeit_risk >= 0 AND counterfeit_risk <= 1", name="counterfeit_risk"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list, server_default="{}")
    tier: Mapped[str] = mapped_column(String(24), default="mid", server_default="mid")
    # 0..1 prevalence of counterfeits on the second-hand market for this brand.
    counterfeit_risk: Mapped[Decimal] = mapped_column(Ratio, default=Decimal("0.05"), server_default="0.05")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    name_it: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    # Baseline resale time used when there is no sold data for a segment.
    baseline_days_to_sell: Mapped[int] = mapped_column(SmallInteger, default=14, server_default="14")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)


class Product(Base):
    """Canonical product cluster: brand + category + model/line + gender."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("product_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_key: Mapped[str] = mapped_column(String(255))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="SET NULL"), index=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    model_name: Mapped[str | None] = mapped_column(String(120))
    gender: Mapped[str | None] = mapped_column(String(10))
    canonical_name: Mapped[str] = mapped_column(String(255))
    sku: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
