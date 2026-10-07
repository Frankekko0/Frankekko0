"""Worker job for the price evidence. CONTRACT STUB."""

from __future__ import annotations

from typing import Any


async def sync_price_evidence_task(ctx: dict[str, Any]) -> dict[str, Any]:
    """Sync concluded sales, negotiation discount, outliers, then ``recompute_model_stats``."""
    raise NotImplementedError
