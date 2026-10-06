"""Ingestion, duplicate detection, analysis pipeline, alerts and database constraints."""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.alerts.service import evaluate_alerts
from app.analytics.market_stats import recompute_market_statistics
from app.core.security import hash_password
from app.db.models import (
    Alert,
    Listing,
    ListingPriceHistory,
    MarketComparable,
    MarketStatistic,
    NotificationSettings,
    Opportunity,
    User,
    UserPreferences,
    Watchlist,
)
from app.ingestion.catalog import load_catalog
from app.ingestion.service import IngestionService
from app.marketplace.base import ProviderListing
from app.opportunities.pipeline import AnalysisPipeline
from tests.conftest import NOW

pytestmark = pytest.mark.usefixtures("clean_db")


async def build_market(session, make_listing: Callable[..., ProviderListing]) -> None:
    """30 sold + 15 active Ralph Lauren Custom Slim Fit polos around 26-34 EUR, plus one outlier."""
    sold_prices = [
        24,
        25,
        26,
        26,
        27,
        27,
        28,
        28,
        28,
        29,
        29,
        30,
        30,
        30,
        31,
        31,
        32,
        32,
        33,
        34,
        26,
        27,
        28,
        29,
        30,
        31,
        28,
        29,
        27,
        30,
    ]
    listings = [
        make_listing(price=p, status="sold", published_days_ago=10 + i % 20, sold_after_days=3 + i % 5)
        for i, p in enumerate(sold_prices)
    ]
    listings += [
        make_listing(price=p, published_days_ago=1 + i)
        for i, p in enumerate([34, 35, 36, 33, 38, 39, 35, 37, 36, 34, 40, 35, 36, 37, 38])
    ]
    listings.append(make_listing(price=140, published_days_ago=4))  # absurd ask: must be ignored
    await IngestionService(session, "test").ingest(listings, now=NOW)
    await session.commit()


async def test_ingestion_normalizes_identifies_and_upserts(session, make_listing) -> None:
    pl = make_listing(title="Polo Ralph Lauren Custom Slim Fit blu navy M", price=28)
    res = await IngestionService(session, "test").ingest([pl], now=NOW)
    await session.commit()
    assert len(res.new_ids) == 1 and res.to_analyze == res.new_ids
    listing = await session.get(Listing, res.new_ids[0])
    catalog = await load_catalog(session)
    assert catalog.brand_slug(listing.brand_id) == "ralph-lauren"
    assert catalog.category_slug(listing.category_id) == "polo-shirts"
    assert listing.model_name == "Custom Slim Fit"
    assert listing.size_normalized == "M" and listing.condition == "very_good"
    assert listing.photo_count == 4 and listing.product_id is not None

    # Same listing again with a lower price: update + price history, not a new row.
    again = pl.model_copy(update={"price": D("22")})
    res2 = await IngestionService(session, "test").ingest([again], now=NOW + timedelta(hours=1))
    await session.commit()
    assert res2.new_ids == [] and len(res2.price_changes) == 1
    assert res2.price_changes[0].old_price == D("28") and res2.price_changes[0].new_price == D("22")
    count = (await session.execute(select(func.count()).select_from(Listing))).scalar_one()
    history = (
        (await session.execute(select(ListingPriceHistory.price).order_by(ListingPriceHistory.observed_at)))
        .scalars()
        .all()
    )
    assert count == 1
    assert history == [D("28"), D("22")]


async def test_repost_and_stolen_photo_detection(session, make_listing) -> None:
    original = make_listing(seller_id="seller-a", phash_seed=42, published_days_ago=5)
    await IngestionService(session, "test").ingest([original], now=NOW)
    await session.commit()
    repost = make_listing(seller_id="seller-a", phash_seed=999, published_days_ago=0.1)  # same title & seller
    stolen = make_listing(title="Polo RL bianca", seller_id="seller-b", phash_seed=42, published_days_ago=0.1)
    res = await IngestionService(session, "test").ingest([repost, stolen], now=NOW)
    await session.commit()
    repost_row = (
        await session.execute(select(Listing).where(Listing.external_id == repost.external_id))
    ).scalar_one()
    stolen_row = (
        await session.execute(select(Listing).where(Listing.external_id == stolen.external_id))
    ).scalar_one()
    assert repost_row.duplicate_of_id is not None
    assert repost_row.id not in res.to_analyze  # original still active -> not analysed twice
    assert stolen_row.duplicate_of_id is None
    assert stolen_row.identification["photos_reused_by_other_seller"] is True


