"""Retroactive accuracy check and calibration of the price estimates (continuous learning).

Every day: every sold listing is re-estimated with only what was known when it was published
(``backtest``), and the user's own resales are compared with the estimate they bought on. The
sales are split in time: the calibration is learned on the older half and measured on the newer
half, so the reported improvement is the one to expect on future items. Only corrections that
prove better on the newer half are kept (a systematic shift of the probable price is applied
only if it lowers the error there); the chosen form is then refitted on every sale and stored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.backtest import Case, load_rows, time_split
from app.analytics.calibration import (
    STATE_KEY,
    Calibration,
    CalibrationCase,
    confidence_bucket,
    describe_errors,
)
from app.analytics.evidence import decide_gate, load_evidence_pool, run_evidence_backtest, store_gate
from app.core.logging import get_logger
from app.db.models import Opportunity, Purchase, Sale, SystemState
from app.ingestion.catalog import Catalog, load_catalog

log = get_logger(__name__)
OWN_WEIGHT = 5.0  # the user's own resales count as five market sales
MAX_SUBJECTS = 1200
SHIFT_MIN_GAIN = 0.01  # a shift must cut the error by at least 1% to be applied


def aware(split: datetime) -> datetime:
    """``time_split`` gives a naive ``datetime.max`` when nothing sold: comparable with sale dates."""
    return split if split.tzinfo is not None else split.replace(tzinfo=UTC)


def _cal_cases(cases: list[Case]) -> list[CalibrationCase]:
    return [
        CalibrationCase(c.expected, c.realized, c.segment[1], c.segment[0], c.condition, c.confidence)
        for c in cases
        if c.expected
    ]


Pair = tuple[float, float, float | None, float | None]


def _pairs_raw(cases: list[Case]) -> list[Pair]:
    return [(c.expected, c.realized, c.quick, c.optimistic) for c in cases if c.expected is not None]


def _pairs_cal(cases: list[CalibrationCase], cal: Calibration) -> list[Pair]:
    out: list[Pair] = []
    for c in cases:
        lo, exp, hi = cal.apply(c.expected, c.category, c.brand, c.condition, c.confidence)
        out.append((exp, c.realized, lo, hi))
    return out


async def own_outcomes(session: AsyncSession, catalog: Catalog) -> list[CalibrationCase]:
    """The user's resales: the estimate shown when buying vs the price actually obtained."""
    rows = (
        await session.execute(
            select(
                Purchase.expected_sale_price,
                Purchase.brand_id,
                Purchase.category_id,
                Purchase.condition,
                Sale.sale_price,
                Opportunity.confidence_score,
            )
            .join(Sale, Sale.purchase_id == Purchase.id)
            .outerjoin(Opportunity, Opportunity.id == Purchase.opportunity_id)
            .where(
                Purchase.expected_sale_price.is_not(None),
                Purchase.expected_sale_price > 0,
                Sale.sale_price > 0,
            )
        )
    ).all()
    return [
        CalibrationCase(
            float(r.expected_sale_price),
            float(r.sale_price),
            catalog.category_slug(r.category_id),
            catalog.brand_slug(r.brand_id),
            r.condition,
            r.confidence_score if r.confidence_score is not None else 50,
            OWN_WEIGHT,
        )
        for r in rows
    ]


def _by_confidence(
    pairs: list[tuple[float, float, float | None, float | None]], conf: list[int]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for bucket in ("low", "mid", "high"):
        sub = [p for p, c in zip(pairs, conf, strict=True) if confidence_bucket(c) == bucket]
        out[bucket] = describe_errors(sub) if sub else {"n": 0}
    return out


def evaluate(
    cases_before: list[Case], cases_now: list[Case], own: list[CalibrationCase], split: datetime
) -> tuple[Calibration, dict[str, Any]]:
    learn = [c for c in cases_now if c.sold_at < split]
    test = [c for c in cases_now if c.sold_at >= split and c.expected]
    learn_cc = _cal_cases(learn) + own  # own resales have no reliable date order: learning only
    test_cc = _cal_cases(test)
    ranges_only = Calibration.fit(learn_cc, groups=(), global_shift=False)
    full = Calibration.fit(learn_cc)
    err_ranges = describe_errors(_pairs_cal(test_cc, ranges_only)) if ranges_only.active else {}
    err_full = describe_errors(_pairs_cal(test_cc, full)) if full.active else {}
    use_shift = bool(
        err_full and err_ranges and err_full["mae_eur"] < err_ranges["mae_eur"] * (1 - SHIFT_MIN_GAIN)
    )
    every = _cal_cases([c for c in cases_now if c.expected]) + own
    final = Calibration.fit(every) if use_shift else Calibration.fit(every, groups=(), global_shift=False)
    before_test = [c for c in cases_before if c.sold_at >= split and c.expected]
    chosen_test = (
        _pairs_cal(test_cc, full if use_shift else ranges_only) if ranges_only.active else _pairs_raw(test)
    )
    metrics = {
        "measured_at": datetime.now(UTC).isoformat(),
        "split_at": split.isoformat(),
        "learn_sales": len(learn),
        "test_sales": len(test),
        "own_resales": len(own),
        # What the estimates are compared with. Vinted publishes no sale prices: for market sales
        # it is the last asking price seen while the item was on sale (typically above the price
        # really paid); only the user's own resales are real prices.
        "basis": {
            "market_sales": "last_asking_price",
            "n_market_sales": len(learn) + len(test),
            "own_resales": "price_received",
            "n_own_resales": len(own),
        },
        "shift_applied": use_shift,
        # Same newer half of the sales, estimated by the previous and by the current version.
        "before": describe_errors(_pairs_raw(before_test)),
        "after_uncalibrated": describe_errors(_pairs_raw(test)),
        "after": describe_errors(chosen_test),
        "after_by_confidence": _by_confidence(chosen_test, [c.confidence for c in test_cc])
        if ranges_only.active
        else {},
        "coverage_without_estimate": round(
            1 - len(test) / max(1, len([c for c in cases_now if c.sold_at >= split])), 4
        ),
        "own": describe_errors(_pairs_cal(own, final)) if own and final.active else {"n": len(own)},
    }
    final.metrics = metrics
    return final, metrics


async def fit_price_calibration(session: AsyncSession, max_subjects: int = MAX_SUBJECTS) -> dict[str, Any]:
    """Backtest of every evidence variant -> evidence gate -> calibration of the variant production
    now uses (the extra evidence decides the estimate the ranges are calibrated on)."""
    catalog = await load_catalog(session)
    rows = await load_rows(session, catalog)
    split = aware(time_split(rows, 0.5))
    pool = await load_evidence_pool(session, catalog)
    bt = run_evidence_backtest(rows, catalog, pool, split, max_subjects=max_subjects)
    gate = decide_gate(bt)
    await store_gate(session, gate)
    before = [c for c in bt.cases["baseline"] if c.kind == "vinted"]
    now_cases = [c for c in bt.cases[gate["variant"]] if c.kind == "vinted"]
    own = await own_outcomes(session, catalog)
    cal, metrics = evaluate(before, now_cases, own, split)
    metrics["evidence"] = {
        k: gate[k] for k in ("variant", "use_external", "use_own_purchases", "use_new_cap", "affected")
    }
    stmt = pg_insert(SystemState).values(key=STATE_KEY, value=cal.to_state())
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"], set_={"value": stmt.excluded.value, "updated_at": datetime.now(UTC)}
        )
    )
    log.info("calibration.fitted", n=cal.n, shift=cal.shift != 0, test_sales=metrics["test_sales"])
    return metrics
