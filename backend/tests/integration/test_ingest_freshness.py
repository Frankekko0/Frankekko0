"""Freshness, races and useless writes of the ingestion (dup-F5, dup-F7, PX-12, F8 of the ingest wave).

* a capture queued in the browser while the server was away keeps the time it was READ (``observed_at``): its price,
  status and "verified at" are not stamped with the time of arrival, and an older capture never overwrites a newer one;
* two requests that bring the same new listing at the same time are served one after the other (no 500 from the
  second one's images), and a repost that comes before its original in a batch is stored;
* the same page seen again writes nothing it does not need to.
"""

import asyncio
import contextlib
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.db.models import Listing, ListingImage, ListingSnapshot
from app.db.session import session_scope
from app.domain.enums import AcquisitionMode
from app.ingestion import service as ingestion
from app.ingestion.service import IngestionService, observation_time
from tests.conftest import NOW

pytestmark = pytest.mark.usefixtures("clean_db")


async def ingest(
    listings: list[Any], at: datetime = NOW, mode: AcquisitionMode = AcquisitionMode.EXTENSION_ITEM
) -> Any:
    async with session_scope() as s:
        return await IngestionService(s, "vinted", mode).ingest(listings, now=at)


async def stored(external_id: str) -> Listing:
    async with session_scope() as s:
        row = (await s.execute(select(Listing).where(Listing.external_id == external_id))).scalar_one()
        s.expunge(row)
        return row


async def xmin(table: str, where: str, **params: Any) -> list[int]:
    async with session_scope() as s:
        query = text(f"SELECT xmin::text::bigint FROM {table} WHERE {where} ORDER BY id")  # noqa: S608
        rows = await s.execute(query, params)
        return [r[0] for r in rows]


# ------------------------------------------------------------------ observed_at
def test_the_observation_time_is_the_browsers_clamped_to_the_last_two_days() -> None:
    assert observation_time(None, NOW) == NOW  # the source does not say: it arrived when it was read
    assert observation_time(NOW - timedelta(hours=20), NOW) == NOW - timedelta(hours=20)
    assert observation_time(NOW + timedelta(hours=3), NOW) == NOW  # the browser's clock is ahead
    assert observation_time(NOW - timedelta(days=9), NOW) == NOW - timedelta(hours=48)
    naive = (NOW - timedelta(hours=1)).replace(tzinfo=None)
    assert observation_time(naive, NOW) == NOW - timedelta(hours=1)


async def test_a_capture_queued_for_hours_is_stamped_with_the_time_it_was_read(make_listing: Any) -> None:
    read_at = NOW - timedelta(hours=20)
    pl = make_listing(external_id="9101").model_copy(update={"observed_at": read_at})
    res = await ingest([pl])  # arrives now, was read 20 hours ago
    row = await stored("9101")
    assert row.last_verified_at == read_at and row.last_seen_at == read_at and row.first_seen_at == read_at
    async with session_scope() as s:
        snap = (
            await s.execute(
                select(ListingSnapshot.observed_at).where(ListingSnapshot.listing_id == res.new_ids[0])
            )
        ).scalar_one()
    assert snap == read_at

    later = pl.model_copy(update={"observed_at": NOW - timedelta(hours=2), "price": Decimal("19")})
    await ingest([later])
    row = await stored("9101")
    assert row.last_verified_at == NOW - timedelta(hours=2) and row.price == Decimal("19")


async def test_an_older_capture_never_overwrites_a_newer_one(make_listing: Any) -> None:
    pl = make_listing(external_id="9102", price=20)
    await ingest([pl.model_copy(update={"observed_at": NOW - timedelta(hours=1)})])
    newer = await stored("9102")

    stale = pl.model_copy(
        update={"observed_at": NOW - timedelta(hours=5), "price": Decimal("31"), "status": "sold"}
    )
    res = await ingest([stale])  # read five hours ago, delivered after the capture of an hour ago

    row = await stored("9102")
    assert res.out_of_order_ids == [newer.id] and res.price_changes == [] and res.status_changes == []
    assert row.price == Decimal("20") and row.status == "active", "an older price/status won"
    assert row.last_seen_at == newer.last_seen_at and row.last_verified_at == newer.last_verified_at
    assert (
        res.ids_by_external["9102"] == newer.id and newer.id in res.updated_ids
    )  # the listing is still known
    async with session_scope() as s:
        n = (await s.execute(select(func.count()).select_from(ListingSnapshot))).scalar_one()
    assert n == 1, "the stale capture added a history row"