async def test_pipeline_finds_undervalued_listing(session, make_listing) -> None:
    await build_market(session, make_listing)
    deal = make_listing(
        title="Polo Ralph Lauren Custom Slim Fit blu navy M", price=12, published_days_ago=0.05
    )
    res = await IngestionService(session, "test").ingest([deal], now=NOW)
    await session.commit()
    outcome = await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    await session.commit()
    assert outcome is not None and outcome.is_new
    r = outcome.result
    assert D("26") <= r.market.fair_market_value <= D("31")
    assert r.market.n_outliers >= 1  # the 140 EUR ask is excluded
    assert r.expected_profit > D("8") and r.expected_roi > D("0.4")
    assert r.flip.score >= 75
    assert r.confidence.score >= 60
    assert r.max_buy_price is not None and r.max_buy_price > D("12")

    opp = (
        await session.execute(select(Opportunity).where(Opportunity.listing_id == res.new_ids[0]))
    ).scalar_one()
    assert opp.flip_score == r.flip.score and opp.is_active
    assert opp.explanation and opp.ai_analysis["verdict"] in ("BUY", "CONSIDER")
    comps = (
        await session.execute(
            select(func.count())
            .select_from(MarketComparable)
            .where(MarketComparable.listing_id == opp.listing_id)
        )
    ).scalar_one()
    assert comps >= 20


async def test_fairly_priced_listing_is_not_an_opportunity(session, make_listing) -> None:
    await build_market(session, make_listing)
    fair = make_listing(price=29, published_days_ago=0.05)
    res = await IngestionService(session, "test").ingest([fair], now=NOW)
    outcome = await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    assert outcome.result.flip.score < 40
    assert outcome.result.analysis.verdict.value == "SKIP"


async def test_unknown_product_gets_low_confidence(session, make_listing) -> None:
    await build_market(session, make_listing)
    vague = make_listing(title="Maglia blu", brand=None, category=None, price=8, description="", photos=1)
    res = await IngestionService(session, "test").ingest([vague], now=NOW)
    outcome = await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    assert outcome.result.confidence.score < 50
    assert outcome.result.flip.score <= 35


async def test_market_statistics_and_alerts(session, make_listing) -> None:
    await build_market(session, make_listing)
    n = await recompute_market_statistics(session, 90)
    assert n >= 1
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
    assert stat.sold_count == 30 and D("26") <= stat.median_price <= D("31")

    user = User(email="a@b.it", password_hash=hash_password("x" * 12))
    session.add(user)
    await session.flush()
    session.add_all(
        [
            UserPreferences(user_id=user.id),
            NotificationSettings(
                user_id=user.id,
                alert_min_flip_score=70,
                alert_min_confidence=50,
                alert_min_profit=D("5"),
                alert_min_roi=D("0.3"),
            ),
            Watchlist(user_id=user.id, name="RL", brand_slugs=["ralph-lauren"], max_buy_price=D("25")),
        ]
    )
    await session.commit()
    deal = make_listing(price=12, published_days_ago=0.05)
    res = await IngestionService(session, "test").ingest([deal], now=NOW)
    listing = await session.get(Listing, res.new_ids[0])
    outcome = await AnalysisPipeline(session).analyze_listing(listing.id, now=NOW)
    pending = await evaluate_alerts(session, outcome, listing, await load_catalog(session), now=NOW)
    await session.commit()
    types = sorted(
        (await session.execute(select(Alert.type).where(Alert.user_id == user.id))).scalars().all()
    )
    assert "watchlist_match" in types
    assert "new_opportunity" in types or "ultra_deal" in types
    assert pending == []  # only in-app channel enabled
    # Re-evaluating the same analysis never duplicates alerts.
    await evaluate_alerts(session, outcome, listing, await load_catalog(session), now=NOW)
    await session.commit()
    assert len((await session.execute(select(Alert).where(Alert.user_id == user.id))).scalars().all()) == len(
        types
    )


