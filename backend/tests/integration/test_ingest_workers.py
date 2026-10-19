"""The worker queues of the ingest wave: what the photo follow-up waits behind, and what the bulk re-analysis does.

* F3 / TT-07: the analysis that turns a photo check into the verdict goes on the high queue (it used to wait behind
  the daily re-analysis of every active opportunity), and its job id never collides with a running analysis;
* F11: the bulk re-analysis is sent grouped by brand and category (a batch of 40 shares one comparables pool);
* F3: it runs when the calibration (or the algorithm) changed or a day passed, not at every restart;
* TT-11: a poisoned listing does not take its neighbours down with it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import AnalysisJob, Listing, Opportunity, SystemState
from app.db.session import session_scope
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.opportunities.pipeline import AnalysisPipeline
from app.workers import tasks
from tests.conftest import NOW


class Queue:
    """Replaces ``enqueue`` of the tasks module and remembers every job."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def __call__(self, function: str, *args: Any, **kw: Any) -> bool:
        self.jobs.append((function, args, kw))
        return True

    def of(self, function: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        return [(a, k) for f, a, k in self.jobs if f == function]


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> Queue:
    q = Queue()
    monkeypatch.setattr(tasks, "enqueue", q)
    return q


async def active_listings(make_listing: Any, specs: list[dict[str, Any]]) -> list[uuid.UUID]:
    """Ingested and analysed listings (one opportunity each), in the order of ``specs``."""
    listings = [make_listing(**spec) for spec in specs]
    async with session_scope() as s:
        res = await IngestionService(s, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest(listings, now=NOW)
    async with session_scope() as s:
        await AnalysisPipeline(s).analyze_many(list(res.ids_by_external.values()), NOW)
    return [res.ids_by_external[pl.external_id] for pl in listings]


async def test_the_photo_follow_up_goes_on_the_high_queue_with_an_id_that_a_running_analysis_cannot_swallow(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    async def changed(db: Any, listing_id: Any) -> bool:
        return True

    monkeypatch.setattr(tasks, "run_vision", changed)
    listing_id = str(uuid.uuid4())
    await tasks.vision_task({}, listing_id)
    [(args, kw)] = queue.of("analyze_listing")
    assert args == (listing_id, True)
    assert kw["high"] is True  # not behind the bulk re-analysis on the default queue
    # arq keeps a job's key until it ends: a fixed id is dropped while an older analysis of the listing still runs.
    assert kw["job_id"] != f"analyze:{listing_id}" and kw["job_id"].startswith(f"analyze:{listing_id}")


async def test_a_photo_check_that_changed_nothing_queues_no_analysis(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    async def unchanged(db: Any, listing_id: Any) -> bool:
        return False

    monkeypatch.setattr(tasks, "run_vision", unchanged)
    await tasks.vision_task({}, str(uuid.uuid4()))
    assert queue.jobs == []


async def test_the_bulk_re_analysis_is_grouped_by_brand_and_category(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    # Alternating brands: in select order every batch would span both families.
    specs = [
        {"brand": "Nike" if i % 2 else "Adidas", "category": "Felpa", "title": f"Felpa {i}", "price": 20 + i}
        for i in range(8)
    ]
    ids = await active_listings(make_listing, specs)

    async def no_fit(session: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(tasks, "fit_price_calibration", no_fit)
    monkeypatch.setattr(tasks, "ANALYSIS_BATCH_DEFAULT", 4)
    await tasks.fit_price_calibration_task({})

    async with session_scope() as s:
        brand = dict((await s.execute(select(Listing.id, Listing.brand_id))).all())
    batches = [args[0] for args, _ in queue.of("analyze_batch")]
    assert sorted(i for b in batches for i in b) == sorted(str(i) for i in ids)
    for batch in batches:
        assert len({brand[uuid.UUID(i)] for i in batch}) == 1, "a batch must span one brand"


async def test_a_restart_that_changed_nothing_does_not_re_analyse_every_active_opportunity(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    ids = await active_listings(make_listing, [{"title": f"Polo {i}", "price": 20 + i} for i in range(3)])

    async def no_fit(session: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(tasks, "fit_price_calibration", no_fit)

    first = await tasks.fit_price_calibration_task({})
    assert first["reanalysis"] is True
    assert sorted(i for args, _ in queue.of("analyze_batch") for i in args[0]) == sorted(str(i) for i in ids)

    queue.jobs.clear()
    restart = await tasks.fit_price_calibration_task(
        {}
    )  # a deploy or a restart: nothing changed, minutes later
    assert restart["reanalysis"] is False
    assert queue.of("analyze_batch") == []

    # The calibration moved: everything is recomputed with it.
    async with session_scope() as s:
        await s.merge(
            SystemState(
                key="price_calibration",
                value={"n": 40, "shift": 0.05, "groups": {}, "ranges": {}, "fitted_at": "x", "metrics": {}},
            )
        )
    moved = await tasks.fit_price_calibration_task({})
    assert moved["reanalysis"] is True
    assert len(queue.of("analyze_batch")) == 1

    # A day later the market has moved even if the calibration did not.
    queue.jobs.clear()
    async with session_scope() as s:
        state = await s.get(SystemState, tasks.BULK_REANALYSIS_STATE)
        assert state is not None
        state.value = {**state.value, "at": (datetime.now(UTC) - timedelta(hours=21)).isoformat()}
    assert (await tasks.fit_price_calibration_task({}))["reanalysis"] is True


def test_the_bulk_re_analysis_is_due_for_a_changed_algorithm_or_calibration_or_a_day() -> None:
    now = datetime(2026, 10, 18, 5, 23, tzinfo=UTC)
    last = {"signature": "abc", "algorithm_version": "v1", "at": (now - timedelta(minutes=30)).isoformat()}
    assert tasks.bulk_reanalysis_due(None, "abc", "v1", now) is True
    assert tasks.bulk_reanalysis_due(last, "abc", "v1", now) is False
    assert tasks.bulk_reanalysis_due(last, "abd", "v1", now) is True
    assert tasks.bulk_reanalysis_due(last, "abc", "v2", now) is True
    old = {**last, "at": (now - timedelta(hours=21)).isoformat()}
    assert tasks.bulk_reanalysis_due(old, "abc", "v1", now) is True
    assert tasks.bulk_reanalysis_due({**last, "at": "garbage"}, "abc", "v1", now) is True


async def test_failed_analysis_rows_exist_per_listing_so_a_poison_batch_is_visible(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TT-11: one listing that raises must not stop its neighbours; only it is recorded as failed."""
    ids = await active_listings(make_listing, [{"title": f"Polo {i}", "price": 20 + i} for i in range(4)])
    poisoned = ids[2]
    real = AnalysisPipeline.analyze_many

    async def poisoned_analyze_many(self: AnalysisPipeline, listing_ids: Any, *a: Any, **k: Any) -> Any:
        if poisoned in set(listing_ids):
            raise ValueError("a listing the analysis cannot read")
        return await real(self, listing_ids, *a, **k)

    monkeypatch.setattr(AnalysisPipeline, "analyze_many", poisoned_analyze_many)
    res = await tasks.analyze_batch({}, [str(i) for i in ids])
    assert res["analyzed"] == 3  # the other three were analysed
    async with session_scope() as s:
        jobs = (await s.execute(select(AnalysisJob.listing_id, AnalysisJob.status))).all()
        opps = (await s.execute(select(Opportunity.listing_id, Opportunity.is_active))).all()
    status = {lid: st for lid, st in jobs}
    assert status[poisoned] == "failed"
    assert {lid for lid, st in status.items() if st == "succeeded"} == set(ids) - {poisoned}
    assert len(opps) == 4
