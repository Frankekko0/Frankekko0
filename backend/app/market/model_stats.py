"""Pre-computed statistics per brand, category, model, size and condition. CONTRACT STUB."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class StatQuery:
    """What a page item asks for; the most specific segment with enough data answers."""

    ref: str  # caller's id (e.g. the Vinted id)
    brand_id: int | None
    category_id: int | None
    model_name: str | None
    size: str | None
    condition: str | None


def stat_key(
    brand_id: int | None, category_id: int | None, model: str | None, size: str | None, condition: str | None
) -> str:
    """``"<brand_id>|<category_id>|<model folded>|<size>|<condition>"`` with ``*`` for any."""
    raise NotImplementedError


async def recompute_model_stats(session: AsyncSession, window_days: int = 180) -> int:
    """Rebuild ``model_price_stats`` (outliers cleaned, sales weighted by source); rows written."""
    raise NotImplementedError


async def lookup_stats(session: AsyncSession, queries: list[StatQuery]) -> dict[str, dict[str, Any]]:
    """One query for a whole page: ref -> best segment row as a dict (see the design document),
    falling back model+size+condition -> model+condition -> model+size -> model -> brand+category."""
    raise NotImplementedError
