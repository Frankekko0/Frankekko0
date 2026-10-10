"""The agent's side of the API: AI spend against its caps, and the latest review of the best deals."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from app.agent.review import KIND
from app.ai.budget import AiBudget
from app.ai.limiter import get_limiter
from app.ai.queue import queue_stats
from app.api.deps import DB, CurrentUser
from app.core.config import get_settings
from app.db.models import AgentRun, Opportunity
from app.vision.cache import VisionCacheStore

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/usage", response_model=dict[str, Any])
async def usage(user: CurrentUser) -> dict[str, Any]:
    """Spend of today and of the month against the caps (prices are assumptions, see DEPENDENCIES.md)."""
    settings = get_settings()
    status = await AiBudget(settings).status()
    limiter = get_limiter()
    return status.as_dict() | {
        "ai_enabled": bool(settings.ai_api_key),
        "provider": settings.ai_provider,
        "models": {"strong": settings.ai_model, "cheap": settings.ai_model_cheap},
        # Requests left on each model this minute and today (the free tier is capped by requests, not by cost),
        # and the cooldown after a 429.
        "limits": {
            "strong": await limiter.snapshot(settings.ai_model, "strong"),
            "cheap": await limiter.snapshot(settings.ai_model_cheap, "cheap"),
        },
        # The strong model's review of the best deals: how many wait, are waiting for their next try, ran out of
        # attempts (stay as the rules wrote them until the analysis changes) or are reviewed already.
        "analyst_queue": await queue_stats(settings),
        # Photo sets analysed once and found again instead of being paid for twice.
        "vision_cache": await VisionCacheStore().stats(),
    }


@router.get("/review/latest", response_model=dict[str, Any])
async def latest_review(user: CurrentUser, db: DB) -> dict[str, Any]:
    """The most recent review of the best opportunities, each choice with the listing it is about."""
    run = (
        await db.execute(
            select(AgentRun)
            .where(AgentRun.kind == KIND, AgentRun.status == "succeeded")
            .order_by(AgentRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None or not run.result:
        return {"run": None, "picks": []}
    picks = list(run.result.get("picks", []))
    ids = [p["opportunity_id"] for p in picks]
    rows = {
        str(o.id): o
        for o in (await db.execute(select(Opportunity).where(Opportunity.id.in_(ids)))).scalars().all()
    }
    out = []
    for p in picks:
        o = rows.get(p["opportunity_id"])
        if o is None:
            continue
        out.append(
            {
                **p,
                "title": o.listing.title,
                "url": o.listing.url,
                "price": float(o.listing_price),
                "verdict_now": o.decision_verdict,
                "still_active": o.is_active,
                "threshold_price": (o.decision or {}).get("threshold_price"),
            }
        )
    return {
        "run": {
            "id": str(run.id),
            "at": run.started_at.isoformat(),
            "provider": run.provider,
            "model": run.model,
            "summary": run.result.get("summary"),
            "fallback": run.result.get("fallback", False),
            "cost_usd": str(run.cost_usd),
            "stop_reason": run.stop_reason,
        },
        "picks": out,
    }
