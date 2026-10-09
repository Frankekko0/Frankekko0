"""Phase 2.6: every analysis that changes the result is kept for good, in five blocks; an
identical re-run adds nothing; a price change adds one; stored analyses cannot be edited."""

from datetime import timedelta
from decimal import Decimal

import asyncpg
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.db.models import Analysis, Listing, Opportunity, OpportunityScore
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.opportunities.pipeline import AnalysisPipeline
from tests.conftest import NOW
from tests.integration.test_pipeline import build_market
from tests.integration.test_tracking_ingest import card


async def _analyse(session, ids, when, trigger=None):
    outcomes = await AnalysisPipeline(session).analyze_many(
        ids, now=when, mode=AcquisitionMode.EXTENSION_CARD, trigger=trigger
    )
    await session.commit()
    return outcomes


async def _count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_reanalysing_the_same_listing_creates_no_duplicates(session, make_listing) -> None:
    await build_market(session, make_listing)
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD)
    res = await svc.ingest([card(make_listing(external_id="7001", price=12))], now=NOW)
    await session.commit()
    [first] = await _analyse(session, res.new_ids, NOW)
    assert first.analysis_created and first.trigger == "new"
    before = (await _count(session, Analysis), await _count(session, OpportunityScore))

    for hours in (1, 2, 30):
        [again] = await _analyse(session, res.new_ids, NOW + timedelta(hours=hours))
        assert not again.analysis_created and again.analysis_id == first.analysis_id
    assert (await _count(session, Analysis), await _count(session, OpportunityScore)) == before
    opp = (await session.execute(select(Opportunity))).scalar_one()
    assert opp.analysis_id == first.analysis_id and opp.analyzed_at == NOW + timedelta(hours=30)


async def test_a_price_change_stores_a_new_analysis_and_points_the_opportunity_at_it(
    session, make_listing
) -> None:
    await build_market(session, make_listing)
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD)
    pl = card(make_listing(external_id="7002", price=14))
    res = await svc.ingest([pl], now=NOW)
    [first] = await _analyse(session, res.new_ids, NOW)

    await svc.ingest([pl.model_copy(update={"price": Decimal("9")})], now=NOW + timedelta(hours=3))
    await session.commit()
    [second] = await _analyse(session, res.new_ids, NOW + timedelta(hours=3))
    assert second.analysis_created and second.trigger == "price_change"
    assert second.analysis_id != first.analysis_id

    rows = (await session.execute(select(Analysis).order_by(Analysis.created_at))).scalars().all()
    assert [r.trigger for r in rows] == ["new", "price_change"]
    old, new = rows
    # The old one is exactly what it was; the new one was computed on the new price.
    assert old.economic["listing_price"] == "14" and new.economic["listing_price"] == "9"
    assert Decimal(new.economic["total_acquisition_cost"]) < Decimal(old.economic["total_acquisition_cost"])
    assert new.result_hash != old.result_hash and new.input_hash != old.input_hash
    opp = (await session.execute(select(Opportunity))).scalar_one()
    assert opp.analysis_id == new.id and opp.listing_price == Decimal("9")


async def test_a_record_carries_its_traceability_and_the_five_blocks(session, make_listing) -> None:
    await build_market(session, make_listing)
    res = await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD).ingest(
        [card(make_listing(external_id="7003", price=12))], now=NOW
    )
    await session.commit()
    await _analyse(session, res.new_ids, NOW)
    a = (await session.execute(select(Analysis))).scalar_one()
    li = (await session.execute(select(Listing).where(Listing.external_id == "7003"))).scalar_one()
    assert a.listing_id == li.id and a.vinted_id == "7003" and a.url == li.url
    assert a.source == "extension_card" and a.created_at == NOW
    assert a.schema_version == 1 and a.algorithm_version and a.trigger == "new"
    assert len(a.input_hash) == 64 and len(a.result_hash) == 64
    for block in ("product", "visual", "economic", "market", "decision"):
        assert getattr(a, block)["v"] == 1, block
    assert a.product["brand"] and a.product["price"] == "12"
    # No photo analysis yet: stated, not invented.
    assert a.visual["analysed"] is False
    assert {"expected", "conservative", "optimistic"} == set(a.economic["scenarios"])
    assert "used" in a.market["comparables"] and a.market["data_quality"] in ("ok", "limited", "insufficient")
    assert a.decision["verdict"] and isinstance(a.decision["flip_score"], int)


async def test_a_stored_analysis_cannot_be_edited(session, make_listing) -> None:
    await build_market(session, make_listing)
    res = await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD).ingest(
        [card(make_listing(external_id="7004", price=12))], now=NOW
    )
    await session.commit()
    await _analyse(session, res.new_ids, NOW)
    with pytest.raises(DBAPIError, match="analyses are immutable"):
        await session.execute(update(Analysis).values(trigger="manual"))
    await session.rollback()
    with pytest.raises(DBAPIError):
        await session.execute(text("UPDATE analyses SET decision = '{}'::jsonb"))
    await session.rollback()


async def test_migration_gives_every_existing_opportunity_a_first_record() -> None:
    import uuid

    from tests.integration.test_migration import ADMIN_DSN, MIG_DB, MIG_DSN, alembic

    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
    await admin.execute(f"CREATE DATABASE {MIG_DB}")
    await admin.close()
    c = None
    try:
        alembic("upgrade", "0014")
        c = await asyncpg.connect(MIG_DSN)
        lid = uuid.uuid4()
        await c.execute(
            "INSERT INTO listings (id, provider, external_id, url, title, price, status, capture_level)"
            " VALUES ($1, 'vinted', '9400', 'https://www.vinted.it/items/9400', 'Felpa', 18, 'active', 'full')",
            lid,
        )
        await c.execute(
            "INSERT INTO opportunities (id, listing_id, algorithm_version, listing_price, currency,"
            " total_acquisition_cost, flip_score, confidence_score, risk_score, risk_level, deal_tier, verdict,"
            " recommended_action, comparables_count, sold_comparables_count, score_breakdown, explanation,"
            " risk_factors, market_snapshot, expected_profit, expected_roi)"
            " VALUES ($1, $2, '2026.10-1', 18, 'EUR', 21, 81, 70, 20, 'low', 'excellent', 'BUY', 'buy_now',"
            " 12, 5, '{}', '[]', '[]', '{}', 15, 0.7)",
            uuid.uuid4(),
            lid,
        )
        alembic("upgrade", "0015")
        a = await c.fetchrow("SELECT * FROM analyses")
        assert a["trigger"] == "migrated" and a["vinted_id"] == "9400" and a["schema_version"] == 1
        assert a["url"] == "https://www.vinted.it/items/9400"
        import json

        decision, economic = json.loads(a["decision"]), json.loads(a["economic"])
        assert decision["flip_score"] == 81 and decision["verdict"] == "BUY"
        assert float(economic["scenarios"]["expected"]["profit"]) == 15.0
        assert await c.fetchval("SELECT analysis_id FROM opportunities") == a["id"]
        # The row is protected from the first moment.
        with pytest.raises(asyncpg.RaiseError):
            await c.execute("UPDATE analyses SET trigger = 'manual'")
        alembic("downgrade", "0014")
        assert await c.fetchval("SELECT count(*) FROM opportunities") == 1
    finally:
        if c is not None:
            await c.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
        await admin.close()
