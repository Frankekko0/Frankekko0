"""Backtest of the extra price evidence on stored data: no look-ahead, gate stored by the daily
calibration, CLI report."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.analytics.accuracy import fit_price_calibration
from app.analytics.backtest import load_rows, time_split
from app.analytics.evidence import load_evidence_pool, run_evidence_backtest
from app.db.models import SystemState
from app.domain.enums import AcquisitionMode
from app.ingestion.catalog import load_catalog
from app.ingestion.service import IngestionService
from app.market.sold_sales import sync_sold_sales
from app.tools.price_eval import evaluate_prices, format_report
from tests.conftest import NOW
from tests.integration.test_price_evidence import ext_row, ralph

pytestmark = pytest.mark.usefixtures("clean_db")


async def history(session: Any, make_listing: Any) -> int:
    """40 Custom Slim Fit sales over ~100 days; other marketplaces' sales known long before
    them (dated) and others found only after all of them (undated, found by a search later)."""
    items = [
        make_listing(
            price=26 + i % 9,
            status="sold",
            published_days_ago=100 - 2 * i,
            sold_after_days=3 + i % 4,
            external_id=f"h{i}",
        )
        for i in range(40)
    ]
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD).ingest(items, now=NOW)
    brand_id = await ralph(session)
    early = [ext_row(brand_id, "sold", 28 + i, f"https://www.ebay.it/old{i}", days_ago=200) for i in range(6)]
    late = [
        ext_row(brand_id, "sold", 29 + i, f"https://www.ebay.it/new{i}", days_ago=-1, source_date=False)
        for i in range(6)
    ]
    session.add_all([*early, *late])
    await session.flush()
    await sync_sold_sales(session, full=True)
    await session.commit()
    return brand_id


async def test_external_prices_only_from_their_date(session, make_listing) -> None:
    await history(session, make_listing)
    catalog = await load_catalog(session)
    rows = await load_rows(session, catalog)
    split = time_split(rows, 0.5)
    pool = await load_evidence_pool(session, catalog)
    bt = run_evidence_backtest(rows, catalog, pool, split)
    ext = bt.cases["external_own"]
    assert len(ext) == len(bt.cases["listings"]) == 40
    # The 6 dated sales precede every subject; the 6 found later never help a past sale.
    assert max(c.n_external for c in ext) == 6 and all(c.n_external <= 6 for c in ext)
    assert sum(1 for c in ext if c.n_external > 0) >= 35
    assert all(c.n_external == 0 for c in bt.cases["listings"])
    assert [c.listing_id for c in ext] == [c.listing_id for c in bt.cases["listings"]]  # same subjects


async def test_daily_calibration_stores_the_gate(session, make_listing) -> None:
    await history(session, make_listing)
    metrics = await fit_price_calibration(session)
    await session.commit()
    gate = (await session.get(SystemState, "evidence_gate")).value
    assert {
        "measured_at",
        "without_external",
        "with_external",
        "use_external",
        "use_own_purchases",
        "use_new_cap",
        "n_subjects",
        "note",
    } <= set(gate)
    assert gate["n_subjects"] == 20  # the newer half of the 40 sales
    assert gate["affected"]["external"] < 30 and gate["use_external"] is True  # not measurable yet
    assert "non ancora misurabili" in gate["note"] and gate["use_new_cap"] is False
    assert set(gate["with_external"]) >= {"mae_eur", "mape", "median_ape", "bias", "in_range"}
    assert metrics["evidence"]["variant"] == gate["variant"]
    # The report says what it measured against: last asking prices, not prices really paid.
    basis = metrics["basis"]
    assert basis["market_sales"] == "last_asking_price" and basis["own_resales"] == "price_received"
    assert basis["n_market_sales"] == metrics["learn_sales"] + metrics["test_sales"]
    assert basis["n_own_resales"] == metrics["own_resales"] == 0
    cal = (
        await session.execute(select(SystemState.value).where(SystemState.key == "price_calibration"))
    ).scalar()
    assert cal is not None and cal["metrics"]["evidence"]["use_external"] is True


async def test_price_eval_report(session, make_listing) -> None:
    await history(session, make_listing)
    result = await evaluate_prices(session, sync=True, store=False)
    assert result["sold_sales"]["before"]["total"] == result["sold_sales"]["after"]["total"] == 40 + 12
    assert result["models_with_5_sales"] == {"before": 1, "after": 1}
    assert result["accuracy"]["test_subjects"] == 20
    assert set(result["accuracy"]["variants"]) >= {"listings", "own", "external_own", "external_own_cap"}
    assert (await session.get(SystemState, "evidence_gate")) is None  # store=False
    text = format_report(result)
    assert "Vendite concluse nel database" in text and "Modelli con almeno 5 vendite reali: 1 -> 1" in text
    assert "solo annunci" in text and "Decisione" in text


async def test_worker_task_runs_in_full_then_incremental_and_one_at_a_time(session, make_listing) -> None:
    from app.core.redis import redis_lock
    from app.market.jobs import LOCK_TTL_SECONDS, sync_price_evidence_task

    await history(session, make_listing)  # ends with a full sync of the concluded sales
    first = await sync_price_evidence_task({})
    assert first["full"] is False  # the last full sync is recent: incremental
    assert first["sold_sales_after"] == 52 and first["stats_rows"] > 0
    forced = await sync_price_evidence_task({}, full=True)
    assert forced["full"] is True and forced["sold_sales_after"] == 52
    second = await sync_price_evidence_task({})
    assert second["full"] is False
    assert second["synced"] == {
        "own_sale": 0,
        "own_purchase": 0,
        "vinted_sold": 0,
        "external_sold": 0,
        "removed": 0,
    }
    async with redis_lock("price-evidence-sync", LOCK_TTL_SECONDS) as held:
        assert held
        assert await sync_price_evidence_task({}) == {"skipped": "already running"}
