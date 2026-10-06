"""Market Database: periodic aggregation of price & demand statistics per product segment.

Segments (grouping sets): brand x category, brand x category x model, and
brand x category x model x size. Percentiles are computed in PostgreSQL (``percentile_cont``)
on realized (sold) prices when at least 5 sales exist, otherwise on asking prices discounted by
the typical ask-to-sale ratio. The database improves continuously as listings are observed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketStatistic
from app.opportunities.pipeline import segment_key

DEFAULT_ASK_RATIO = Decimal("0.88")
MIN_SAMPLE = 3

SEGMENT_SQL = text(
    """
    SELECT brand_id, category_id, model_name, size_normalized,
           GROUPING(model_name) AS g_model, GROUPING(size_normalized) AS g_size,
           count(*) AS sample_size,
           count(*) FILTER (WHERE status = 'sold') AS sold_count,
           count(*) FILTER (WHERE status = 'removed') AS removed_count,
           count(*) FILTER (WHERE status = 'active') AS active_count,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY price) AS median_all,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY price) AS p25_all,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY price) AS p75_all,
           percentile_cont(0.10) WITHIN GROUP (ORDER BY price) AS p10_all,
           percentile_cont(0.90) WITHIN GROUP (ORDER BY price) AS p90_all,
           avg(price) AS mean_all,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY price) FILTER (WHERE status = 'sold') AS median_sold,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY price) FILTER (WHERE status = 'sold') AS p25_sold,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY price) FILTER (WHERE status = 'sold') AS p75_sold,
           percentile_cont(0.10) WITHIN GROUP (ORDER BY price) FILTER (WHERE status = 'sold') AS p10_sold,
           percentile_cont(0.90) WITHIN GROUP (ORDER BY price) FILTER (WHERE status = 'sold') AS p90_sold,
           avg(price) FILTER (WHERE status = 'sold') AS mean_sold,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY price) FILTER (WHERE status = 'active') AS median_active,
           avg(price) FILTER (WHERE status = 'active') AS avg_listing_price,
           avg(EXTRACT(EPOCH FROM (sold_at - published_at)) / 86400.0)
               FILTER (WHERE status = 'sold' AND sold_at IS NOT NULL AND published_at IS NOT NULL) AS avg_days_to_sale
    FROM listings
    WHERE brand_id IS NOT NULL AND category_id IS NOT NULL AND duplicate_of_id IS NULL
      AND COALESCE(sold_at, published_at, first_seen_at) >= :since
    GROUP BY GROUPING SETS ((brand_id, category_id),
                            (brand_id, category_id, model_name),
                            (brand_id, category_id, model_name, size_normalized))
    HAVING count(*) >= :min_sample
    """
)


def _dec(v: Any, places: str = "0.01") -> Decimal | None:
    return Decimal(str(v)).quantize(Decimal(places)) if v is not None else None


def segment_row(r: Any, window_days: int, now: datetime) -> dict[str, Any] | None:
    model = None if r.g_model else r.model_name
    size = None if r.g_size else r.size_normalized
    if (not r.g_model and r.model_name is None) or (not r.g_size and r.size_normalized is None):
        return None  # "unknown model/size" buckets are not meaningful segments
    sold = int(r.sold_count)
    active = int(r.active_count)
    removed = int(r.removed_count)
    ask_ratio = None
    if sold >= MIN_SAMPLE and active >= MIN_SAMPLE and r.median_active:
        ask_ratio = min(
            Decimal("1.05"), max(Decimal("0.6"), Decimal(str(r.median_sold)) / Decimal(str(r.median_active)))
        )
    if sold >= 5:
        median, p25, p75, p10, p90, mean = (
            r.median_sold,
            r.p25_sold,
            r.p75_sold,
            r.p10_sold,
            r.p90_sold,
            r.mean_sold,
        )
        k = Decimal(1)
    else:
        median, p25, p75, p10, p90, mean = (
            r.median_all,
            r.p25_all,
            r.p75_all,
            r.p10_all,
            r.p90_all,
            r.mean_all,
        )
        k = ask_ratio or DEFAULT_ASK_RATIO

    def adj(v: Any) -> Decimal:
        return (Decimal(str(v)) * k).quantize(Decimal("0.01"))

    # Removed listings count as not sold: a sale is only ever counted on evidence.
    denominator = sold + removed + active
    sell_through = Decimal(sold) / denominator if denominator else Decimal(0)
    return {
        "segment_key": segment_key(r.brand_id, r.category_id, model, size),
        "brand_id": r.brand_id,
        "category_id": r.category_id,
        "model_name": model,
        "size_normalized": size,
        "sample_size": int(r.sample_size),
        "sold_count": sold,
        "active_count": active,
        "median_price": adj(median),
        "mean_price": adj(mean),
        "p25_price": adj(p25),
        "p75_price": adj(p75),
        "min_reasonable_price": adj(p10),
        "max_reasonable_price": adj(p90),
        "avg_listing_price": _dec(r.avg_listing_price) or adj(mean),
        "median_sold_price": _dec(r.median_sold),
        "ask_to_sale_ratio": ask_ratio.quantize(Decimal("0.0001")) if ask_ratio else None,
        "sell_through_rate": sell_through.quantize(Decimal("0.0001")),
        "avg_days_to_sale": _dec(r.avg_days_to_sale),
        "window_days": window_days,
        "computed_at": now,
    }


async def recompute_market_statistics(session: AsyncSession, window_days: int = 90) -> int:
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            SEGMENT_SQL, {"since": now - timedelta(days=window_days), "min_sample": MIN_SAMPLE}
        )
    ).all()
    values = [v for r in rows if (v := segment_row(r, window_days, now)) is not None]
    for start in range(0, len(values), 500):
        chunk = values[start : start + 500]
        stmt = pg_insert(MarketStatistic).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["segment_key"],
            set_={k: getattr(stmt.excluded, k) for k in chunk[0] if k != "segment_key"},
        )
        await session.execute(stmt)
    await session.execute(
        delete(MarketStatistic).where(MarketStatistic.computed_at < now - timedelta(days=2))
    )
    return len(values)
