"""What a capture of the browser extension sets in motion (ingest wave: F10/TT-03, F2, F4, F6, PX-12, TT-11).

The extension is the only source of listings in this deployment, so everything that used to wait for a worker pass
happens in the capture itself: alerts (new find, price drop, watchlist), the strong model's review of what the user is
looking at, the removal of a listing the user found gone. A page seen again unchanged writes and computes nothing, and
one bad card never takes the page down with it.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.api.v1 import extension
from app.core.cache import NS_FEED, cache
from app.core.config import get_settings
from app.db.models import Alert, Listing, Opportunity
from app.db.session import session_scope
from tests.api.test_api import API, seed_deal
from tests.api.test_extension_api import CARD, _paired
from tests.integration.test_incremental_recompute import CLEAR_PHOTOS, build_market_worth_45

URL = "https://www.vinted.it/items/{vid}-polo-ralph-lauren"


def card(vid: int, price: float, **kw: Any) -> dict[str, Any]:
    return {
        **CARD,
        "url": URL.format(vid=vid),
        "title": "Polo Ralph Lauren Custom Slim Fit",
        "price": price,
        **kw,
    }


def item(vid: int, price: float) -> dict[str, Any]:
    """An item page as complete as the extension reads it (gallery, seller, date): enough for a buy verdict."""
    return {
        **card(vid, price),
        "description": "Polo in ottime condizioni, poche volte indossata. Spedisco subito.",
        "image_urls": [f"https://images1.vinted.net/t/01_{vid}/f800/{i}.webp" for i in range(4)],
        "images_source": "item_json",
        "seller_key": "h:0123456789abcdef01234567",
        "seller_rating": 4.9,
        "seller_review_count": 120,
        "shipping_fee": 3.49,
        "favourite_count": 4,
        "published_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "published_at_kind": "relative",  # as the page says it ("1 hour ago"): drifts with the clock
    }


async def post_cards(client: httpx.AsyncClient, headers: dict[str, str], cards: list[dict[str, Any]]) -> Any:
    r = await client.post(
        f"{API}/capture/cards", json={"page_type": "catalog", "items": cards}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()


async def post_item(client: httpx.AsyncClient, headers: dict[str, str], body: dict[str, Any]) -> Any:
    r = await client.post(f"{API}/capture/item", json={"item": body}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def easy_alerts(client: httpx.AsyncClient, watch_ralph_lauren: bool = True) -> None:
    """Every kind of alert on, with thresholds that anything reasonable passes, and a Ralph Lauren watchlist."""
    r = await client.put(
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
    if watch_ralph_lauren:
        w = await client.post(
            f"{API}/watchlists",
            json={"name": "Ralph Lauren", "brand_slugs": ["ralph-lauren"], "min_flip_score": 10},
        )
        assert w.status_code in (200, 201), w.text


async def alert_types() -> list[str]:
    async with session_scope() as s:
        return sorted((await s.execute(select(Alert.type))).scalars().all())


async def opportunity(vid: str) -> Opportunity:
    async with session_scope() as s:
        row = (
            await s.execute(
                select(Opportunity)
                .join(Listing, Listing.id == Opportunity.listing_id)
                .where(Listing.external_id == vid)
            )
        ).scalar_one()
        s.expunge(row)
        return row


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    """Every job the capture queues (alert deliveries), and none of them run."""
    jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def fake_enqueue(function: str, *args: Any, **kw: Any) -> bool:
        jobs.append((function, args, kw))
        return True

    monkeypatch.setattr(extension, "enqueue", fake_enqueue)
    return jobs


# ------------------------------------------------------------------ alerts on the extension-only path
async def test_a_watchlist_match_alerts_within_the_capture_and_only_once(
    auth_client: httpx.AsyncClient, make_listing: Any, queued: Any
) -> None:
    await seed_deal(make_listing)
    await easy_alerts(auth_client)
    headers = await _paired(auth_client)
    assert await alert_types() == []

    await post_cards(auth_client, headers, [card(9501, 12)])
    assert await alert_types() == ["watchlist_match"], (
        "nobody was told about a watched brand, seen in the capture"
    )

    await post_cards(auth_client, headers, [card(9501, 12)])  # the page seen again, nothing changed
    await post_cards(
        auth_client, headers, [card(9501, 11)]
    )  # a little cheaper: the same alert, not a second one
    assert await alert_types() == ["watchlist_match"]


async def test_a_new_find_and_a_price_drop_on_an_item_page_alert_when_the_capture_arrives(
    auth_client: httpx.AsyncClient, make_listing: Any, queued: Any
) -> None:
    async with session_scope() as s:
        await build_market_worth_45(s, make_listing)
    await easy_alerts(auth_client, watch_ralph_lauren=False)
    headers = await _paired(auth_client)

    await post_item(
        auth_client, headers, item(9511, 35)
    )  # 35 EUR on a 45 EUR market, seen for the first time
    assert await alert_types() == ["new_opportunity"], "a new find raised no alert in the capture"
    async with session_scope() as s:  # the photos were checked and found nothing wrong
        listing = (await s.execute(select(Listing).where(Listing.external_id == "9511"))).scalar_one()
        listing.identification = {**(listing.identification or {}), "vision": CLEAR_PHOTOS}

    await post_item(auth_client, headers, item(9511, 23))  # the seller drops it by a third
    types = await alert_types()
    assert "price_drop" in types, f"a price drop of a third raised {types}"
    before = len(types)
    await post_item(auth_client, headers, item(9511, 23))  # opened again
    assert len(await alert_types()) == before
    # and the delivery of the new alerts was queued after the capture (in-app alerts need no delivery job)
    assert all(job[0] == "deliver_alert_task" for job in queued)


async def test_the_nightly_worker_does_not_double_the_alerts_the_capture_raised(
    auth_client: httpx.AsyncClient, make_listing: Any, queued: Any
) -> None:
    from app.workers import tasks

    await seed_deal(make_listing)
    await easy_alerts(auth_client)
    headers = await _paired(auth_client)
    await post_cards(auth_client, headers, [card(9502, 12)])
    before = len(await alert_types())
    assert before > 0
    async with session_scope() as s:
        lid = (await s.execute(select(Listing.id).where(Listing.external_id == "9502"))).scalar_one()
    await tasks.analyze_batch({}, [str(lid)])
    assert len(await alert_types()) == before, "dedupe_key must keep the worker from alerting again"


# ------------------------------------------------------------------ the strong model's review
async def test_a_capture_asks_for_the_review_of_a_find_at_once_and_reports_it_pending(
    auth_client: httpx.AsyncClient, make_listing: Any, queued: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    keyed = get_settings().model_copy(
        update={
            "ai_api_key": SecretStr("test-key"),
            "ai_auto_analyze_min_flip_score": 0,
            "ai_review_scope": "all",
        }
    )
    monkeypatch.setattr(extension, "get_settings", lambda: keyed)
    kicked: list[tuple[list[Any], bool]] = []

    async def fake_kick(candidates: Any, settings: Any = None, *, interactive: bool = False) -> int:
        kicked.append((list(candidates), interactive))
        return len(candidates)

    monkeypatch.setattr(extension, "kick_ai_review", fake_kick)

    body = await post_item(auth_client, headers, item(9503, 12))
    assert kicked, (
        "the capture did not ask for the review: the minute sweep would have found it, in rank order"
    )
    assert kicked[0][1] is True, "it is the user's own capture: it goes ahead of the backlog"
    assert [str(oid) for _, oid in kicked[0][0]] == [str((await opportunity("9503")).id)]
    assert body["review_pending"] is True


# ------------------------------------------------------------------ the same page again
async def test_an_item_page_opened_again_unchanged_computes_nothing_and_keeps_the_dashboard_cache(
    auth_client: httpx.AsyncClient, make_listing: Any, queued: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.opportunities.pipeline import AnalysisPipeline

    analysed: list[int] = []
    real = AnalysisPipeline.analyze_many

    async def counting(self: AnalysisPipeline, listing_ids: Any, *a: Any, **k: Any) -> Any:
        analysed.append(len(list(listing_ids)))
        return await real(self, listing_ids, *a, **k)

    monkeypatch.setattr(AnalysisPipeline, "analyze_many", counting)
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    analysed.clear()
    first = await post_item(auth_client, headers, item(9504, 12))
    assert first["analysis"] is not None
    opp = await opportunity("9504")
    version = await cache._version(NS_FEED)

    analysed.clear()
    again = await post_item(auth_client, headers, item(9504, 12))
    assert sum(analysed) == 0, "the same page, unchanged, was analysed again"
    assert again["analysis"] == first["analysis"], "the stored analysis is returned"
    same = await opportunity("9504")
    assert same.analyzed_at == opp.analyzed_at and same.analysis_id == opp.analysis_id
    assert await cache._version(NS_FEED) == version, "an unchanged page emptied the dashboards' cache"

    cheaper = await post_item(auth_client, headers, item(9504, 10))  # a changed price is analysed and shown
    assert cheaper["analysis"] != first["analysis"]
    assert (await opportunity("9504")).listing_price < opp.listing_price
    assert await cache._version(NS_FEED) != version


# ------------------------------------------------------------------ one bad card is not the page
async def test_one_bad_card_does_not_take_the_page_down(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    cards = [card(9601, 12), {**card(9602, 12), "price": 0}, card(9603, 14), {**card(9604, 12), "title": "x"}]
    body = await post_cards(auth_client, headers, cards)
    assert body["received"] == 4 and body["stored"] == 2
    assert {e["vinted_id"] for e in body["errors"]} == {"9602", "9604"}
    assert all("Scheda non valida" in e["message"] for e in body["errors"])
    assert {e["vinted_id"] for e in body["evaluations"]} == {"9601", "9603"}

    only_bad = await auth_client.post(
        f"{API}/capture/cards",
        json={"items": [{**card(9605, 12), "price": 0}, {**card(9606, 12), "price": -1}]},
        headers=headers,
    )
    assert only_bad.status_code == 422, "with no valid card there is nothing to store"


async def test_a_card_the_server_cannot_process_is_left_out_and_named(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.opportunities.pipeline import AnalysisPipeline

    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    real = AnalysisPipeline.analyze_many

    async def poisoned(self: AnalysisPipeline, listing_ids: Any, *a: Any, **k: Any) -> Any:
        ext = select(Listing.external_id).where(Listing.id.in_(list(listing_ids)))
        vids = (await self.session.execute(ext)).scalars().all()  # the capture's own transaction
        if "9702" in vids:
            raise ValueError("a listing the analysis cannot read")
        return await real(self, listing_ids, *a, **k)

    monkeypatch.setattr(AnalysisPipeline, "analyze_many", poisoned)
    body = await post_cards(
        auth_client, headers, [card(9701, 12), card(9702, 12), card(9703, 14), card(9704, 13)]
    )
    assert body["stored"] == 3 and [e["vinted_id"] for e in body["errors"]] == ["9702"]
    async with session_scope() as s:
        known = (
            (await s.execute(select(Listing.external_id).where(Listing.external_id.like("97%"))))
            .scalars()
            .all()
        )
    assert set(known) == {"9701", "9703", "9704"}, (
        "the card at fault left rows behind, or a neighbour was lost"
    )


# ------------------------------------------------------------------ a listing that is gone
async def test_a_listing_the_browser_found_gone_leaves_the_feed_at_once(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    await post_cards(auth_client, headers, [card(9801, 12)])
    opp = await opportunity("9801")

    def ids(feed: dict[str, Any]) -> set[str]:
        return {o["id"] for o in feed["items"]}

    listed = (await auth_client.get(f"{API}/opportunities", params={"page_size": 50})).json()
    assert str(opp.id) in ids(listed)

    r = await auth_client.post(f"{API}/capture/gone", json={"vinted_id": "9801"}, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["known"] is True and out["status"] == "removed" and out["changed"] is True
    assert (await opportunity("9801")).is_active is False
    async with session_scope() as s:
        status = (await s.execute(select(Listing.status).where(Listing.external_id == "9801"))).scalar_one()
    assert status == "removed"
    after = (await auth_client.get(f"{API}/opportunities", params={"page_size": 50})).json()
    assert str(opp.id) not in ids(after), "the deal is still shown as active"

    again = (
        await auth_client.post(f"{API}/capture/gone", json={"vinted_id": "9801"}, headers=headers)
    ).json()
    assert again["changed"] is False
    unknown = (
        await auth_client.post(f"{API}/capture/gone", json={"vinted_id": "123456789"}, headers=headers)
    ).json()
    assert unknown == {"known": False, "vinted_id": "123456789"}
    assert (
        await auth_client.post(f"{API}/capture/gone", json={"vinted_id": "abc"}, headers=headers)
    ).status_code == 422


async def test_a_gone_report_read_before_a_later_capture_is_not_applied(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    await post_cards(auth_client, headers, [card(9802, 12)])
    stale = (datetime.now(UTC) - timedelta(hours=30)).isoformat()  # read before the capture above
    r = await auth_client.post(
        f"{API}/capture/gone", json={"vinted_id": "9802", "observed_at": stale}, headers=headers
    )
    assert r.json()["changed"] is False and (await opportunity("9802")).is_active is True


# ------------------------------------------------------------------ the queue replays a capture with its own time
async def test_a_queued_card_keeps_the_time_the_browser_read_it(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    read = datetime.now(UTC) - timedelta(hours=20)
    await post_cards(auth_client, headers, [card(9901, 12, observed_at=read.isoformat())])
    async with session_scope() as s:
        row = (
            await s.execute(select(Listing.last_verified_at).where(Listing.external_id == "9901"))
        ).scalar_one()
    assert abs((row - read).total_seconds()) < 1, (
        "stamped with the time of arrival instead of the time of reading"
    )
