"""Card captures without repeated work: unchanged cards keep their analysis, pools are shared."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update

from app.api import capture_pipeline
from app.api.capture_pipeline import CapturePipeline, forget_pools, sync_pools
from app.db.models import Listing, Opportunity, OpportunityScore
from app.db.session import session_scope
from app.ingestion.service import IngestionService
from app.opportunities.pipeline import AnalysisPipeline
from tests.api.test_api import API
from tests.api.test_extension_api import CARD, _paired
from tests.conftest import NOW
from tests.integration.test_pipeline import build_market

TITLE = "Polo Ralph Lauren Custom Slim Fit blu navy M"


def _card(n: int, price: float, **kw: Any) -> dict[str, Any]:
    return {**CARD, "url": f"https://www.vinted.it/items/{n}-polo", "title": TITLE, "price": price, **kw}


async def _scores() -> int:
    async with session_scope() as s:
        return (await s.execute(select(func.count()).select_from(OpportunityScore))).scalar_one()


async def _post(client: httpx.AsyncClient, headers: dict[str, str], cards: list[dict[str, Any]]) -> Any:
    r = await client.post(f"{API}/capture/cards", json={"items": cards}, headers=headers)
    assert r.status_code == 200, r.text
    return r


async def test_unchanged_cards_keep_their_analysis(auth_client: httpx.AsyncClient, make_listing: Any) -> None:
    async with session_scope() as s:
        await build_market(s, make_listing)
    headers = await _paired(auth_client)
    cards = [_card(8400, 12), _card(8401, 15), _card(8402, 18)]
    first = await _post(auth_client, headers, cards)
    assert await _scores() == 3

    # Seen again, nothing changed: no new analysis, same evaluations.
    again = await _post(auth_client, headers, cards)
    assert await _scores() == 3
    assert [e["flip_score"] for e in again.json()["evaluations"]] == [
        e["flip_score"] for e in first.json()["evaluations"]
    ]
    assert "analysis;dur=" in again.headers["server-timing"]

    # A new price, a new title, a new status: only those are analysed again (the reserved one is
    # taken off the opportunities instead).
    changed = [_card(8400, 11), _card(8401, 15, title=TITLE + " nuova"), _card(8402, 18, status="reserved")]
    await _post(auth_client, headers, changed)
    assert await _scores() == 5
    async with session_scope() as s:
        reserved = (
            await s.execute(
                select(Opportunity.is_active)
                .join(Listing, Listing.id == Opportunity.listing_id)
                .where(Listing.external_id == "8402")
            )
        ).scalar_one()
    assert reserved is False

    # An analysis older than the reuse window, or of another algorithm version, is redone.
    async with session_scope() as s:
        lid = (await s.execute(select(Listing.id).where(Listing.external_id == "8400"))).scalar_one()
        await s.execute(
            update(Opportunity)
            .where(Opportunity.listing_id == lid)
            .values(
                analyzed_at=datetime.now(UTC) - capture_pipeline.REUSE_ANALYSIS_FOR - timedelta(minutes=1)
            )
        )
        lid2 = (await s.execute(select(Listing.id).where(Listing.external_id == "8401"))).scalar_one()
        await s.execute(
            update(Opportunity).where(Opportunity.listing_id == lid2).values(algorithm_version="old")
        )
    await _post(auth_client, headers, changed[:2])
    assert await _scores() == 7


async def test_pools_shared_between_requests(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_scope() as s:
        await build_market(s, make_listing)
    loads: list[int] = []
    original = AnalysisPipeline.candidate_pool

    async def counting(self: Any, brand_id: int, *args: Any) -> Any:
        loads.append(brand_id)
        return await original(self, brand_id, *args)

    monkeypatch.setattr(AnalysisPipeline, "candidate_pool", counting)
    forget_pools()
    headers = await _paired(auth_client)
    await _post(auth_client, headers, [_card(8500, 12), _card(8501, 14)])
    assert len(loads) == 1  # one pool for the batch
    await _post(auth_client, headers, [_card(8502, 13)])
    assert len(loads) == 1  # next request of the page: same pool, no query
    # A sold item changes the market: every process drops its copies.
    await _post(auth_client, headers, [_card(8503, 25, status="sold"), _card(8504, 16)])
    assert len(loads) == 2
    # The same sold item seen again (a closet page reloaded) changes nothing.
    await _post(auth_client, headers, [_card(8503, 25, status="sold"), _card(8506, 15)])
    assert len(loads) == 2
    # An active item turning sold does.
    await _post(auth_client, headers, [_card(8500, 12, status="sold"), _card(8507, 15)])
    assert len(loads) == 3
    # Expired copies are reloaded.
    monkeypatch.setattr(capture_pipeline, "POOL_TTL_SECONDS", 0.0)
    await _post(auth_client, headers, [_card(8505, 17)])
    assert len(loads) == 4


async def test_shared_pool_gives_the_same_analysis(session: Any, make_listing: Any) -> None:
    await build_market(session, make_listing)
    res = await IngestionService(session, "test").ingest(
        [make_listing(price=p, published_days_ago=0.05) for p in (12, 16)], now=NOW
    )
    await session.commit()
    forget_pools()
    assert await sync_pools()
    warm = await CapturePipeline(session).analyze_many([res.new_ids[0]], now=NOW)  # loads the pool
    shared = await CapturePipeline(session).analyze_many([res.new_ids[1]], now=NOW)  # reuses it
    fresh = await AnalysisPipeline(session).analyze_many([res.new_ids[1]], now=NOW)
    assert warm and shared and fresh
    a, b = shared[0].result, fresh[0].result
    assert a.flip.score == b.flip.score and a.risk_adjusted_profit == b.risk_adjusted_profit
    assert a.market.expected_sale_price == b.market.expected_sale_price and a.market.n_used == b.market.n_used
    assert [c.item.id for c in a.comparables] == [c.item.id for c in b.comparables]
    await session.rollback()


async def test_pools_unused_without_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    class Down:
        async def get(self, *a: Any, **k: Any) -> Any:
            raise RedisConnectionError("down")

        async def set(self, *a: Any, **k: Any) -> Any:
            raise RedisConnectionError("down")

    monkeypatch.setattr(capture_pipeline, "get_redis", lambda: Down())
    assert await sync_pools() is False
