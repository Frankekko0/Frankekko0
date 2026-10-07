"""Average negotiation discount measured on the user's own purchases. CONTRACT STUB."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def compute_negotiation_discount(session: AsyncSession) -> dict[str, Any]:
    """{"discount": float | None, "n": int, "note": str, "measured_at": iso}; stored in system_state."""
    raise NotImplementedError


async def current_negotiation_discount(session: AsyncSession) -> dict[str, Any]:
    """The stored value (no recomputation): same shape as ``compute_negotiation_discount``."""
    raise NotImplementedError
