"""Comparable candidates and segment statistics, against the real database (PX-05, PX-06, PX-11).

PX-05: the pool of a brand/category family is cut by recency, so a rare model vanished behind a popular one
(260 recent Air Force 1 hid 12 older Air Max 95: the Air Max at 55 was called fairly priced, its market is ~103).
PX-06: a removed listing is not a price: 30 vanished asks at 80 lifted the segment prior of a market sold at 30.
PX-11: every stored comparable says why it is, or is not, part of the price.
"""

from collections.abc import Callable
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.analytics.market_stats import recompute_market_statistics
from app.db.models import Listing, MarketStatistic
from app.ingestion.catalog import load_catalog
from app.ingestion.service import IngestionService
from app.marketplace.base import ProviderListing
from app.opportunities.pipeline import AnalysisPipeline, profile_from_row
from tests.conftest import NOW

pytestmark = pytest.mark.usefixtures("clean_db")

AF1 = "Nike Air Force 1 scarpe sneakers uomo 42"
AM95 = "Nike Air Max 95 scarpe sneakers uomo 42"
AM95_PRICES = [100, 105, 98, 110, 102, 106, 99, 104, 108, 101, 103, 107]


def shoe(make: Callable[..., ProviderListing], title: str, price: float, **kw) -> ProviderListing:
    return make(title=title, price=price, brand="Nike", category="Sneakers", size="EU 42", **kw)


async def seed_crowded_family(session, make: Callable[..., ProviderListing]) -> None:
    """260 recent sold Air Force 1 (6-45 days) and 12 older sold Air Max 95 (70-103 days) of the same brand/category."""
    rows = [
        shoe(make, AF1, 50 + i % 9, status="sold", published_days_ago=6 + i % 40, sold_after_days=3) for i in range(260)
    ]
    rows += [
        shoe(make, AM95, p, status="sold", published_days_ago=70 + i * 3, sold_after_days=4)
        for i, p in enumerate(AM95_PRICES)
    ]
    await IngestionService(session, "test").ingest(rows, now=NOW)
    await session.commit()


async def test_g1_a_rare_model_is_not_hidden_by_the_recent_listings_of_a_popular_one(session, make_listing) -> None:
    await seed_crowded_family(session, make_listing)
    deal = shoe(make_listing, AM95, 55, published_days_ago=0.05)
    res = await IngestionService(session, "test").ingest([deal], now=NOW)
    await session.commit()
    outcome = await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    assert outcome is not None
    r = outcome.result
    # the 12 Air Max 95 reach the engine and are the price (the 260 Air Force 1 are reference only)
    assert r.market.tier in ("model", "line")
    assert r.market.n_used >= 10 and r.market.n_specific == r.market.n_used
    assert abs(float(r.market.fair_market_value) - 103) / 103 < 0.12
    assert r.discount_vs_market is not None and r.discount_vs_market > D("0.3")
    assert r.decision.verdict.value in ("BUY", "STRONG_BUY", "NEGOTIATE")


async def test_the_candidate_pool_is_fetched_by_model_beyond_the_recency_cut(session, make_listing) -> None:
    await seed_crowded_family(session, make_listing)
    catalog = await load_catalog(session)
    pipeline = AnalysisPipeline(session)
    category_ids = catalog.sibling_category_ids("sneakers")
    brand_id = catalog.brand_id("nike")
    pool = await pipeline.candidate_pool(brand_id, category_ids, catalog, NOW)
    assert not [c for c in pool.sold if c.model == "Air Max 95"]  # the recency cut hides the rare model
    await pipeline.add_strata(pool, brand_id, category_ids, catalog, NOW, {"Air Max 95"}, set())
    own = [c for c in pool.sold if c.model == "Air Max 95"]
    assert len(own) == 12
    # a model is looked up once per pool: asking again adds nothing
    await pipeline.add_strata(pool, brand_id, category_ids, catalog, NOW, {"Air Max 95"}, set())
    assert len([c for c in pool.sold if c.model == "Air Max 95"]) == 12
    assert len({c.id for c in pool.sold}) == len(pool.sold)
    # and the subject receives its own model's rows on top of the recent quota
    got = pool.for_subject(None, "Air Max 95", None)
    assert len([c for c in got if c.model == "Air Max 95"]) == 12


# ---------------------------------------------------------------------------------------- PX-06
async def segment_median(session) -> D:
    stat = (
        (
            await session.execute(
                select(MarketStatistic).where(
                    MarketStatistic.model_name.is_(None), MarketStatistic.size_normalized.is_(None)
                )
            )
        )
        .scalars()
        .first()
    )
    return stat.median_price


async def test_removed_listings_never_enter_the_segment_prices(session, make_listing) -> None:
    rows = [
        shoe(make_listing, "Nike giacca uomo M", p, status="sold", published_days_ago=10 + i, sold_after_days=3)
        for i, p in enumerate([28, 29, 30, 30, 31, 32])
    ]
    await IngestionService(session, "test").ingest(rows, now=NOW)
    await session.commit()
    await recompute_market_statistics(session, 90)
    before = await segment_median(session)
    removed = [
        shoe(make_listing, "Nike giacca uomo M", 80 + i % 5, status="removed", published_days_ago=20 + i % 30)
        for i in range(30)
    ]
    await IngestionService(session, "test").ingest(removed, now=NOW)
    await session.commit()
    await recompute_market_statistics(session, 90)
    after = await segment_median(session)
    assert before == after and D("28") <= after <= D("32")


async def test_a_segment_made_only_of_removed_listings_has_no_price(session, make_listing) -> None:
    removed = [
        shoe(make_listing, "Nike giacca uomo M", 80 + i % 5, status="removed", published_days_ago=20 + i % 30)
        for i in range(10)
    ]
    await IngestionService(session, "test").ingest(removed, now=NOW)
    await session.commit()
    await recompute_market_statistics(session, 90)
    stats = (await session.execute(select(MarketStatistic))).scalars().all()
    assert not [s for s in stats if s.median_price and s.sold_count == 0 and s.active_count == 0]


# ---------------------------------------------------------------------------------------- PX-11
async def test_every_stored_comparable_says_why_it_is_or_is_not_in_the_price(session, make_listing) -> None:
    await seed_crowded_family(session, make_listing)
    deal = shoe(make_listing, AM95, 55, published_days_ago=0.05)
    res = await IngestionService(session, "test").ingest([deal], now=NOW)
    await session.commit()
    outcome = await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    subject_row = await session.get(Listing, res.new_ids[0])
    catalog = await load_catalog(session)
    subject = profile_from_row(subject_row, catalog)
    from app.pricing.comparison import exclusion_label, explain_comparable

    comps = outcome.result.market.comparables
    own = next(c for c in comps if c.included)
    other = next(c for c in comps if not c.included and c.exclusion_reason == "other_line")
    why_own = explain_comparable(subject, own.item)
    why_other = explain_comparable(subject, other.item)
    assert why_own["tier"] in ("model", "line") and any("stess" in m for m in why_own["match"])
    assert why_other["tier"] == "family" and "altra linea o modello" in why_other["match"]
    assert exclusion_label("other_line") and "riferimento" in exclusion_label("other_line")
    assert exclusion_label("other_category") and exclusion_label(None) is None
