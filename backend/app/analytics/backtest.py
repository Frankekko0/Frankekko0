"""Retroactive check of the price estimates against real outcomes.

For every sold listing (the "subject") the estimate is recomputed with only what was known when
the subject was published: listings published before it, each with the status it had at that
moment (an item sold later still counted as on sale), and a segment reference computed from the
same data. The estimate is then compared with the price the subject actually sold at.

No future information leaks into an estimate. Metrics: mean absolute error (EUR and %), median
error, bias (systematic over- or under-estimation), share of sales inside the quick-optimistic
range, over-estimation error (the costly one: paying more than the item resells for), and the
share of subjects with an estimate at all ("dati insufficienti" otherwise).
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing
from app.ingestion.catalog import Catalog
from app.opportunities.pipeline import CANDIDATE_LIMIT, profile_from_row
from app.pricing.comparables import ItemProfile, select_comparables
from app.pricing.market_value import MarketEstimate, SegmentPrior, estimate_market_value

PRICING_COMPARABLES = 60  # same as app.opportunities.engine


@dataclass
class Row:
    profile: ItemProfile
    brand_id: int | None
    category_id: int | None
    realized: float | None


@dataclass
class Case:
    listing_id: uuid.UUID
    segment: tuple[str | None, str | None]  # (brand, category)
    condition: str | None
    sold_at: datetime
    realized: float
    expected: float | None
    quick: float | None
    optimistic: float | None
    n_used: int
    confidence: int


@dataclass
class Metrics:
    subjects: int
    estimated: int
    mae_eur: float | None
    mape: float | None
    median_ape: float | None
    bias: float | None
    in_range: float | None
    over_mae_eur: float | None

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def metrics(cases: list[Case]) -> Metrics:
    est = [c for c in cases if c.expected is not None and c.realized > 0]
    if not est:
        return Metrics(len(cases), 0, None, None, None, None, None, None)
    abs_err = [abs(c.expected - c.realized) for c in est]  # type: ignore[operator]
    ape = [abs(c.expected - c.realized) / c.realized for c in est]  # type: ignore[operator]
    signed = [(c.expected - c.realized) / c.realized for c in est]  # type: ignore[operator]
    in_range = [
        c
        for c in est
        if c.quick is not None and c.optimistic is not None and c.quick <= c.realized <= c.optimistic
    ]
    over = [c.expected - c.realized for c in est if c.expected > c.realized]  # type: ignore[operator]
    return Metrics(
        subjects=len(cases),
        estimated=len(est),
        mae_eur=sum(abs_err) / len(est),
        mape=sum(ape) / len(est),
        median_ape=statistics.median(ape),
        bias=sum(signed) / len(est),
        in_range=len(in_range) / len(est),
        over_mae_eur=(sum(over) / len(est)) if est else None,
    )


async def load_rows(session: AsyncSession, catalog: Catalog, provider: str | None = None) -> list[Row]:
    cols = (
        Listing.id,
        Listing.title,
        Listing.price,
        Listing.brand_id,
        Listing.category_id,
        Listing.model_name,
        Listing.condition,
        Listing.size_normalized,
        Listing.gender,
        Listing.color,
        Listing.material,
        Listing.country,
        Listing.is_vintage,
        Listing.status,
        Listing.published_at,
        Listing.sold_at,
        Listing.last_seen_at,
        Listing.removed_at,
        Listing.url,
        Listing.favourite_count,
        Listing.last_active_price,
    )
    stmt = select(*cols).where(Listing.duplicate_of_id.is_(None), Listing.published_at.is_not(None))
    if provider:
        stmt = stmt.where(Listing.provider == provider)
    rows = (await session.execute(stmt)).all()
    out = []
    for r in rows:
        realized = r.last_active_price if r.last_active_price is not None else r.price
        out.append(
            Row(
                profile_from_row(r, catalog), r.brand_id, r.category_id, float(realized) if realized else None
            )
        )
    return out


def _as_of(p: ItemProfile, cutoff: datetime) -> ItemProfile | None:
    """The listing as it looked at ``cutoff`` (None if not yet published)."""
    if p.published_at is None or p.published_at >= cutoff:
        return None
    if p.sold_at is not None and p.sold_at <= cutoff:
        status, sold_at, removed_at = "sold", p.sold_at, None
    elif p.removed_at is not None and p.removed_at <= cutoff:
        status, sold_at, removed_at = "removed", None, p.removed_at
    else:
        status, sold_at, removed_at = "active", None, None
    return ItemProfile(
        **{
            **p.__dict__,
            "status": status,
            "sold_at": sold_at,
            "removed_at": removed_at,
            "last_seen_at": cutoff if status == "active" else (sold_at or removed_at),
        }
    )


def _prior_at(pool: list[ItemProfile]) -> SegmentPrior | None:
    sold = sorted(float(p.price) for p in pool if p.status == "sold")
    active = sorted(float(p.price) for p in pool if p.status == "active")
    if len(sold) < 5:
        return None

    def q(xs: list[float], f: float) -> float:
        k = (len(xs) - 1) * f
        lo, hi = math.floor(k), math.ceil(k)
        return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)

    total = len(sold) + len(active)
    return SegmentPrior(
        median_price=q(sold, 0.5),
        p25_price=q(sold, 0.25),
        p75_price=q(sold, 0.75),
        sample_size=len(sold),
        ask_to_sale_ratio=(q(sold, 0.5) / q(active, 0.5)) if len(active) >= 5 else None,
        sell_through_rate=len(sold) / total if total else 0.0,
        avg_days_to_sale=None,
    )


Estimator = Callable[[ItemProfile, list[ItemProfile], datetime, SegmentPrior | None], MarketEstimate]


def baseline_estimator(
    subject: ItemProfile, candidates: list[ItemProfile], now: datetime, prior: SegmentPrior | None
) -> MarketEstimate:
    """The estimate before this version (the 60 most similar listings, no calibration)."""
    similar = select_comparables(subject, candidates, now, max_count=10_000)
    pool = [c for c in similar if c.item.status in ("sold", "active")]
    return estimate_market_value(
        pool[:PRICING_COMPARABLES], subject.condition, now, prior, sold_only_min=None
    )


def production_estimator(
    subject: ItemProfile, candidates: list[ItemProfile], now: datetime, prior: SegmentPrior | None
) -> MarketEstimate:
    """The current estimate before calibration (exactly what ``run_analysis`` computes)."""
    from app.opportunities.engine import price_estimate

    return price_estimate(subject, candidates, now, prior)[2]


def run_backtest(
    rows: list[Row],
    catalog: Catalog,
    estimator: Estimator = baseline_estimator,
    max_subjects: int = 1500,
    window_days: int = 120,
    since: datetime | None = None,
) -> list[Case]:
    """Estimate every sold subject (sold after ``since``) from what was known at its publication."""
    by_brand: dict[int | None, list[Row]] = defaultdict(list)
    for r in rows:
        by_brand[r.brand_id].append(r)
    subjects = [
        r
        for r in rows
        if r.profile.status == "sold"
        and r.profile.sold_at is not None
        and r.realized
        and r.brand_id is not None
        and (since is None or r.profile.sold_at >= since)
    ]
    subjects.sort(key=lambda r: (r.profile.sold_at, str(r.profile.id)))
    if len(subjects) > max_subjects:
        step = len(subjects) / max_subjects
        subjects = [subjects[int(i * step)] for i in range(max_subjects)]
    cases: list[Case] = []
    half = CANDIDATE_LIMIT // 2
    for s in subjects:
        p = s.profile
        assert p.published_at is not None and p.sold_at is not None
        cutoff: datetime = p.published_at + timedelta(hours=1)
        siblings = set(catalog.sibling_category_ids(p.category) or [])
        horizon = cutoff - timedelta(days=window_days)
        known: list[ItemProfile] = []
        for c in by_brand[s.brand_id]:
            if c is s or (siblings and c.category_id not in siblings):
                continue
            seen = _as_of(c.profile, cutoff)
            if seen is None:
                continue
            event = seen.sold_at or seen.removed_at or seen.published_at
            if event is None or event < horizon:
                continue
            known.append(seen)
        sold = sorted(
            (k for k in known if k.status == "sold"), key=lambda k: k.sold_at or cutoff, reverse=True
        )[:half]
        active = sorted(
            (k for k in known if k.status == "active"), key=lambda k: k.published_at or cutoff, reverse=True
        )[:half]
        same_cat = [k for k in known if k.category == p.category]
        prior = _prior_at(same_cat)
        subject = ItemProfile(**{**p.__dict__, "status": "active", "sold_at": None, "last_seen_at": cutoff})
        est = estimator(subject, [*sold, *active], cutoff, prior)
        cases.append(
            Case(
                listing_id=p.id,  # type: ignore[arg-type]
                segment=(p.brand, p.category),
                condition=p.condition,
                sold_at=p.sold_at,
                realized=s.realized,  # type: ignore[arg-type]
                expected=float(est.expected_sale_price) if est.expected_sale_price is not None else None,
                quick=float(est.quick_sale_price) if est.quick_sale_price is not None else None,
                optimistic=float(est.optimistic_sale_price)
                if est.optimistic_sale_price is not None
                else None,
                n_used=est.n_used,
                confidence=est.confidence,
            )
        )
    return cases


def time_split(rows: list[Row], fraction: float = 0.5) -> datetime:
    """Sale date splitting sold subjects into an earlier (learning) and a later (test) part."""
    dates = sorted(r.profile.sold_at for r in rows if r.profile.status == "sold" and r.profile.sold_at)
    return dates[int(len(dates) * fraction)] if dates else datetime.max


@dataclass
class Report:
    before: Metrics
    after: Metrics | None = None
    notes: list[str] = field(default_factory=list)


def to_decimal(v: float | None) -> Decimal | None:
    return None if v is None else Decimal(str(round(v, 2)))
