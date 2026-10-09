"""Phase 2 through the API: stored analyses are listed and readable; re-analysing the same
listing does not duplicate; a price change adds a history point and an analysis."""

import httpx

from tests.api.test_api import API, seed_deal

BASE = {"brand": "Ralph Lauren", "size": "M", "condition": "Ottime condizioni"}
PAYLOAD = {
    **BASE,
    "url": "https://www.vinted.it/items/741-polo",
    "title": "Polo Ralph Lauren Custom Slim Fit",
    "price": 11,
}


async def test_analyses_are_listed_and_readable_and_not_duplicated(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    await seed_deal(make_listing)
    first = (await auth_client.post(f"{API}/analyze", json=PAYLOAD)).json()
    # The same listing analysed again and again: still one analysis.
    for _ in range(3):
        again = await auth_client.post(f"{API}/analyze", json=PAYLOAD)
        assert again.json()["listing_id"] == first["listing_id"]
    rows = (await auth_client.get(f"{API}/items/741/analyses")).json()
    assert len(rows) == 1 and rows[0]["trigger"] == "new" and rows[0]["is_current"] is True

    # A new price: a new analysis and a new price point; the first stays as it was.
    await auth_client.post(f"{API}/analyze", json={**PAYLOAD, "price": 9})
    rows = (await auth_client.get(f"{API}/items/741/analyses")).json()
    assert [r["trigger"] for r in rows] == ["price_change", "new"]
    assert [r["is_current"] for r in rows] == [True, False]
    assert [r["price"] for r in rows] == ["9", "11"]
    detail = (await auth_client.get(f"{API}/items/741")).json()
    assert [s["price"] for s in detail["snapshots"]] == [11, 9]

    full = (await auth_client.get(f"{API}/items/741/analyses/{rows[1]['id']}")).json()
    assert full["vinted_id"] == "741" and full["url"].endswith("/items/741-polo")
    assert full["schema_version"] == 1 and full["source"] and full["created_at"]
    assert full["is_current"] is False
    assert set(full) >= {"product", "visual", "economic", "market", "decision"}
    assert full["economic"]["listing_price"] == "11"
    # Another listing's analysis is not reachable through this one.
    other = (
        await auth_client.post(f"{API}/analyze", json={**PAYLOAD, "url": "https://www.vinted.it/items/742-x"})
    ).json()
    miss = await auth_client.get(f"{API}/items/{other['listing_id']}/analyses/{rows[1]['id']}")
    assert miss.status_code == 404
