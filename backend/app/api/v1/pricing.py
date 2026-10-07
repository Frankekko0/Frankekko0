"""Price data: concluded sales, negotiation discount, external search and measured accuracy."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import DB, CurrentUser
from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.core.rate_limit import RateLimit
from app.external.service import external_status
from app.market.negotiation import current_negotiation_discount
from app.market.sold_sales import sold_sales_summary
from app.market.state import get_state
from app.pricing.evidence import GATE_KEY, EvidenceGate
from app.schemas.pricing import PricingEvidenceOut, RefreshQueued
from app.workers.queue import enqueue

log = get_logger(__name__)
router = APIRouter(prefix="/pricing", tags=["pricing"])

# external_status shape (design document), returned empty when the status cannot be read.
EXTERNAL_KEYS = (
    "provider",
    "enabled",
    "key_configured",
    "cost_per_query_usd",
    "free_queries",
    "month_used",
    "month_budget",
    "today_used",
    "daily_max",
    "refresh_days",
    "models_cached",
    "models_due",
    "models_pending",
    "prices",
    "rejected",
    "last_run",
    "last_error",
    "expected_monthly_queries",
)
EXTERNAL_UNAVAILABLE = "Stato della ricerca esterna non disponibile al momento."


async def _external(db: DB) -> dict[str, Any]:
    """The external search status; never fails the page (a clear "unavailable" instead)."""
    try:
        return await external_status(db)
    except Exception as exc:
        log.warning("pricing.external_status_failed", error=type(exc).__name__)
        await db.rollback()  # a failed query would leave the transaction aborted
        return {**dict.fromkeys(EXTERNAL_KEYS), "enabled": False, "last_error": EXTERNAL_UNAVAILABLE}


@router.get("/evidence", response_model=PricingEvidenceOut)
async def evidence(user: CurrentUser, db: DB) -> dict[str, Any]:
    """Where the prices come from: concluded sales per source, the negotiation discount measured
    on the user's purchases, the external search (budget, cache) and the backtest accuracy with
    and without the extra sources."""
    sold = await sold_sales_summary(db)
    negotiation = await current_negotiation_discount(db)
    state = await get_state(db, GATE_KEY) or {}
    gate = EvidenceGate.from_state(state)
    accuracy = {
        "measured_at": state.get("measured_at"),
        "without_external": state.get("without_external"),
        "with_external": state.get("with_external"),
        "external_in_use": gate.use_external,
        "own_purchases_in_use": gate.use_own_purchases,
        "note": gate.note,
    }
    external = await _external(db)
    return {"sold_sales": sold, "negotiation": negotiation, "external": external, "accuracy": accuracy}


@router.post(
    "/evidence/refresh",
    response_model=RefreshQueued,
    dependencies=[Depends(RateLimit("evidence-refresh", per_minute=2))],
)
async def refresh(user: CurrentUser) -> dict[str, Any]:
    """ "Aggiorna ora": queue the evidence sync (sales, discount, statistics) and the external
    refresh (it self-limits by budget). Repeated clicks while they wait are dropped by job id."""
    try:
        await enqueue("sync_price_evidence_task", job_id="price-evidence:manual")
        await enqueue("refresh_external_prices_task", job_id="external-prices:manual")
    except Exception as exc:
        log.warning("pricing.refresh_not_queued", error=type(exc).__name__)
        raise ProviderUnavailableError(
            "Coda dei lavori non raggiungibile: riprova tra qualche minuto.", code="queue_unavailable"
        ) from exc
    return {"queued": True}
