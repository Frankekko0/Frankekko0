from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field, model_validator

from app.schemas.common import Money, Ratio, Schema


class PurchaseIn(Schema):
    title: str = Field(min_length=1, max_length=300)
    opportunity_id: uuid.UUID | None = None
    listing_id: uuid.UUID | None = None
    brand: str | None = Field(default=None, max_length=120, description="Brand slug or free text")
    category: str | None = Field(default=None, max_length=120, description="Category slug")
    size: str | None = Field(default=None, max_length=20)
    condition: str | None = Field(default=None, max_length=24)
    purchase_price: Money = Field(ge=0, le=100_000)
    buyer_protection_fee: Money | None = Field(default=None, ge=0, le=10_000)
    shipping_cost: Money | None = Field(default=None, ge=0, le=10_000)
    other_costs: Money = Field(default=0, ge=0, le=10_000)
    purchase_date: date
    expected_sale_price: Money | None = Field(default=None, ge=0, le=100_000)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _date_not_future(self) -> PurchaseIn:
        if self.purchase_date > date.today():
            raise ValueError("La data di acquisto non può essere nel futuro.")
        return self


class PurchaseUpdate(Schema):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    expected_sale_price: Money | None = Field(default=None, ge=0, le=100_000)
    notes: str | None = Field(default=None, max_length=2000)
    inventory_status: str | None = Field(default=None, pattern="^(in_stock|listed|returned)$")
    listed_price: Money | None = Field(default=None, ge=0, le=100_000)


class SaleIn(Schema):
    purchase_id: uuid.UUID
    sale_price: Money = Field(ge=0, le=100_000)
    selling_fees: Money = Field(default=0, ge=0, le=10_000)
    shipping_cost: Money = Field(default=0, ge=0, le=10_000)
    packaging_cost: Money = Field(default=0, ge=0, le=10_000)
    other_costs: Money = Field(default=0, ge=0, le=10_000)
    sale_date: date
    platform: str = Field(default="vinted", max_length=32)
    notes: str | None = Field(default=None, max_length=2000)


class SaleOut(Schema):
    id: uuid.UUID
    purchase_id: uuid.UUID
    sale_price: Money
    selling_fees: Money
    shipping_cost: Money
    packaging_cost: Money
    other_costs: Money
    net_revenue: Money
    profit: Money
    roi: Ratio
    holding_days: int
    sale_date: date
    platform: str
    notes: str | None
    created_at: datetime


class FlipOut(Schema):
    purchase_id: uuid.UUID
    title: str
    brand: str | None
    category: str | None
    size: str | None
    condition: str | None
    purchase_price: Money
    total_cost: Money
    purchase_date: date
    expected_sale_price: Money | None
    opportunity_id: uuid.UUID | None
    status: str
    listed_price: Money | None
    sale: SaleOut | None
    profit: Money | None
    roi: Ratio | None
    holding_days: int | None
    notes: str | None
