"""Price data status for the web app's Settings ("Price data")."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EvidenceAccuracy(BaseModel):
    """The backtest gate (``system_state`` ``evidence_gate``): errors with and without the extra sources."""

    measured_at: str | None
    without_external: dict[str, Any] | None
    with_external: dict[str, Any] | None
    external_in_use: bool
    own_purchases_in_use: bool
    note: str


class PricingEvidenceOut(BaseModel):
    sold_sales: dict[str, Any]
    negotiation: dict[str, Any]
    external: dict[str, Any]
    accuracy: EvidenceAccuracy


class RefreshQueued(BaseModel):
    queued: bool
