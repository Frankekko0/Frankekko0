"""Pre-computed price statistics per brand, category, model, size and condition (``model_price_stats``).

Levels (most specific first): model + size + condition, model + condition, model + size, model,
brand + category. At the model levels the category is "any" (``*`` in the key): a model belongs to
one category, and its sales count even when a source filed them without one.

Per segment:

* price - weighted median, P25 (low) and P75 (high) of the concluded sales, weighted by source
  (own resale 5, own purchase 1.5, Vinted sale 1.5, other marketplaces' sale 1; Vinted sales at
  the last price seen minus the measured negotiation discount); with fewer than 3 sales, the
  asking prices (Vinted 1, other marketplaces 0.5) brought to a sale price by the ask-to-sale
  ratio (``price_basis = 'asking'``);
* counts - sales per source, asking prices, outliers excluded;
* speed - average and median days to sell (Vinted and own sales), Vinted sell-through = sold /
  (sold + removed + on sale) among the listings seen in the window;
* new price - median of the retail prices found by the external search (reference only).

The backtest gate applies: sources it switched off do not enter the prices (they are still
counted in ``sources.excluded``). ``lookup_stats`` answers a whole page in one query.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brand, Category, ExternalPrice, Listing, ModelPriceStat, SoldSale, SystemState
from app.domain.enums import Condition, ListingStatus
from app.identification.taxonomy import fold
from app.ingestion.catalog import load_catalog
from app.pricing.comparables import SOURCE_WEIGHTS
from app.pricing.evidence import (
    GATE_KEY,
    NEGOTIATION_KEY,
    EvidenceGate,
    discount_from_state,
    evidence_condition,
)
from app.pricing.market_value import DEFAULT_ASK_TO_SALE
from app.pricing.stats import WeightedValue, weighted_percentile

WINDOW_DAYS = 180
MIN_SALES = 3  # below this many concluded sales the asking prices answer
MIN_SAMPLES = 3
RATIO_MIN = 5  # sales and asks needed to measure a segment's ask-to-sale ratio
LEVELS = ("model_size_condition", "model_condition", "model_size", "model", "brand_category")


@dataclass(frozen=True)
class StatQuery:
    """What a page item asks for; the most specific segment with enough data answers."""

    ref: str  # caller's id (e.g. the Vinted id)
    brand_id: int | None
    category_id: int | None
    model_name: str | None
    size: str | None
    condition: str | None


def stat_key(
    brand_id: int | None, category_id: int | None, model: str | None, size: str | None, condition: str | None
) -> str:
    """``"<brand_id>|<category_id>|<model folded>|<size>|<condition>"`` with ``*`` for any."""

    def part(v: Any) -> str:
        return "*" if v is None or v == "" else str(v)

    return "|".join(
        (part(brand_id), part(category_id), part(fold(model) if model else None), part(size), part(condition))
    )[:255]


def _known_condition(condition: str | None) -> str | None:
    return condition if condition and condition != Condition.UNKNOWN.value else None


def segment_keys(
    brand_id: int, category_id: int | None, model: str | None, size: str | None, condition: str | None
) -> list[tuple[str, str]]:
    """(level, key) of every segment a row belongs to, most specific first."""
    cond = _known_condition(condition)
    out: list[tuple[str, str]] = []
    if model:
        if size and cond:
            out.append(("model_size_condition", stat_key(brand_id, None, model, size, cond)))
        if cond:
            out.append(("model_condition", stat_key(brand_id, None, model, None, cond)))
        if size:
            out.append(("model_size", stat_key(brand_id, None, model, size, None)))
        out.append(("model", stat_key(brand_id, None, model, None, None)))
    if category_id is not None:
        out.append(("brand_category", stat_key(brand_id, category_id, None, None, None)))
    return out


def level_of(model: str | None, size: str | None, condition: str | None) -> str:
    if not model:
        return "brand_category"
    if size and condition:
        return "model_size_condition"
    if condition:
        return "model_condition"
    if size:
        return "model_size"
    return "model"


@dataclass
class _Segment:
    brand_id: int
    level: str
    size: str | None = None
    condition: str | None = None
    category_id: int | None = None
    sales: list[tuple[float, float, str]] = field(default_factory=list)  # (price, weight, source)
    asks: list[tuple[float, float, str]] = field(default_factory=list)
    excluded: Counter[str] = field(default_factory=Counter)
    outliers: int = 0
    days: list[float] = field(default_factory=list)
    seen: int = 0
    seen_sold: int = 0
    names: Counter[str] = field(default_factory=Counter)
    categories: Counter[int] = field(default_factory=Counter)


def _wq(items: list[tuple[float, float]], q: float) -> float:
    return weighted_percentile([WeightedValue(p, w) for p, w in items], q)


async def _state(session: AsyncSession) -> tuple[EvidenceGate, float | None]:
    rows = (
        await session.execute(
            select(SystemState.key, SystemState.value).where(SystemState.key.in_((GATE_KEY, NEGOTIATION_KEY)))
        )
    ).all()
    state = {r.key: r.value for r in rows}
    return EvidenceGate.from_state(state.get(GATE_KEY)), discount_from_state(state.get(NEGOTIATION_KEY))


async def recompute_model_stats(session: AsyncSession, window_days: int = WINDOW_DAYS) -> int:
    """Rebuild ``model_price_stats`` (outliers cleaned, sales weighted by source); rows written."""
    now = datetime.now(UTC)
    since = now - timedelta(days=window_days)
    gate, discount = await _state(session)
    catalog = await load_catalog(session)
    segments: dict[str, _Segment] = {}

    def touch(
        brand_id: int, category_id: int | None, model: str | None, size: str | None, cond: str | None
    ) -> Iterator[_Segment]:
        for level, key in segment_keys(brand_id, category_id, model, size, cond):
            seg = segments.get(key)
            if seg is None:
                seg = segments[key] = _Segment(
                    brand_id=brand_id,
                    level=level,
                    size=size if level in ("model_size_condition", "model_size") else None,
                    condition=_known_condition(cond)
                    if level in ("model_size_condition", "model_condition")
                    else None,
                    category_id=category_id if level == "brand_category" else None,
                )
            if model and level != "brand_category":
                seg.names[model] += 1
            if category_id is not None:
                seg.categories[category_id] += 1
            yield seg

    # Concluded sales (every source).
    sold_rows = (
        await session.execute(
            select(
                SoldSale.source,
                SoldSale.brand_id,
                SoldSale.category_id,
                SoldSale.model_name,
                SoldSale.size_normalized,
                SoldSale.condition,
                SoldSale.price_eur,
                SoldSale.days_to_sell,
                SoldSale.is_outlier,
            ).where(SoldSale.brand_id.is_not(None), SoldSale.sold_at >= since)
        )
    ).all()
    for r in sold_rows:
        cond = evidence_condition(r.condition)
        excluded = (r.source == "external_sold" and not gate.use_external) or (
            r.source == "own_purchase" and not gate.use_own_purchases
        )
        price = float(r.price_eur)
        if r.source == "vinted_sold" and discount:
            price *= 1 - discount
        for seg in touch(r.brand_id, r.category_id, r.model_name, r.size_normalized, cond):
            if r.is_outlier:
                seg.outliers += 1
            elif excluded:
                seg.excluded[r.source] += 1
            else:
                seg.sales.append((price, SOURCE_WEIGHTS[r.source], r.source))
                if r.days_to_sell is not None and r.source in ("own_sale", "vinted_sold"):
                    seg.days.append(float(r.days_to_sell))

    # Vinted listings seen in the window: asking prices and sell-through.
    event = func.coalesce(Listing.sold_at, Listing.removed_at, Listing.published_at, Listing.first_seen_at)
    listing_rows = (
        await session.execute(
            select(
                Listing.brand_id,
                Listing.category_id,
                Listing.model_name,
                Listing.size_normalized,
                Listing.condition,
                Listing.status,
                Listing.price,
            ).where(
                Listing.brand_id.is_not(None),
                Listing.duplicate_of_id.is_(None),
                Listing.currency == "EUR",
                Listing.status.in_(
                    (ListingStatus.ACTIVE.value, ListingStatus.SOLD.value, ListingStatus.REMOVED.value)
                ),
                event >= since,
            )
        )
    ).all()
    for r in listing_rows:
        for seg in touch(r.brand_id, r.category_id, r.model_name, r.size_normalized, r.condition):
            seg.seen += 1
            if r.status == ListingStatus.SOLD.value:
                seg.seen_sold += 1
            elif r.status == ListingStatus.ACTIVE.value and r.price > 0:
                seg.asks.append((float(r.price), SOURCE_WEIGHTS["vinted_asking"], "vinted_asking"))

    # External asking and new prices (one row per page, the newest).
    when = func.coalesce(ExternalPrice.source_date, ExternalPrice.observed_at)
    ext_rows = (
        await session.execute(
            select(
                ExternalPrice.kind,
                ExternalPrice.brand_id,
                ExternalPrice.model_key,
                ExternalPrice.category_id,
                ExternalPrice.model_name,
                ExternalPrice.size,
                ExternalPrice.condition,
                ExternalPrice.price_eur,
                ExternalPrice.source_url,
            )
            .where(
                ExternalPrice.kind.in_(("asking", "new")),
                ExternalPrice.is_outlier.is_(False),
                when >= since,
            )
            .order_by(when.desc(), ExternalPrice.id)
        )
    ).all()
    seen_urls: set[tuple[str, str]] = set()
    new_by_model: dict[str, list[float]] = defaultdict(list)
    for r in ext_rows:
        if (r.kind, r.source_url) in seen_urls:
            continue
        seen_urls.add((r.kind, r.source_url))
        brand_id = r.brand_id or catalog.brand_id(r.model_key.split("|", 1)[0])
        if brand_id is None:
            continue
        if r.kind == "new":
            if r.model_name:
                new_by_model[stat_key(brand_id, None, r.model_name, None, None)].append(float(r.price_eur))
            continue
        cond = evidence_condition(r.condition)
        for seg in touch(brand_id, r.category_id, r.model_name, r.size, cond):
            if gate.use_external:
                seg.asks.append((float(r.price_eur), SOURCE_WEIGHTS["external_asking"], "external_asking"))
            else:
                seg.excluded["external_asking"] += 1

    # Ask-to-sale ratio per brand + category (used where a segment has too few sales).
    ratios: dict[tuple[int, int | None], float] = {}
    for seg in segments.values():
        if seg.level == "brand_category" and len(seg.sales) >= RATIO_MIN and len(seg.asks) >= RATIO_MIN:
            sold_med = _wq([(p, w) for p, w, _ in seg.sales], 0.5)
            ask_med = _wq([(p, w) for p, w, _ in seg.asks], 0.5)
            if ask_med > 0:
                ratios[(seg.brand_id, seg.category_id)] = min(1.05, max(0.6, sold_med / ask_med))

    # New prices apply to every level of the model.
    values: list[dict[str, Any]] = []
    for key, seg in segments.items():
        category_id = seg.category_id
        if category_id is None and seg.categories:
            category_id = seg.categories.most_common(1)[0][0]
        model = seg.names.most_common(1)[0][0] if seg.names else None
        k_ask = ratios.get((seg.brand_id, category_id), DEFAULT_ASK_TO_SALE)
        if len(seg.sales) >= MIN_SALES:
            basis = "sold"
            sample = [(p, w) for p, w, _ in seg.sales]
        elif len(seg.asks) >= MIN_SAMPLES:
            basis = "asking"
            sample = [(p * k_ask, w) for p, w, _ in seg.asks]
        else:
            continue
        per_source = Counter(src for _, _, src in seg.sales)
        new = new_by_model.get(stat_key(seg.brand_id, None, model, None, None), []) if model else []
        days = seg.days
        values.append(
            {
                "segment_key": key,
                "brand_id": seg.brand_id,
                "category_id": category_id,
                "model_name": model[:120] if model else None,
                "size_normalized": seg.size,
                "condition": seg.condition,
                "price_basis": basis,
                "median_price": _money(_wq(sample, 0.5)),
                "low_price": _money(_wq(sample, 0.25)),
                "high_price": _money(_wq(sample, 0.75)),
                "n_samples": len(sample),
                "n_sales": len(seg.sales),
                "n_own": per_source["own_sale"] + per_source["own_purchase"],
                "n_vinted_sold": per_source["vinted_sold"],
                "n_external_sold": per_source["external_sold"],
                "n_asking": len(seg.asks),
                "n_outliers": seg.outliers,
                "avg_days_to_sell": _days(statistics.fmean(days)) if days else None,
                "median_days_to_sell": _days(statistics.median(days)) if days else None,
                "n_seen": seg.seen,
                "sell_through": Decimal(str(round(seg.seen_sold / seg.seen, 4))) if seg.seen else None,
                "median_new_price": _money(statistics.median(new)) if new else None,
                "n_new": len(new),
                "negotiation_discount": Decimal(str(discount))
                if discount and per_source["vinted_sold"]
                else None,
                "sources": {
                    "weights": SOURCE_WEIGHTS,
                    "n": {**{s: per_source[s] for s in per_source}, **_ask_counts(seg)},
                    "excluded": dict(seg.excluded),
                    "ask_to_sale": round(k_ask, 4) if basis == "asking" else None,
                    "gate": {
                        "use_external": gate.use_external,
                        "use_own_purchases": gate.use_own_purchases,
                    },
                },
                "window_days": window_days,
                "computed_at": now,
            }
        )
    values.sort(key=lambda v: v["segment_key"])
    for start in range(0, len(values), 500):
        chunk = values[start : start + 500]
        stmt = pg_insert(ModelPriceStat.__table__)
        stmt = stmt.on_conflict_do_update(
            index_elements=["segment_key"],
            set_={k: getattr(stmt.excluded, k) for k in chunk[0] if k != "segment_key"},
        )
        await session.execute(stmt, chunk)
    await session.execute(delete(ModelPriceStat).where(ModelPriceStat.computed_at < now))
    return len(values)


def _ask_counts(seg: _Segment) -> dict[str, int]:
    c = Counter(src for _, _, src in seg.asks)
    return {s: c[s] for s in c}


def _money(v: float) -> Decimal:
    return Decimal(str(round(v, 2)))


def _days(v: float) -> Decimal:
    return Decimal(str(round(v, 1)))


def candidate_keys(q: StatQuery) -> list[tuple[str, str]]:
    """(level, key) the query may be answered by, in the fallback order
    model+size+condition -> model+condition -> model+size -> model -> brand+category."""
    if q.brand_id is None:
        return []
    return segment_keys(q.brand_id, q.category_id, q.model_name, q.size, q.condition)


def stat_dict(row: Any, level: str, brand_slug: str | None, category_slug: str | None) -> dict[str, Any]:
    """The page-stats shape of a ``model_price_stats`` row."""
    days = row.median_days_to_sell if row.median_days_to_sell is not None else row.avg_days_to_sell
    return {
        "segment": row.segment_key,
        "level": level,
        "basis": row.price_basis,
        "median": float(row.median_price),
        "low": float(row.low_price),
        "high": float(row.high_price),
        "n_sales": row.n_sales,
        "n_own": row.n_own,
        "n_vinted_sold": row.n_vinted_sold,
        "n_external_sold": row.n_external_sold,
        "n_asking": row.n_asking,
        "days": float(days) if days is not None else None,
        "sell_through": float(row.sell_through) if row.sell_through is not None else None,
        "new_price": float(row.median_new_price) if row.median_new_price is not None else None,
        "model": row.model_name,
        "brand": brand_slug,
        "category": category_slug,
    }


async def lookup_stats(session: AsyncSession, queries: list[StatQuery]) -> dict[str, dict[str, Any]]:
    """One query for a whole page: ref -> best segment row as a dict (see the design document),
    falling back model+size+condition -> model+condition -> model+size -> model -> brand+category.

    Concluded sales first: the most specific model-level segment priced on sales answers; else
    the most specific model-level one priced on asks; else brand + category. Items without data
    are omitted."""
    wanted = {q.ref: candidate_keys(q) for q in queries}
    keys = sorted({k for cands in wanted.values() for _, k in cands})
    if not keys:
        return {}
    rows = (
        await session.execute(
            select(
                ModelPriceStat,
                Brand.slug.label("brand_slug"),
                Category.slug.label("category_slug"),
            )
            .outerjoin(Brand, Brand.id == ModelPriceStat.brand_id)
            .outerjoin(Category, Category.id == ModelPriceStat.category_id)
            .where(ModelPriceStat.segment_key.in_(keys))
        )
    ).all()
    by_key = {r[0].segment_key: r for r in rows}
    out: dict[str, dict[str, Any]] = {}
    for ref, cands in wanted.items():
        found = [(level, by_key[k]) for level, k in cands if k in by_key]
        if not found:
            continue
        models = [f for f in found if f[0] != "brand_category"]
        best = next((f for f in models if f[1][0].price_basis == "sold"), None) or (
            models[0] if models else found[0]
        )
        level, (stat, brand_slug, category_slug) = best
        out[ref] = stat_dict(stat, level, brand_slug, category_slug)
    return out
