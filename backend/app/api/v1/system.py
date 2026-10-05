"""Operational endpoints: health checks, system status and manual scan trigger."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import func, select, text

from app.api.deps import DB, CurrentUser
from app.core.config import get_settings
from app.core.rate_limit import RateLimit
from app.core.redis import get_redis
from app.db.models import Listing, Opportunity, SystemState
from app.marketplace.registry import get_provider
from app.schemas.common import Message
from app.workers.queue import enqueue, queue_lengths

router = APIRouter(tags=["system"])


@router.get("/health", response_model=dict[str, str])
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(db: DB) -> JSONResponse:
    """Readiness probe: database and Redis reachable."""
    checks: dict[str, str] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except (RedisError, OSError):
        checks["redis"] = "unavailable"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ready" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503
    )


@router.get("/system/status", response_model=dict[str, Any])
async def status(user: CurrentUser, db: DB) -> dict[str, Any]:
    settings = get_settings()
    provider = await get_provider(db)
    last_scan = await db.get(SystemState, "scanner_last_run")
    try:
        queues = await queue_lengths()
    except (RedisError, OSError):
        queues = {}
    counts = (
        await db.execute(
            select(
                select(func.count()).select_from(Listing).scalar_subquery(),
                select(func.count()).select_from(Listing).where(Listing.status == "active").scalar_subquery(),
                select(func.count())
                .select_from(Opportunity)
                .where(Opportunity.is_active.is_(True))
                .scalar_subquery(),
            )
        )
    ).one()
    return {
        "provider": {
            "name": provider.name,
            "capabilities": provider.capabilities.model_dump(),
            "demo_mode": provider.name == "mock",
        },
        "scanner": {
            "interval_seconds": settings.scan_interval_seconds,
            "last_run": last_scan.value if last_scan else None,
        },
        "queues": queues,
        "listings_tracked": counts[0],
        "listings_active": counts[1],
        "active_opportunities": counts[2],
        "ai": {
            "enabled": bool(settings.ai_api_key),
            "model": settings.ai_model if settings.ai_api_key else None,
        },
        "algorithm_version": settings.algorithm_version,
    }


@router.post("/system/scan", response_model=Message, dependencies=[Depends(RateLimit("scan", per_minute=6))])
async def trigger_scan(user: CurrentUser) -> Message:
    queued = await enqueue("scan_new_listings", high=True, job_id="scan:manual")
    return Message(message="Scansione avviata." if queued else "Una scansione è già in corso.")
