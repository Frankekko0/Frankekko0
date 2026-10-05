"""Market analytics: brands, categories, market database, insights."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.analytics.segments import brand_analytics, category_analytics, market_database, market_insights
from app.api.deps import DB, CurrentUser
from app.core.cache import NS_ANALYTICS, cache

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/brands", response_model=list[dict[str, Any]])
async def brands(user: CurrentUser, db: DB, limit: int = Query(50, ge=1, le=200)) -> Any:
    return await cache.get_or_set(NS_ANALYTICS, ("brands", limit), 600, lambda: brand_analytics(db, limit))


@router.get("/categories", response_model=list[dict[str, Any]])
async def categories(user: CurrentUser, db: DB) -> Any:
    return await cache.get_or_set(NS_ANALYTICS, ("categories",), 600, lambda: category_analytics(db))


@router.get("/market", response_model=dict[str, Any])
async def market(
    user: CurrentUser,
    db: DB,
    brand: str | None = None,
    category: str | None = None,
    q: str | None = Query(None, max_length=60),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    items, total = await market_database(db, brand, category, q, page_size, (page - 1) * page_size)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
    }


@router.get("/insights", response_model=dict[str, Any])
async def insights(user: CurrentUser, db: DB) -> Any:
    return await cache.get_or_set(
        NS_ANALYTICS, ("insights", str(user.id)), 120, lambda: market_insights(db, user.id)
    )
