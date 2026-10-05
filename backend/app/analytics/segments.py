"""Brand and category analytics: which segments statistically produce the best flips."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Brand,
    Category,
    Listing,
    ListingPriceHistory,
    MarketStatistic,
    Opportunity,
    UserAffinity,
)

WINDOW_DAYS = 30


def _flt(v: Any) -> float | None:
    return round(float(v), 4) if v is not None else None


async def _market_by(session: AsyncSession, key: Any) -> dict[Any, dict[str, float | None]]:
    """Sell-through and days-to-sale per brand/category from brand x category segments."""
    rows = (
        await session.execute(
            select(
                key,
                (
                    func.sum(MarketStatistic.sell_through_rate * MarketStatistic.sample_size)
                    / func.sum(MarketStatistic.sample_size)
                ).label("str"),
                func.avg(MarketStatistic.avg_days_to_sale).label("days"),
                func.sum(MarketStatistic.sample_size).label("n"),
            )
            .where(MarketStatistic.model_name.is_(None), MarketStatistic.size_normalized.is_(None))
            .group_by(key)
        )
    ).all()
    return {
        r[0]: {"sell_through": _flt(r.str), "avg_days_to_sale": _flt(r.days), "market_sample": int(r.n or 0)}
        for r in rows
    }


def _opportunity_aggregates() -> list[Any]:
    flippable = Opportunity.expected_profit > 0
    return [
        func.count().label("analyzed"),
        func.count()
        .filter(and_(Opportunity.flip_score >= 70, Opportunity.is_active.is_(True)))
        .label("opportunities"),
        func.avg(Opportunity.expected_roi).filter(flippable).label("avg_roi"),
        func.avg(Opportunity.expected_profit).filter(flippable).label("avg_profit"),
        func.avg(Opportunity.flip_score).label("avg_flip"),
        func.avg(Opportunity.estimated_days_to_sell).label("avg_days"),
        func.max(Opportunity.flip_score).label("best_flip"),
    ]


async def brand_analytics(session: AsyncSession, limit: int = 50) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)
    rows = (
        await session.execute(
            select(Brand.id, Brand.slug, Brand.name, Brand.tier, *_opportunity_aggregates())
            .join(Listing, Listing.brand_id == Brand.id)
            .join(Opportunity, Opportunity.listing_id == Listing.id)
            .where(Opportunity.analyzed_at >= since)
            .group_by(Brand.id)
        )
    ).all()
    market = await _market_by(session, MarketStatistic.brand_id)
    out = [
        _segment_dict(r, market.get(r.id, {}), slug=r.slug, name=r.name, extra={"tier": r.tier}) for r in rows
    ]
    out.sort(key=lambda x: x["flip_index"], reverse=True)
    return out[:limit]


async def category_analytics(session: AsyncSession) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)
    rows = (
        await session.execute(
            select(Category.id, Category.slug, Category.name, Category.name_it, *_opportunity_aggregates())
            .join(Listing, Listing.category_id == Category.id)
            .join(Opportunity, Opportunity.listing_id == Listing.id)
            .where(Opportunity.analyzed_at >= since)
            .group_by(Category.id)
        )
    ).all()
    market = await _market_by(session, MarketStatistic.category_id)
    out = [
        _segment_dict(r, market.get(r.id, {}), slug=r.slug, name=r.name, extra={"name_it": r.name_it})
        for r in rows
    ]
    # Cross-cutting segments requested by resellers: Vintage and Designer.
    for slug, name, cond in (
        ("vintage", "Vintage", Listing.is_vintage.is_(True)),
        ("designer", "Designer", Brand.tier.in_(["luxury", "premium"])),
    ):
        r = (
            await session.execute(
                select(*_opportunity_aggregates())
                .select_from(Opportunity)
                .join(Listing, Listing.id == Opportunity.listing_id)
                .outerjoin(Brand, Brand.id == Listing.brand_id)
                .where(Opportunity.analyzed_at >= since, cond)
            )
        ).one()
        if r.analyzed:
            out.append(_segment_dict(r, {}, slug=slug, name=name, extra={"name_it": name, "segment": True}))
    out.sort(key=lambda x: x["flip_index"], reverse=True)
    return out


def _segment_dict(
    r: Any, market: dict[str, Any], *, slug: str, name: str, extra: dict[str, Any]
) -> dict[str, Any]:
    analyzed = int(r.analyzed or 0)
    opportunities = int(r.opportunities or 0)
    avg_roi = _flt(r.avg_roi)
    hit_rate = opportunities / analyzed if analyzed else 0.0
    sell_through = market.get("sell_through")
    # Composite "flip index" used for ranking: profitable ROI, deal frequency and demand.
    flip_index = round(
        100
        * (
            0.4 * min(1.0, max(0.0, (avg_roi or 0) / 0.9))
            + 0.35 * min(1.0, hit_rate / 0.08)
            + 0.25 * (sell_through or 0.3)
        ),
        1,
    )
    return {
        "slug": slug,
        "name": name,
        **extra,
        "listings_analyzed": analyzed,
        "opportunities": opportunities,
        "opportunity_rate": round(hit_rate, 4),
        "average_roi": avg_roi,
        "average_profit": _flt(r.avg_profit),
        "average_flip_score": _flt(r.avg_flip),
        "best_flip_score": r.best_flip,
        "average_days_to_sell": _flt(r.avg_days) or market.get("avg_days_to_sale"),
        "sell_through": sell_through,
        "market_sample": market.get("market_sample", 0),
        "flip_index": flip_index,
    }


async def market_database(
    session: AsyncSession, brand: str | None, category: str | None, q: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    stmt = (
        select(MarketStatistic, Brand.slug, Brand.name, Category.slug, Category.name_it)
        .join(Brand, Brand.id == MarketStatistic.brand_id)
        .join(Category, Category.id == MarketStatistic.category_id)
    )
    if brand:
        stmt = stmt.where(Brand.slug == brand)
    if category:
        stmt = stmt.where(Category.slug == category)
    if q:
        stmt = stmt.where(MarketStatistic.model_name.ilike(f"%{q.replace('%', '')}%"))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(MarketStatistic.sample_size.desc(), MarketStatistic.segment_key)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = [
        {
            "segment_key": m.segment_key,
            "brand": {"slug": bs, "name": bn},
            "category": {"slug": cs, "name": cn},
            "model_name": m.model_name,
            "size": m.size_normalized,
            "sample_size": m.sample_size,
            "sold_count": m.sold_count,
            "active_count": m.active_count,
            "median_price": float(m.median_price),
            "p25_price": float(m.p25_price),
            "p75_price": float(m.p75_price),
            "min_reasonable_price": float(m.min_reasonable_price),
            "max_reasonable_price": float(m.max_reasonable_price),
            "avg_listing_price": float(m.avg_listing_price),
            "sell_through_rate": float(m.sell_through_rate),
            "avg_days_to_sale": float(m.avg_days_to_sale) if m.avg_days_to_sale is not None else None,
            "computed_at": m.computed_at.isoformat(),
        }
        for m, bs, bn, cs, cn in rows
    ]
    return items, total


async def market_insights(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=24)
    brands = await brand_analytics(session, limit=5)
    cats = await category_analytics(session)
    first_price = (
        select(ListingPriceHistory.listing_id, func.max(ListingPriceHistory.price).label("max_price"))
        .group_by(ListingPriceHistory.listing_id)
        .subquery()
    )
    drops = (
        await session.execute(
            select(
                Opportunity.id, Listing.title, Listing.price, first_price.c.max_price, Opportunity.flip_score
            )
            .join(Listing, Listing.id == Opportunity.listing_id)
            .join(first_price, first_price.c.listing_id == Listing.id)
            .where(
                Opportunity.is_active.is_(True),
                first_price.c.max_price > Listing.price,
                Listing.updated_at >= since,
            )
            .order_by(((first_price.c.max_price - Listing.price) / first_price.c.max_price).desc())
            .limit(5)
        )
    ).all()
    personal = (
        (
            await session.execute(
                select(UserAffinity)
                .where(UserAffinity.user_id == user_id, UserAffinity.flips_count >= 1)
                .order_by(case((UserAffinity.avg_roi.is_(None), 1), else_=0), UserAffinity.avg_roi.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    hottest = sorted(
        [c for c in cats if c.get("sell_through")], key=lambda c: c["sell_through"], reverse=True
    )[:3]
    return {
        "top_brands": brands,
        "hottest_categories": hottest,
        "price_drops": [
            {
                "opportunity_id": str(r.id),
                "title": r.title,
                "price": float(r.price),
                "previous_price": float(r.max_price),
                "drop_pct": round(float((r.max_price - r.price) / r.max_price), 4),
                "flip_score": r.flip_score,
            }
            for r in drops
        ],
        "your_best_segments": [
            {
                "dimension": a.dimension,
                "key": a.key,
                "flips": a.flips_count,
                "avg_roi": float(a.avg_roi) if a.avg_roi is not None else None,
                "adjustment": float(a.adjustment),
            }
            for a in personal
        ],
    }
