"""External price references: refresh of the per-model cache. CONTRACT STUB."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def refresh_due_models(
    session: AsyncSession, now: datetime | None = None, max_models: int | None = None
) -> dict[str, Any]:
    """Search the models due for a refresh (highest demand first) within the query budget."""
    raise NotImplementedError


async def external_status(session: AsyncSession) -> dict[str, Any]:
    """Provider, budget used, cache size and last run (shape in the design document)."""
    raise NotImplementedError
