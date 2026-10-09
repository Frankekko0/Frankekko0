"""Case G: a rare item with no reliable comparables gets no invented price anywhere.

The whole chain is checked: import -> analysis -> feed -> detail -> alerts -> the extension's quick
verdict. "Insufficient evidence" is a result of its own, never a number made up to fill the gap.
"""

import httpx

from tests.api.test_api import API, seed_deal
from tests.api.test_extension_api import _paired

RARE = {
    "url": "https://www.vinted.it/items/777001-tabi",
    "title": "Maison Margiela Tabi stivali prototipo pezzo unico",
    "price": 480,
    "brand": "Maison Margiela",
    "size": "40",
    "condition": "Ottime condizioni",
}
NO_VALUE = (
    "fair_market_value",
    "expected_sale_price",
    "expected_profit",
    "expected_roi",
    "risk_adjusted_profit",
)


async def rare_card(client: httpx.AsyncClient) -> dict:
    """Import the rare listing, then read it back from the feed that shows insufficient items."""
    r = await client.post(f"{API}/listings/import", json=RARE)
    assert r.status_code == 201, r.text
    feed = (await client.get(f"{API}/opportunities", params={"include_insufficient": "true"})).json()
    return next(c for c in feed["items"] if c["url"] == RARE["url"])


async def test_a_rare_item_is_declared_insufficient_and_gets_no_price(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    await seed_deal(make_listing)  # a healthy market exists, but for other products
    card = await rare_card(auth_client)
    assert card["data_quality"] == "insufficient"
    assert "comparabil" in card["insufficient_reason"]
    for field in NO_VALUE:
        assert card[field] is None, f"{field} was invented: {card[field]}"
    assert card["verdict"] != "BUY" and card["recommended_action"] in ("watch", "skip")
    assert card["headline"].startswith("Dati insufficienti")


async def test_the_default_feed_hides_it(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    await rare_card(auth_client)
    shown = (await auth_client.get(f"{API}/opportunities")).json()["items"]
    assert RARE["url"] not in [c["url"] for c in shown]
    assert any(c["data_quality"] != "insufficient" for c in shown)  # the real deal is still there


async def test_the_detail_offers_no_purchase_advice(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    card = await rare_card(auth_client)
    detail = (await auth_client.get(f"{API}/opportunities/{card['id']}")).json()
    assert detail["scenarios"] == []
    assert detail["smart_buy"]["max_buy_price"] is None and detail["smart_buy"]["suggested_offer"] is None
    assert detail["provenance"]["expected_price"]["basis"] == "none"
    assert detail["provenance"]["expected_price"]["value"] is None
    assert detail["provenance"]["price_range"]["low"] is None


async def test_no_alert_is_raised_on_a_guess(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    await rare_card(auth_client)
    alerts = (await auth_client.get(f"{API}/alerts")).json()["items"]
    assert RARE["url"] not in [a.get("url") for a in alerts]
    assert all(a.get("type") != "ultra_deal" for a in alerts)


async def test_the_extension_quick_verdict_shows_no_numbers(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    card = {
        "url": RARE["url"],
        "title": RARE["title"],
        "price": RARE["price"],
        "brand": RARE["brand"],
        "size": RARE["size"],
        "condition": RARE["condition"],
    }
    r = await auth_client.post(
        f"{API}/capture/cards", json={"page_type": "catalog", "items": [card]}, headers=headers
    )
    assert r.status_code == 200, r.text
    ev = r.json()["evaluations"][0]
    assert ev["data_quality"] == "insufficient"
    for field in (
        "flip_score",
        "net_margin",
        "roi",
        "resale_expected",
        "days_to_sell",
        "risk_adjusted_profit",
    ):
        assert ev[field] is None, f"{field} was shown for an item with no evidence: {ev[field]}"
    assert ev["reason"]  # it says why, instead of staying silent
