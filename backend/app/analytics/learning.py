"""Learning engine: learns from the user's real flips and from what they ignore or save.

For each dimension (brand, category, size, price band) it measures the user's own results
(ROI vs. their average, win rate, holding time) and behaviour (ignored / saved deals) and turns
them into a bounded score adjustment used by the Personal Flip Score:

* performance only counts once the user has >= 3 completed flips, and is shrunk by sample size
  (n / (n + 3)) so one lucky flip does not dominate;
* repeatedly ignored segments get a mild penalty (max -8): lower priority, never hidden;
* each adjustment is clamped to +/-12 and the total personal adjustment to +/-15.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brand, Category, Favorite, Listing, Purchase, Sale, UserAffinity
from app.identification.taxonomy import fold
from app.opportunities.queries import price_band

MIN_FLIPS = 3
DIM_WEIGHTS = {"brand": 1.0, "category": 0.8, "price_band": 0.6, "size": 0.4}
IGNORE_WEIGHTS = {"brand": 1.0, "category": 0.8, "price_band": 0.5, "size": 0.0}


@dataclass
class FlipRecord:
    brand: str | None
    category: str | None
    size: str | None
    purchase_price: Decimal
    roi: Decimal
    profit: Decimal
    holding_days: int


@dataclass
class DimStats:
    flips: list[FlipRecord] = field(default_factory=list)
    ignored: int = 0
    saved: int = 0


def compute_adjustments(
    flips: list[FlipRecord],
    behaviour: dict[tuple[str, str], tuple[int, int]],
) -> dict[tuple[str, str], dict[str, float | int | None]]:
    """Pure core of the learning engine. ``behaviour`` maps (dim, key) -> (ignored, saved)."""
    stats: dict[tuple[str, str], DimStats] = defaultdict(DimStats)
    for f in flips:
        for dim, key in (
            ("brand", f.brand),
            ("category", f.category),
            ("size", f.size),
            ("price_band", price_band(float(f.purchase_price))),
        ):
            if key:
                stats[(dim, key)].flips.append(f)
    for (dim, key), (ignored, saved) in behaviour.items():
        stats[(dim, key)].ignored = ignored
        stats[(dim, key)].saved = saved

    total = len(flips)
    user_avg_roi = float(sum(f.roi for f in flips) / total) if total else 0.0
    out: dict[tuple[str, str], dict[str, float | int | None]] = {}
    for (dim, key), st in stats.items():
        n = len(st.flips)
        wins = sum(1 for f in st.flips if f.profit > 0)
        avg_roi = float(sum(f.roi for f in st.flips) / n) if n else None
        avg_profit = float(sum(f.profit for f in st.flips) / n) if n else None
        avg_hold = sum(f.holding_days for f in st.flips) / n if n else None
        adj = 0.0
        if total >= MIN_FLIPS and n and avg_roi is not None:
            shrink = n / (n + 3)
            perf = max(-10.0, min(10.0, (avg_roi - user_avg_roi) * 20))
            adj += DIM_WEIGHTS[dim] * shrink * (perf + (wins / n - 0.5) * 6)
        if st.ignored >= 3:
            adj -= IGNORE_WEIGHTS[dim] * min(8.0, 0.8 * st.ignored)
        if st.saved:
            adj += min(3.0, 0.3 * st.saved) * DIM_WEIGHTS[dim]
        adj = max(-12.0, min(12.0, adj))
        out[(dim, key)] = {
            "flips_count": n,
            "wins": wins,
            "avg_roi": avg_roi,
            "avg_profit": avg_profit,
            "avg_holding_days": avg_hold,
            "ignored_count": st.ignored,
            "saved_count": st.saved,
            "adjustment": round(adj, 2),
        }
    return out


async def recompute_user_affinities(session: AsyncSession, user_id: uuid.UUID) -> int:
    rows = (
        await session.execute(
            select(Purchase, Sale, Brand.slug, Category.slug)
            .join(Sale, Sale.purchase_id == Purchase.id)
            .outerjoin(Brand, Brand.id == Purchase.brand_id)
            .outerjoin(Category, Category.id == Purchase.category_id)
            .where(Purchase.user_id == user_id)
        )
    ).all()
    flips = [
        FlipRecord(
            brand=brand_slug or (fold(p.brand_name).replace(" ", "-") if p.brand_name else None),
            category=cat_slug,
            size=p.size,
            purchase_price=p.purchase_price,
            roi=s.roi,
            profit=s.profit,
            holding_days=s.holding_days,
        )
        for p, s, brand_slug, cat_slug in rows
    ]
    fav_rows = (
        await session.execute(
            select(Favorite.state, Brand.slug, Category.slug, Listing.price, Listing.size_normalized)
            .join(Listing, Listing.id == Favorite.listing_id)
            .outerjoin(Brand, Brand.id == Listing.brand_id)
            .outerjoin(Category, Category.id == Listing.category_id)
            .where(Favorite.user_id == user_id)
        )
    ).all()
    behaviour: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for state, brand, category, price, size in fav_rows:
        idx = 0 if state == "ignored" else 1 if state in ("saved", "watching", "purchased") else None
        if idx is None:
            continue
        for dim, key in (
            ("brand", brand),
            ("category", category),
            ("price_band", price_band(float(price))),
            ("size", size),
        ):
            if key:
                behaviour[(dim, key)][idx] += 1
    result = compute_adjustments(flips, {k: (v[0], v[1]) for k, v in behaviour.items()})
    now = datetime.now(UTC)
    await session.execute(delete(UserAffinity).where(UserAffinity.user_id == user_id))
    if result:
        await session.execute(
            pg_insert(UserAffinity),
            [
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "dimension": dim,
                    "key": key[:120],
                    "flips_count": v["flips_count"],
                    "wins": v["wins"],
                    "avg_roi": Decimal(str(round(v["avg_roi"], 4))) if v["avg_roi"] is not None else None,
                    "avg_profit": Decimal(str(round(v["avg_profit"], 2)))
                    if v["avg_profit"] is not None
                    else None,
                    "avg_holding_days": Decimal(str(round(v["avg_holding_days"], 2)))
                    if v["avg_holding_days"] is not None
                    else None,
                    "ignored_count": v["ignored_count"],
                    "saved_count": v["saved_count"],
                    "adjustment": Decimal(str(v["adjustment"])),
                    "updated_at": now,
                }
                for (dim, key), v in result.items()
            ],
        )
    return len(result)


async def recompute_all_affinities(session: AsyncSession) -> int:
    user_ids = set((await session.execute(select(Purchase.user_id).distinct())).scalars().all())
    user_ids |= set((await session.execute(select(Favorite.user_id).distinct())).scalars().all())
    total = 0
    for uid in user_ids:
        total += await recompute_user_affinities(session, uid)
    return total