# ------------------------------------------------------------------ races and ordering
async def run_concurrently(listings: list[Any], monkeypatch: pytest.MonkeyPatch, *, lock: bool) -> list[Any]:
    """Two requests with the same new listings, forced to read the database before either has written."""
    barrier = asyncio.Barrier(2)
    real = IngestionService._last_snapshots

    async def meet(self: IngestionService, ids: Any) -> Any:
        out = await real(self, ids)
        # both have now looked and found the listings new (with the lock, the second one is still waiting)
        with contextlib.suppress(TimeoutError, asyncio.BrokenBarrierError):
            await asyncio.wait_for(barrier.wait(), 0.6)
        return out

    monkeypatch.setattr(IngestionService, "_last_snapshots", meet)
    if not lock:

        async def no_lock(self: IngestionService, ids: Any) -> None:
            return None

        monkeypatch.setattr(IngestionService, "_lock_listings", no_lock)
    return await asyncio.gather(ingest(listings), ingest(listings), return_exceptions=True)


@pytest.mark.parametrize("lock", [True, False], ids=["advisory lock", "insert race fallback"])
async def test_two_requests_with_the_same_new_cards_both_succeed(
    make_listing: Any, monkeypatch: pytest.MonkeyPatch, lock: bool
) -> None:
    # Cards of a search page carry no seller: nothing but the listings themselves is shared between the two requests.
    cards = [
        make_listing(external_id=f"92{i:02d}", photos=3).model_copy(update={"seller": None})
        for i in range(12)
    ]
    results = await run_concurrently(cards, monkeypatch, lock=lock)
    assert not [r for r in results if isinstance(r, BaseException)], results  # no foreign key error, no 500
    async with session_scope() as s:
        listings = (await s.execute(select(func.count()).select_from(Listing))).scalar_one()
        images = (await s.execute(select(func.count()).select_from(ListingImage))).scalar_one()
        snaps = (await s.execute(select(func.count()).select_from(ListingSnapshot))).scalar_one()
    assert listings == 12 and images == 36 and snaps == 12
    a, b = results
    assert a.ids_by_external == b.ids_by_external, "both requests name the same listings"
    assert sorted(a.new_ids + b.new_ids) == sorted(a.ids_by_external.values()), (
        "each listing is new exactly once"
    )


async def test_a_repost_that_comes_before_its_original_in_a_batch_is_stored(make_listing: Any) -> None:
    same = dict(title="Giacca invernale uomo taglia M", price=40, seller_id="seller-x", photos=2)
    newer = make_listing(
        external_id="100", published_days_ago=3, **same
    )  # sorts first, points at the older one
    older = make_listing(external_id="200", published_days_ago=7, **same)
    for batch in ([newer, older], [older, newer]):
        async with session_scope() as s:
            await s.execute(text("TRUNCATE listings CASCADE"))
        res = await ingest(batch)
        assert len(res.new_ids) == 2
        n, o = await stored("100"), await stored("200")
        assert n.duplicate_of_id == o.id and o.duplicate_of_id is None
        assert res.duplicates == {n.id: o.id}


# ------------------------------------------------------------------ the same page again
async def test_the_same_page_seen_again_rewrites_nothing(make_listing: Any) -> None:
    pl = make_listing(external_id="9301", photos=3)
    first = await ingest([pl])
    listing_x = await xmin("listings", "external_id = :e", e="9301")
    images_x = await xmin("listing_images", "listing_id = :l", l=first.new_ids[0])

    again = await ingest([pl], at=NOW + timedelta(seconds=30))  # a reload a moment later
    assert again.updated_ids == first.new_ids and again.enriched_ids == []
    assert await xmin("listings", "external_id = :e", e="9301") == listing_x, "the listing row was rewritten"
    assert await xmin("listing_images", "listing_id = :l", l=first.new_ids[0]) == images_x

    later = await ingest([pl], at=NOW + timedelta(minutes=30))
    assert later.enriched_ids == [], "the description and photos are the same: not written again"
    assert await xmin("listing_images", "listing_id = :l", l=first.new_ids[0]) == images_x
    row = await stored("9301")
    assert row.last_seen_at == NOW + timedelta(minutes=30), "but it was seen, and the row says so"

    changed = await ingest(
        [pl.model_copy(update={"description": "Descrizione nuova con più dettagli."})],
        at=NOW + timedelta(hours=1),
    )
    assert changed.enriched_ids == first.new_ids
    assert (await stored("9301")).description.startswith("Descrizione nuova")


async def test_a_changed_price_is_never_swallowed_by_the_quiet_reload(make_listing: Any) -> None:
    pl = make_listing(external_id="9302", price=30)
    await ingest([pl])
    res = await ingest([pl.model_copy(update={"price": Decimal("24")})], at=NOW + timedelta(seconds=20))
    assert len(res.price_changes) == 1 and (await stored("9302")).price == Decimal("24")
    assert timedelta(minutes=5) > ingestion.RESIGHT_DEBOUNCE
