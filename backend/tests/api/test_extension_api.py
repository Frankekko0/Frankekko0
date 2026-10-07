"""Browser extension API: pairing keys, captures, quick evaluations, track, slow refresh."""

import httpx

from tests.api.test_api import API, seed_deal

CARD = {"brand": "Ralph Lauren", "size": "M", "condition": "Ottime condizioni"}


async def _paired(auth_client: httpx.AsyncClient) -> dict[str, str]:
    r = await auth_client.post(f"{API}/extension/keys", json={"name": "Chrome"})
    assert r.status_code == 201, r.text
    key = r.json()["key"]
    assert key.startswith("ff_ext_") and len(key) > 40
    return {"Authorization": f"Bearer {key}"}


async def test_pairing_key_lifecycle(auth_client: httpx.AsyncClient, client: httpx.AsyncClient) -> None:
    headers = await _paired(auth_client)
    keys = (await auth_client.get(f"{API}/extension/keys")).json()
    assert len(keys) == 1 and "key" not in keys[0] and keys[0]["prefix"].startswith("ff_ext_")

    # The key alone authenticates (no session cookie, no CSRF token).
    anon = httpx.AsyncClient(transport=client._transport, base_url="http://testserver")
    ping = await anon.get(f"{API}/extension/ping", headers=headers)
    assert ping.status_code == 200 and ping.json()["ok"] is True
    cfg = await anon.get(f"{API}/extension/parser-config", headers=headers)
    assert cfg.status_code == 200 and cfg.json()["version"] and "patterns" in cfg.json()
    # ...but only for the extension endpoints.
    assert (await anon.get(f"{API}/items", headers=headers)).status_code == 401

    assert (await auth_client.delete(f"{API}/extension/keys/{keys[0]['id']}")).json()["revoked_at"]
    gone = await anon.get(f"{API}/extension/ping", headers=headers)
    assert gone.status_code == 401 and gone.json()["error"]["code"] == "invalid_extension_key"
    bogus = await anon.get(f"{API}/extension/ping", headers={"Authorization": "Bearer ff_ext_nope"})
    assert bogus.status_code == 401
    await anon.aclose()