async def test_sold_listing_deactivates_opportunity(session, make_listing) -> None:
    await build_market(session, make_listing)
    pl = make_listing(price=12, published_days_ago=0.05)
    res = await IngestionService(session, "test").ingest([pl], now=NOW)
    await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    await session.commit()
    sold = ProviderListing.model_validate({**pl.model_dump(), "status": "sold"})
    res2 = await IngestionService(session, "test").ingest([sold], now=NOW + timedelta(hours=2))
    assert res2.status_changes and res2.status_changes[0][2] == "sold"
    await AnalysisPipeline(session).deactivate([res2.status_changes[0][0]])
    await session.commit()
    opp = (
        await session.execute(select(Opportunity).where(Opportunity.listing_id == res.new_ids[0]))
    ).scalar_one()
    listing = await session.get(Listing, res.new_ids[0])
    assert opp.is_active is False
    assert listing.status == "sold" and listing.sold_at is not None  # kept for history, not deleted


async def test_database_constraints(session, make_listing) -> None:
    res = await IngestionService(session, "test").ingest([make_listing(external_id="dup-1")], now=NOW)
    await session.commit()
    session.add(Listing(provider="test", external_id="dup-1", url="u", title="t", price=D("1")))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
    session.add(Listing(provider="test", external_id="neg", url="u", title="t", price=D("-5")))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
    assert res.new_ids


async def test_overlapping_scans_are_serialized() -> None:
    """A long backfill must not overlap with the next scheduled scan (it used to deadlock)."""
    from app.core.redis import redis_lock
    from app.workers.tasks import scan_new_listings

    async with redis_lock("scan_new_listings", 30) as held:
        assert held is True
        assert await scan_new_listings({}) == {"skipped": "already_running"}


async def test_scan_lock_is_exclusive_and_released() -> None:
    from app.core.redis import redis_lock

    async with redis_lock("test-scan", 30) as first:
        assert first is True
        async with redis_lock("test-scan", 30) as second:
            assert second is False
    async with redis_lock("test-scan", 30) as again:
        assert again is True


async def test_batch_analysis_matches_single_analysis(session, make_listing) -> None:
    """analyze_many shares comparables pools and writes in bulk; results must not change."""
    from app.db.session import session_scope

    await build_market(session, make_listing)
    subjects = [
        make_listing(title="Polo Ralph Lauren Custom Slim Fit blu navy M", price=p, published_days_ago=0.05)
        for p in (12, 18, 25)
    ]
    res = await IngestionService(session, "test").ingest(subjects, now=NOW)
    await session.commit()
    ids = res.new_ids

    single = {}
    for lid in ids:
        async with session_scope() as s:
            o = await AnalysisPipeline(s).analyze_listing(lid, now=NOW)
            assert o is not None
            single[lid] = (o.result.flip.score, o.result.confidence.score, o.result.market.fair_market_value)
    async with session_scope() as s:
        outcomes = await AnalysisPipeline(s).analyze_many([*ids, ids[0]], now=NOW)  # duplicates ignored
    assert [o.listing_id for o in outcomes] == ids  # input order, once each
    for o in outcomes:
        assert (o.result.flip.score, o.result.confidence.score, o.result.market.fair_market_value) == single[
            o.listing_id
        ]
        assert not o.is_new and o.listing is not None
    comps = (
        await session.execute(
            select(MarketComparable.listing_id, func.count())
            .where(MarketComparable.listing_id.in_(ids))
            .group_by(MarketComparable.listing_id)
        )
    ).all()
    assert len(comps) == 3 and all(n >= 20 for _, n in comps)  # bulk COPY wrote every listing's comparables


async def test_feed_cache_bump_is_throttled() -> None:
    from app.core.cache import cache

    ns = "test-throttle"
    first = await cache.bump_throttled(ns, 30)
    second = await cache.bump_throttled(ns, 30)
    assert first is True and second is False
