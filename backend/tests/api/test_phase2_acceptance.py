"""Phase 2 acceptance, through the extension's own endpoints:

1. analysing the same listing again creates no duplicates;
2. a price change produces a new history row and a new analysis;
3. a listing that sells appears in the sold-prices table;
4. no record without a URL and a date.
"""

import httpx
from sqlalchemy import func, select, text

from app.db.models import Analysis, Listing, ListingSnapshot, SoldSale
from app.db.session import session_scope
from tests.api.test_api import API, seed_deal
from tests.api.test_extension_api import CARD, _paired

URL = "https://www.vinted.it/items/7771-polo"
ITEM = {**CARD, "url": URL, "title": "Polo Ralph Lauren Custom Slim Fit", "price": 14, "status": "active"}


async def _count(model) -> int:
    async with session_scope() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def _capture(client, headers, item, track=None):
    r = await client.post(
        f"{API}/capture/item",
        json={
            "item": item,
            "mode": "extension_item",
            "track": track,
            "extension_version": "1.2.0",
            "parser_version": "2026.10.1",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r


async def test_phase_2_acceptance(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    base = (await _count(Listing), await _count(Analysis), await _count(ListingSnapshot))

    # 1. The same listing, analysed again and again: one listing, one analysis, one observation.
    for _ in range(4):
        await _capture(auth_client, headers, ITEM)
    now = (await _count(Listing), await _count(Analysis), await _count(ListingSnapshot))
    assert tuple(n - b for n, b in zip(now, base, strict=True)) == (1, 1, 1)

    # 2. A new price: one more history row, one more analysis, the old ones untouched.
    await _capture(auth_client, headers, {**ITEM, "price": 11})
    after = (await _count(Listing), await _count(Analysis), await _count(ListingSnapshot))
    assert tuple(n - b for n, b in zip(after, now, strict=True)) == (0, 1, 1)
    detail = (await auth_client.get(f"{API}/items/7771")).json()
    assert [s["price"] for s in detail["snapshots"]] == [14, 11]
    analyses = (await auth_client.get(f"{API}/items/7771/analyses")).json()
    assert [a["trigger"] for a in analyses] == ["price_change", "new"]
    assert [a["price"] for a in analyses] == ["11", "14"]
    assert detail["item"]["record_level"] == "analizzato"

    # 3. It sells: it shows up in the sold-prices table, as an asking price (not a price paid).
    before_sales = await _count(SoldSale)
    await _capture(auth_client, headers, {**ITEM, "price": 11, "status": "sold"})
    assert await _count(SoldSale) == before_sales + 1
    async with session_scope() as s:
        sale = (
            await s.execute(
                select(SoldSale)
                .join(Listing, Listing.id == SoldSale.listing_id)
                .where(Listing.external_id == "7771")
            )
        ).scalar_one()
    assert sale.price_kind == "last_seen" and float(sale.asking_price) == 11.0 and sale.realized_price is None
    assert sale.window_start is not None and sale.sold_at is not None
    table = (await auth_client.get(f"{API}/pricing/sold-prices")).json()["models"]
    assert sum(r["samples"]["total"] for r in table) >= 1
    # A sold listing keeps all its photos and its history.
    detail = (await auth_client.get(f"{API}/items/7771")).json()
    assert detail["item"]["status"] == "sold" and len(detail["snapshots"]) >= 3

    # 4. No record without a URL and a date, anywhere.
    async with session_scope() as s:
        for sql in (
            "SELECT count(*) FROM listings WHERE url IS NULL OR btrim(url) = '' OR first_seen_at IS NULL",
            "SELECT count(*) FROM listing_snapshots WHERE observed_at IS NULL",
            "SELECT count(*) FROM analyses WHERE url IS NULL OR btrim(url) = '' OR created_at IS NULL OR (provider = 'vinted' AND vinted_id IS NULL)",
            "SELECT count(*) FROM listing_images WHERE url IS NULL OR btrim(url) = '' OR first_seen_at IS NULL",
            "SELECT count(*) FROM sold_sales WHERE sold_at IS NULL",
        ):
            assert (await s.execute(text(sql))).scalar_one() == 0, sql
    # Every observation says which software read it.
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(ListingSnapshot.extension_version, ListingSnapshot.parser_version)
                .join(Listing)
                .where(Listing.external_id == "7771")
            )
        ).all()
    assert rows and all(r == ("1.2.0", "2026.10.1") for r in rows)
