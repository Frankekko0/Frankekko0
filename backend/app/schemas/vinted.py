"""Vinted favourites and purchases recorded from FlipFinder and the browser extension."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import Money, Schema


class VintedActionIn(Schema):
    kind: Literal["favourite", "checkout_opened", "purchased"]
    value: bool | None = None
    price: Money | None = Field(default=None, ge=0, le=100_000)
    source: Literal["click", "page", "checkout", "manual"] = "click"
    detail: dict[str, Any] | None = None


class CaptureVintedActionIn(VintedActionIn):
    vinted_id: str = Field(pattern=r"^\d{1,15}$")
