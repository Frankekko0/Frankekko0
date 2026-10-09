"""Archive API: filters, CSV export, item tracking page, track/untrack, no volatile analyses."""

import csv
import io

import httpx

from tests.api.test_api import API, seed_deal

BASE = {"brand": "Ralph Lauren", "size": "M", "condition": "Ottime condizioni"}


async def test_quick_check_is_saved_and_dedups_by_vinted_id(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    await seed_deal(make_listing)
    payload = {
        **BASE,
        "url": "https://www.vinted.it/items/321-polo",
        "title": "Polo Ralph Lauren Custom Slim Fit",
        "price": 11,
    }
    r = await auth_client.post(f"{API}/analyze", json=payload)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["listing_id"] and first["opportunity_id"]
    # Same item from another Vinted domain, later and cheaper: same record, one more snapshot.
    again = await auth_client.post(
        f"{API}/analyze", json={**payload, "url": "https://www.vinted.fr/items/321-polo", "price": 9}
    )
    assert again.json()["listing_id"] == first["listing_id"]
    detail = (await auth_client.get(f"{API}/items/321")).json()  # by Vinted ID
    assert detail["item"]["id"] == first["listing_id"]
    assert detail["item"]["vinted_id"] == "321" and detail["item"]["acquisition_mode"] == "manual_form"
    assert [s["price"] for s in detail["snapshots"]] == [11, 9]
    # Saved, but tracking (periodic checks) is the user's choice.
    assert detail["tracking"]["tracked"] is False and detail["tracking"]["next_check_at"] is None
    assert (await auth_client.post(f"{API}/items/321/track")).json()["tracked"] is True
    detail = (await auth_client.get(f"{API}/items/321")).json()
    assert detail["tracking"]["tracked"] is True and detail["tracking"]["next_check_at"]
    assert "extension_item" in detail["tracking"]["refresh_modes"]  # updated when you open it
    a = detail["analysis"]
    assert a["algorithm_version"] and a["analysis_depth"] == "full" and a["acquisition_mode"] == "manual_form"
    assert a["economics"]["total_acquisition_cost"] > 9


async def test_archive_filters_and_csv_export(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    page = [
        {
            **BASE,
            "url": "https://www.vinted.it/items/501-a",
            "title": '=HYPERLINK("http://x") Polo Ralph Lauren',
            "price": 10,
        },
        {
            **BASE,
            "url": "https://www.vinted.it/items/502-b",
            "title": "Polo Ralph Lauren Big Pony",
            "price": 30,
        },
    ]
    r = await auth_client.post(
        f"{API}/listings/import/batch", json={"items": page, "source": "vinted_search"}
    )
    assert r.status_code == 201, r.text

    res = (await auth_client.get(f"{API}/items", params={"mode": "batch_import"})).json()
    assert res["total"] == 2
    assert {i["capture_level"] for i in res["items"]} == {"card"}
    assert {i["tracked"] for i in res["items"]} == {False}  # whole pages are market data
    by_score = (
        await auth_client.get(f"{API}/items", params={"mode": "batch_import", "sort": "score"})
    ).json()
    scores = [i["flip_score"] for i in by_score["items"]]
    assert scores == sorted(scores, reverse=True)
    assert (await auth_client.get(f"{API}/items", params={"q": "502"})).json()["total"] == 1
    assert (await auth_client.get(f"{API}/items", params={"status": "sold", "mode": "batch_import"})).json()[
        "total"
    ] == 0
    hi = (
        await auth_client.get(f"{API}/items", params={"mode": "batch_import", "min_score": 101})
    ).status_code
    assert hi == 422

    exp = await auth_client.get(
        f"{API}/items/export.csv", params={"mode": "batch_import", "delimiter": "semicolon"}
    )
    assert exp.status_code == 200
    assert exp.headers["content-type"].startswith("text/csv")
    assert "attachment" in exp.headers["content-disposition"]
    text = exp.text.lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0][:3] == ["ID interno", "ID Vinted", "URL"]
    assert len(rows) == 3
    titles = [r[3] for r in rows[1:]]
    assert any(t.startswith("'=HYPERLINK") for t in titles)  # formula neutralized
    assert {r[12] for r in rows[1:]} == {"batch_import"}


async def test_track_and_untrack_keep_the_history(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    r = await auth_client.post(
        f"{API}/listings/import/batch",
        json={
            "items": [
                {
                    **BASE,
                    "url": "https://www.vinted.it/items/777-c",
                    "title": "Polo Ralph Lauren",
                    "price": 15,
                }
            ]
        },
    )
    lid = r.json()["items"][0]["listing_id"]
    t = await auth_client.post(f"{API}/items/{lid}/track")
    assert t.status_code == 200 and t.json()["tracked"] is True and t.json()["next_check_at"]
    saved = (await auth_client.get(f"{API}/items", params={"tracked": "true"})).json()
    assert [i["id"] for i in saved["items"]] == [lid]
    u = await auth_client.delete(f"{API}/items/{lid}/track")
    assert u.json()["tracked"] is False
    detail = (await auth_client.get(f"{API}/items/{lid}")).json()
    assert detail["tracking"]["tracked"] is False and detail["tracking"]["next_check_at"] is None
    assert len(detail["snapshots"]) == 1 and detail["analysis"]["analysis_depth"] == "quick"
    missing = await auth_client.get(f"{API}/items/does-not-exist")
    assert missing.status_code == 404 and "Traceback" not in missing.text
