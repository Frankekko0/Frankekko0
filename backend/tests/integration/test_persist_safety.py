"""An older analysis never overwrites a newer state (dup-F4), and an identical one rewrites nothing (F4 perf).

* an analysis takes a second or two between reading the listing and writing the result: a capture that lands in between
  (a new price, a sale) must not be undone by the result computed from the old state;
* re-analysing a listing whose analysis comes out exactly the same (a revisit, the nightly job right after a capture)
  used to rewrite the whole opportunity row and delete and copy the sixty comparables for nothing.
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.db.models import Analysis, Listing, MarketComparable, Opportunity
from app.db.session import session_scope
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.opportunities.pipeline import AnalysisPipeline
from tests.conftest import NOW
from tests.integration.test_pipeline import build_market

pytestmark = pytest.mark.usefixtures("clean_db")


async def ingest(listing: Any, hours: float = 0.0) -> uuid.UUID:
    async with session_scope() as s:
        res = await IngestionService(s, "test", AcquisitionMode.EXTENSION_ITEM).ingest(
            [listing], now=NOW + timedelta(hours=hours)
        )
        return res.ids_by_external[listing.external_id]


async def analyse(listing_id: uuid.UUID, hours: float = 0.0) -> Any:
    async with session_scope() as s:
        outcomes = await AnalysisPipeline(s).analyze_many([listing_id], NOW + timedelta(hours=hours))
        return outcomes[0] if outcomes else None


async def market(make_listing: Any) -> None:
    async with session_scope() as s:
        await build_market(s, make_listing)


async def opportunity(listing_id: uuid.UUID) -> Opportunity:
    async with session_scope() as s:
        row = (await s.execute(select(Opportunity).where(Opportunity.listing_id == listing_id))).scalar_one()
        s.expunge(row)
        return row


async def xmins(table: str, where: str, **params: Any) -> list[int]:
    """Row versions: unchanged while a row is not rewritten."""
    async with session_scope() as s:
        rows = await s.execute(
            text(f"SELECT xmin::text::bigint FROM {table} WHERE {where} ORDER BY id"),  # noqa: S608
            params,
        )
        return [r[0] for r in rows]


async def interleaved(monkeypatch: pytest.MonkeyPatch, between: Any) -> None:
    """Run ``between`` after the analysis is computed and before it is persisted (the worker's window)."""
    real = AnalysisPipeline.persist_many

    async def persist_many(self: AnalysisPipeline, items: Any, *a: Any, **k: Any) -> Any:
        monkeypatch.setattr(AnalysisPipeline, "persist_many", real)
        await between()
        return await real(self, items, *a, **k)

    monkeypatch.setattr(AnalysisPipeline, "persist_many", persist_many)


async def test_an_analysis_of_an_old_price_does_not_overwrite_the_capture_that_changed_it(
    make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await market(make_listing)
    pl = make_listing(price=25, external_id="7001")
    lid = await ingest(pl)
    await analyse(lid)
    assert (await opportunity(lid)).listing_price == Decimal("25")

    async def capture_cheaper() -> None:
        listing_id = await ingest(pl.model_copy(update={"price": Decimal("7")}), hours=0.5)
        assert listing_id == lid
        await analyse(lid, hours=0.5)  # the capture's own analysis, at the new price

    await interleaved(monkeypatch, capture_cheaper)
    async with session_scope() as s:  # the worker's analysis, read at price 25, is still in flight
        pipeline = AnalysisPipeline(s)
        outcomes = await pipeline.analyze_many([lid], NOW + timedelta(hours=1))
        assert outcomes == [] and pipeline.superseded == [lid]  # not written: the caller analyses again

    opp = await opportunity(lid)
    async with session_scope() as s:
        price = (await s.execute(select(Listing.price).where(Listing.id == lid))).scalar_one()
    assert price == Decimal("7") and opp.listing_price == Decimal("7"), "the older analysis won"


async def test_an_analysis_started_while_the_item_was_for_sale_does_not_bring_a_sold_item_back(
    make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await market(make_listing)
    pl = make_listing(price=25, external_id="7002")
    lid = await ingest(pl)
    await analyse(lid)
    assert (await opportunity(lid)).is_active is True

    async def sold_meanwhile() -> None:
        await ingest(pl.model_copy(update={"status": "sold"}), hours=0.5)
        async with session_scope() as s:
            await AnalysisPipeline(s).deactivate([lid])  # what the capture does for a closed listing

    await interleaved(monkeypatch, sold_meanwhile)
    async with session_scope() as s:
        pipeline = AnalysisPipeline(s)
        assert await pipeline.analyze_many([lid], NOW + timedelta(hours=1)) == []
        assert pipeline.superseded == [lid]
    assert (await opportunity(lid)).is_active is False, "a sold item is buyable again"


async def test_an_analysis_of_unchanged_listings_is_written_as_before(make_listing: Any) -> None:
    await market(make_listing)
    lid = await ingest(make_listing(price=25, external_id="7003"))
    async with session_scope() as s:
        pipeline = AnalysisPipeline(s)
        outcomes = await pipeline.analyze_many([lid], NOW)
        assert len(outcomes) == 1 and pipeline.superseded == []


async def test_an_identical_analysis_rewrites_neither_the_opportunity_nor_its_comparables(
    make_listing: Any,
) -> None:
    await market(make_listing)
    lid = await ingest(make_listing(price=25, external_id="7004"))
    first = await analyse(lid)
    assert first.written is True and first.analysis_created is True
    opp_before = await opportunity(lid)
    opp_x = await xmins("opportunities", "listing_id = :l", l=lid)
    cmp_x = await xmins("market_comparables", "listing_id = :l", l=lid)
    assert len(cmp_x) > 5, "the fixture must have comparables"
    async with session_scope() as s:
        analyses = (await s.execute(select(func.count()).select_from(Analysis))).scalar_one()

    second = await analyse(lid, hours=0.25)  # a revisit a quarter of an hour later: nothing moved
    assert second.analysis_created is False and second.written is False
    assert second.opportunity_id == first.opportunity_id
    assert await xmins("opportunities", "listing_id = :l", l=lid) == opp_x, (
        "the opportunity row was rewritten"
    )
    assert await xmins("market_comparables", "listing_id = :l", l=lid) == cmp_x, "comparables were re-copied"
    assert (await opportunity(lid)).analyzed_at == opp_before.analyzed_at
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Analysis))).scalar_one() == analyses

    # Hours later the time-dependent text of the dossier is refreshed (the row is written), the comparables are
    # still exactly the same and are left alone.
    third = await analyse(lid, hours=5)
    assert third.analysis_created is False and third.written is True
    assert (await opportunity(lid)).analyzed_at > opp_before.analyzed_at
    assert await xmins("market_comparables", "listing_id = :l", l=lid) == cmp_x


async def test_a_changed_comparable_set_is_still_replaced(make_listing: Any) -> None:
    await market(make_listing)
    lid = await ingest(make_listing(price=25, external_id="7005"))
    await analyse(lid)
    cmp_x = await xmins("market_comparables", "listing_id = :l", l=lid)
    async with session_scope() as s:  # a stored row the analysis no longer produces (a stale copy)
        await s.execute(
            text(
                "DELETE FROM market_comparables WHERE id IN (SELECT id FROM market_comparables WHERE listing_id = :l LIMIT 1)"
            ),
            {"l": lid},
        )
    await analyse(lid, hours=0.1)
    after = await xmins("market_comparables", "listing_id = :l", l=lid)
    assert len(after) == len(cmp_x), "the missing comparable row is restored"
    async with session_scope() as s:
        n = (
            await s.execute(
                select(func.count()).select_from(MarketComparable).where(MarketComparable.listing_id == lid)
            )
        ).scalar_one()
    assert n == len(cmp_x)
