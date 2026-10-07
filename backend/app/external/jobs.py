"""Worker job for the external price references. CONTRACT STUB."""

from __future__ import annotations

from typing import Any


async def refresh_external_prices_task(ctx: dict[str, Any]) -> dict[str, Any]:
    """Periodic refresh (no-op when no provider/key is configured)."""
    raise NotImplementedError
