"""Outlier cleaning of the price evidence (``is_outlier`` on ``sold_sales`` and ``external_prices``).

Same robust rule as the comparables (``app.pricing.stats.detect_outliers``: log-space Tukey IQR
fences + median absolute deviation, a ratio filter for small groups), applied per model:

* concluded sales: per brand + model across every source (rows without a model per brand +
  category), prices brought to the "very good" condition first so a new item among used ones is
  not mistaken for an anomaly;
* external prices: per model and kind (new, asking and sold prices are different markets).

Groups of fewer than 3 prices are left alone (two prices cannot tell which one is wrong).
Flagged rows are kept for traceability and excluded from estimates and statistics; the flags are
recomputed on every run, so a price that becomes plausible with more data is used again.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Iterable
from typing import Any

from sqlalchemy import BigInteger, all_, any_, bindparam, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExternalPrice, SoldSale
from app.identification.taxonomy import fold
from app.pricing.comparables import CONDITION_MULTIPLIER
from app.pricing.evidence import evidence_condition
from app.pricing.stats import detect_outliers

MIN_GROUP = 3


def outlier_ids(rows: Iterable[tuple[int, Hashable, float, str | None]]) -> set[int]:
    """Ids flagged as outliers among ``(id, group, price, condition)`` rows (pure)."""
    groups: dict[Hashable, list[tuple[int, float]]] = defaultdict(list)
    for row_id, group, price, condition in rows:
        mult = CONDITION_MULTIPLIER.get(evidence_condition(condition), 0.95)
        groups[group].append((row_id, float(price) / mult))
    out: set[int] = set()
    for items in groups.values():
        if len(items) < MIN_GROUP:
            continue
        res = detect_outliers([p for _, p in items])
        out.update(items[i][0] for i in res.removed)
    return out


async def _apply(session: AsyncSession, table: Any, flagged: set[int]) -> int:
    ids = bindparam("ids", value=sorted(flagged), type_=ARRAY(BigInteger))
    await session.execute(
        update(table).where(table.c.is_outlier.is_(True), table.c.id != all_(ids)).values(is_outlier=False)
    )
    if not flagged:
        return 0
    await session.execute(
        update(table).where(table.c.is_outlier.is_(False), table.c.id == any_(ids)).values(is_outlier=True)
    )
    return len(flagged)


async def flag_sold_outliers(session: AsyncSession) -> int:
    """Recompute ``sold_sales.is_outlier``; returns the rows flagged."""
    rows = (
        await session.execute(
            select(
                SoldSale.id,
                SoldSale.brand_id,
                SoldSale.category_id,
                SoldSale.model_name,
                SoldSale.price_eur,
                SoldSale.condition,
            ).where(SoldSale.brand_id.is_not(None))
        )
    ).all()
    keyed = [
        (
            r.id,
            ("model", r.brand_id, fold(r.model_name)) if r.model_name else ("cat", r.brand_id, r.category_id),
            r.price_eur,
            r.condition,
        )
        for r in rows
    ]
    return await _apply(session, SoldSale.__table__, outlier_ids(keyed))


async def flag_external_outliers(session: AsyncSession) -> int:
    """Recompute ``external_prices.is_outlier`` per model and kind; returns the rows flagged."""
    rows = (
        await session.execute(
            select(
                ExternalPrice.id,
                ExternalPrice.model_key,
                ExternalPrice.kind,
                ExternalPrice.price_eur,
                ExternalPrice.condition,
            )
        )
    ).all()
    keyed = [(r.id, (r.model_key, r.kind), r.price_eur, r.condition) for r in rows]
    return await _apply(session, ExternalPrice.__table__, outlier_ids(keyed))
