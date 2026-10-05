"""Shared schema types."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, PlainSerializer

T = TypeVar("T")

# Money/ratios are exact Decimals internally and plain JSON numbers on the wire.
Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]
Ratio = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Page(Schema, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    has_more: bool


class Message(Schema):
    message: str


class BrandRef(Schema):
    slug: str
    name: str


class CategoryRef(Schema):
    slug: str
    name: str
    name_it: str | None = None
