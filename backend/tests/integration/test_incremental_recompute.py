"""A change redoes only what it touches: a price drop recomputes the decision and says so once.

Case "price 35 -> 23": the photos are not analysed again (no model call is queued), the product and
visual blocks of the new analysis are the old ones, exactly one analysis is recorded for the change,
and the person gets at most one alert even when the item is an Ultra Deal, dropped in price and
matches a watchlist all at once.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.agent.stages import Stage, needs_photo_check, stages_after
from app.db.models import Alert, Analysis, Listing
from app.db.session import session_scope
from app.ingestion.service import IngestionService
from app.workers import tasks, vision_queue
from tests.api.test_api import API
from tests.conftest import NOW

CLEAR_PHOTOS = {
    "analyzer": "claude",
    "defects": [],
    "photo_quality": {"has_label_photo": True},
    "photo_checks": [],
    "provenance": {"reused_photos": [], "catalog_photos": [], "stock_or_catalog": 0},
}


def test_the_stage_policy_matches_what_each_change_touches() -> None:
    assert stages_after("price_change") == {Stage.S4_DECISION, Stage.S5_NOTIFY}
    assert Stage.S2_VISION_LIGHT not in stages_after("price_change")
    assert Stage.S2_VISION_LIGHT in stages_after("photos") and Stage.S3_DEEP in stages_after("photos")
    assert Stage.S1_TRIAGE in stages_after("data_changed")
    assert stages_after("new") == frozenset(Stage)
    assert stages_after(None) == stages_after("manual") and Stage.S0_INGEST not in stages_after("manual")
    assert stages_after("something_unknown") == stages_after("manual")


def test_photos_are_checked_once_and_again_only_when_they_change() -> None:
    assert needs_photo_check("new", vision_done=False)
    assert not needs_photo_check("new", vision_done=True)
    assert not needs_photo_check("price_change", vision_done=True)
    assert not needs_photo_check("status_change", vision_done=True)
    assert needs_photo_check("photos", vision_done=True)
    assert not needs_photo_check("price_change", vision_done=False)  # not a photo stage at all


async def build_market_worth_45(session: Any, make_listing: Any) -> None:
    """Like the shared market but ~1.6x dearer (these polos sell for ~45 EUR): a 35 EUR ask is
    nothing special and 23 EUR is a real deal."""
    sold = [
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
    ]
    listings = [
        make_listing(
            price=round(p * 1.6), status="sold", published_days_ago=10 + i % 20, sold_after_days=3 + i % 5
        )
        for i, p in enumerate(sold)
    ]
    listings += [
        make_listing(price=round(p * 1.6), published_days_ago=1 + i)
        for i, p in enumerate([34, 35, 36, 33, 38, 39, 35, 37, 36, 34])
    ]
    await IngestionService(session, "test").ingest(listings, now=NOW)


async def _count(model: Any) -> int:
    async with session_scope() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_price_35_to_23_recomputes_only_the_decision_and_alerts_at_most_once(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The person wants every kind of alert on a Ralph Lauren watchlist, with easy thresholds.
    r = await auth_client.put(
        f"{API}/settings/notifications",
        json={
            "in_app_enabled": True,
            "alert_min_flip_score": 10,
            "alert_min_roi": 0.01,
            "alert_min_profit": 0,
            "alert_min_confidence": 10,
        },
    )
    assert r.status_code == 200, r.text
    r = await auth_client.post(
        f"{API}/watchlists",
        json={"name": "Ralph Lauren", "brand_slugs": ["ralph-lauren"], "min_flip_score": 10},
    )
    assert r.status_code in (200, 201), r.text

    async with session_scope() as s:
        await build_market_worth_45(s, make_listing)
    t0 = datetime.now(UTC) - timedelta(hours=2)  # listed an hour ago: still fresh for opportunity alerts
    pl = make_listing(price=35, published_days_ago=0.04, now=t0)
    async with session_scope() as s:
        res = await IngestionService(s, "test").ingest([pl], now=t0)
        listing_id = res.new_ids[0]
        listing = await s.get(Listing, listing_id)
        assert listing is not None
        listing.identification = {**(listing.identification or {}), "vision": CLEAR_PHOTOS}

    queued: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_enqueue(function: str, *args: Any, **kw: Any) -> bool:
        queued.append((function, args))
        return True

    monkeypatch.setattr(tasks, "enqueue", fake_enqueue)
    monkeypatch.setattr(vision_queue, "enqueue", fake_enqueue)

    first = await tasks.analyze_batch({"queue": "default"}, [str(listing_id)])
    assert first["analyzed"] == 1
    analyses_before, alerts_before = await _count(Analysis), await _count(Alert)
    queued.clear()

    # The seller drops the price from 35 to 23.
    async with session_scope() as s:
        res = await IngestionService(s, "test").ingest(
            [pl.model_copy(update={"price": D("23")})], now=t0 + timedelta(hours=1)
        )
    assert len(res.price_changes) == 1 and res.price_changes[0].new_price == D("23")
    second = await tasks.analyze_batch({"queue": "high"}, [str(listing_id)])
    assert second["analyzed"] == 1

    # 1. exactly one new analysis, caused by the price, with the product and the photos untouched.
    assert await _count(Analysis) == analyses_before + 1
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(Analysis).where(Analysis.listing_id == listing_id).order_by(Analysis.created_at)
                )
            )
            .scalars()
            .all()
        )
    old, new = rows[-2], rows[-1]
    assert new.trigger == "price_change"
    assert new.visual == old.visual
    assert {k: v for k, v in new.product.items() if k not in ("price",)} == {
        k: v for k, v in old.product.items() if k not in ("price",)
    }
    assert (
        new.decision["decision_verdict"] != old.decision["decision_verdict"] or new.economic != old.economic
    )

    # 2. no model work was queued: not a photo check, not an AI analysis.
    assert [f for f, _ in queued if f in ("vision_task", "ai_analyze_task")] == []

    # 3. at most one alert for the change, even though several rules match.
    created = await _count(Alert) - alerts_before
    assert created == 1
    async with session_scope() as s:
        alert = (await s.execute(select(Alert).order_by(Alert.created_at.desc()))).scalars().first()
    assert alert is not None and alert.type == "price_drop"
    assert "Anche:" in alert.body and "Ralph Lauren" in alert.body  # the watchlist is named inside it
