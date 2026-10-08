"""Worker job for the external price references.

Hourly; it limits itself: only the models due (``external_searches``), only within the daily and
monthly query budget, one run at a time (Redis lock). A no-op with a clear status when no provider
or no key is configured.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.redis import redis_lock
from app.db.session import session_scope
from app.external.service import disabled_reason, refresh_due_models

LOCK_NAME = "external-prices-refresh"
LOCK_TTL_SECONDS = 1800


async def refresh_external_prices_task(ctx: dict[str, Any]) -> dict[str, Any]:
    """Periodic refresh (no-op when no provider/key is configured)."""
    settings = get_settings()
    if reason := disabled_reason(settings):
        return {"status": "disabled", "reason": reason}
    async with redis_lock(LOCK_NAME, LOCK_TTL_SECONDS) as acquired:
        if not acquired:
            return {"status": "skipped", "reason": "already running"}
        async with session_scope() as s:
            return await refresh_due_models(s, settings=settings)
