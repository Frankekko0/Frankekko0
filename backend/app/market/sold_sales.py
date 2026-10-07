"""Concluded sales (``sold_sales``): the most reliable price evidence. CONTRACT STUB - see
the design document; the evidence-core implementation replaces the bodies, not the signatures."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def sync_sold_sales(session: AsyncSession, *, full: bool = False) -> dict[str, int]:
    """Upsert concluded sales from every source; returns rows inserted/updated per source."""
    raise NotImplementedError


async def record_vinted_sold(session: AsyncSession, listing_ids: list[uuid.UUID]) -> int:
    """Record listings just seen turning to "sold" (called right after a capture)."""
    raise NotImplementedError


async def sold_sales_summary(session: AsyncSession) -> dict[str, Any]:
    """{"total", "by_source": {...}, "models_with_5_sales", "models_total", "last_sync"}."""
    raise NotImplementedError