async def test_cards_seen_while_scrolling_are_stored_and_evaluated(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    cards = [
        {**CARD, "url": "https://www.vinted.it/items/9001-polo", "title": "Polo Ralph Lauren", "price": 9},
        {
            **CARD,
            "url": "https://www.vinted.it/items/9002-polo",
            "title": "Polo Ralph Lauren slim",
            "price": 60,
        },
        # Not Vinted: ignored.
        {**CARD, "url": "https://example.com/items/9003", "title": "Altro sito", "price": 5},
    ]
    r = await auth_client.post(
        f"{API}/capture/cards",
        json={"page_type": "catalog", "items": cards, "extension_version": "1.0.0"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["received"] == 3 and body["stored"] == 2
    evals = {e["vinted_id"]: e for e in body["evaluations"]}
    assert set(evals) == {"9001", "9002"}
    cheap = evals["9001"]
    assert cheap["capture_level"] == "card" and cheap["analysis_depth"] == "quick"
    assert cheap["tracked"] is False and cheap["status"] == "active"
    assert cheap["total_cost"] > 9 and cheap["reason"]
    if cheap["data_quality"] == "insufficient":
        assert cheap["flip_score"] is None and cheap["net_margin"] is None
    else:
        assert cheap["flip_score"] is not None and cheap["net_margin"] is not None
    item = (await auth_client.get(f"{API}/items/9001")).json()
    assert item["item"]["acquisition_mode"] == "extension_card"

    # Seen again later, reserved: same record, status updated, one more snapshot.
    again = await auth_client.post(
        f"{API}/capture/cards",
        json={"page_type": "catalog", "items": [{**cards[0], "status": "reserved"}]},
        headers=headers,
    )
    assert again.json()["evaluations"][0]["status"] == "reserved"
    detail = (await auth_client.get(f"{API}/items/9001")).json()
    assert len(detail["snapshots"]) == 2 and detail["tracking"]["status"] == "reserved"

    looked_up = await auth_client.post(
        f"{API}/capture/evaluations", json={"vinted_ids": ["9002", "424242"]}, headers=headers
    )
    assert [e["vinted_id"] for e in looked_up.json()] == ["9002"]
    sync = (await auth_client.get(f"{API}/acquisition/status")).json()["extension"]
    assert sync["last_sync"] and sync["listings"] == 2


async def test_item_capture_full_analysis_and_track(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    item = {
        **CARD,
        "url": "https://www.vinted.it/items/9100-polo-ralph-lauren",
        "title": "Polo Ralph Lauren Custom Slim Fit",
        "price": 11,
        "description": "Polo originale, etichetta interna presente, misure in foto.",
        "image_urls": ["https://images1.vinted.net/t/1.jpeg", "https://images1.vinted.net/t/2.jpeg"],
        "favourite_count": 12,
        "view_count": 140,
        "seller_key": "h:0123456789abcdef01234567",
        "seller_rating": 4.8,
        "seller_review_count": 40,
        "buyer_protection_fee": 1.25,
    }
    r = await auth_client.post(f"{API}/capture/item", json={"item": item}, headers=headers)
    assert r.status_code == 200, r.text
    ev, analysis = r.json()["evaluation"], r.json()["analysis"]
    assert ev["capture_level"] == "full" and ev["analysis_depth"] == "full"
    assert ev["tracked"] is False  # opening a page is not "track"
    assert analysis["acquisition_mode"] == "extension_item" and analysis["algorithm_version"]
    detail = (await auth_client.get(f"{API}/items/9100")).json()
    assert len(detail["images"]) == 2 and detail["view_count"] == 140
    assert detail["seller"]["review_count"] == 40

    panel = await auth_client.get(f"{API}/capture/items/9100", headers=headers)
    assert panel.status_code == 200, panel.text
    pd = panel.json()
    assert [i["url"] for i in pd["images"]] == item["image_urls"]
    assert pd["evaluation"]["vinted_id"] == "9100" and pd["analysis"]["market"]
    assert (await auth_client.get(f"{API}/capture/items/0000", headers=headers)).status_code == 404

    tr = await auth_client.post(
        f"{API}/capture/track", json={"url": item["url"], "track": True}, headers=headers
    )
    assert tr.json()["tracked"] is True and tr.json()["evaluation"]["tracked"] is True
    assert (await auth_client.get(f"{API}/items/9100")).json()["tracking"]["next_check_at"]

    # A deep analysis on command tracks by default.
    deep = await auth_client.post(
        f"{API}/capture/item",
        json={"item": {**item, "url": "https://www.vinted.it/items/9101-x"}, "mode": "extension_deep"},
        headers=headers,
    )
    assert deep.json()["evaluation"]["tracked"] is True
    # Unknown link: tracking creates a link-only record.
    unknown = await auth_client.post(
        f"{API}/capture/track", json={"url": "https://www.vinted.fr/items/9200-abc"}, headers=headers
    )
    assert unknown.json()["evaluation"]["capture_level"] == "link"


async def test_slow_refresh_queue_and_results(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    for vid in ("9301", "9302"):
        await auth_client.post(
            f"{API}/listings/import/links", json={"text": f"https://www.vinted.it/items/{vid}-a"}
        )
    q = (await auth_client.get(f"{API}/capture/refresh-queue", params={"limit": 5}, headers=headers)).json()
    assert {i["vinted_id"] for i in q["items"]} == {"9301", "9302"}
    # Leased: not handed out twice.
    assert (await auth_client.get(f"{API}/capture/refresh-queue", headers=headers)).json()["items"] == []

    gone = await auth_client.post(
        f"{API}/capture/refresh-result",
        json={"vinted_id": "9301", "outcome": "not_found", "http_status": 404},
        headers=headers,
    )
    assert gone.json()["status"] == "removed"  # never "sold" without evidence
    blocked = await auth_client.post(
        f"{API}/capture/refresh-result",
        json={"vinted_id": "9302", "outcome": "blocked", "http_status": 403},
        headers=headers,
    )
    assert blocked.json()["status"] == "unknown"
    detail = (await auth_client.get(f"{API}/items/9302")).json()
    assert detail["tracking"]["check_failures"] == 1
    assert detail["attempts"][0]["outcome"] == "blocked"


async def test_market_summary_for_the_instant_verdict(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)  # a market of sold and on-sale Ralph Lauren polos
    headers = await _paired(auth_client)
    m = (await auth_client.get(f"{API}/extension/market-cache", headers=headers)).json()
    assert m["version"] and m["costs"]["shipping_in"] > 0
    median, p10, p90, n_sold, p_sale, days = m["segments"]["ralph-lauren|polo-shirts"]
    assert days is None or days > 0
    assert n_sold >= m["min_sold"] and p10 <= median <= p90
    assert p_sale is None or 0 < p_sale < 1
    assert all(seg[3] >= m["min_sold"] for seg in m["segments"].values())  # thin segments left out
    brands = {b[0]: b for b in m["brands"]}
    assert "ralph lauren" in brands["ralph-lauren"][2]
    assert any(c[0] == "polo-shirts" for c in m["categories"])
    # Costs are the user's own: changing them changes the summary.
    prefs = (await auth_client.get(f"{API}/settings/preferences")).json()
    prefs["cost_profile"]["shipping_in"] = 7.5
    assert (await auth_client.put(f"{API}/settings/preferences", json=prefs)).status_code == 200
    again = (await auth_client.get(f"{API}/extension/market-cache", headers=headers)).json()
    assert again["costs"]["shipping_in"] == 7.5 and again["version"] != m["version"]


async def test_vinted_favourite_and_purchase_are_recorded(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    from app.db.session import session_scope
    from app.domain.enums import AcquisitionMode
    from app.ingestion.service import IngestionService
    from tests.conftest import NOW

    async with session_scope() as s:
        res = await IngestionService(s, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest(
            [make_listing(price=20, external_id="5551112223")], now=NOW
        )
    lid = str(res.new_ids[0])
    empty = (await auth_client.get(f"{API}/listings/{lid}/vinted")).json()
    assert empty == {"favourite": None, "checkout_opened": None, "purchased": None}

    on = (
        await auth_client.post(f"{API}/listings/{lid}/vinted", json={"kind": "favourite", "value": True})
    ).json()
    assert on["favourite"]["value"] is True and on["favourite"]["source"] == "click"
    off = (
        await auth_client.post(f"{API}/listings/{lid}/vinted", json={"kind": "favourite", "value": False})
    ).json()
    assert off["favourite"]["value"] is False  # the latest state wins
    started = (
        await auth_client.post(f"{API}/listings/{lid}/vinted", json={"kind": "checkout_opened", "price": 20})
    ).json()
    assert started["checkout_opened"]["price"] == 20 and started["purchased"] is None

    # The extension sees the completed checkout (user confirmed the payment on Vinted).
    headers = await _paired(auth_client)
    body = {
        "vinted_id": "5551112223",
        "kind": "purchased",
        "price": 25.19,
        "source": "checkout",
        "detail": {"item_price": 20},
    }
    done = (await auth_client.post(f"{API}/capture/vinted-actions", json=body, headers=headers)).json()
    assert done["purchased"]["price"] == 25.19
    again = await auth_client.post(f"{API}/capture/vinted-actions", json=body, headers=headers)
    assert again.status_code == 201
    flips = (await auth_client.get(f"{API}/flips")).json()
    assert len(flips) == 1  # recorded once, however many times the success page is seen
    assert flips[0]["purchase_price"] == 20 and abs(flips[0]["total_cost"] - 25.19) < 0.01  # the total paid
    unknown = await auth_client.post(
        f"{API}/capture/vinted-actions", json={**body, "vinted_id": "999"}, headers=headers
    )
    assert unknown.status_code == 404
