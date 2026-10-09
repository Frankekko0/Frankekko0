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


async def test_sold_prices_endpoint_and_csv(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    r = await auth_client.get(f"{API}/pricing/sold-prices")
    assert r.status_code == 200 and "models" in r.json()
    rows = r.json()["models"]
    assert rows, "seed_deal records sold listings"
    first = rows[0]
    assert set(first) >= {"samples", "realized", "asking", "reported", "days_to_sell", "evidence"}
    # Seeded sales are sold listings: last asking prices, not prices paid.
    assert first["asking"] is not None and first["realized"] is None
    csv_response = await auth_client.get(f"{API}/pricing/sold-prices/export.csv?delimiter=semicolon")
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "Ultimo prezzo richiesto: mediana" in csv_response.text
    filtered = await auth_client.get(f"{API}/pricing/sold-prices?q=zzz-no-such-model")
    assert filtered.json() == {"models": []}


async def test_observation_and_analysis_csv_exports(auth_client: httpx.AsyncClient, make_listing) -> None:
    import csv
    import io

    await seed_deal(make_listing)
    payload = {
        **PAYLOAD,
        "url": "https://www.vinted.it/items/751-polo",
        "title": '=HYPERLINK("x") Polo Ralph Lauren',
    }
    await auth_client.post(f"{API}/analyze", json=payload)
    await auth_client.post(f"{API}/analyze", json={**payload, "price": 8})

    def rows(text: str) -> list[dict[str, str]]:
        return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))

    obs = await auth_client.get(f"{API}/items/export-observations.csv?ref=751")
    assert obs.status_code == 200 and obs.headers["content-type"].startswith("text/csv")
    o = rows(obs.text)
    assert [r["Prezzo"] for r in o] == ["11.00", "8.00"] or [r["Prezzo"] for r in o] == ["11", "8"]
    assert all(r["ID Vinted"] == "751" and r["URL originale"].endswith("/items/751-polo") for r in o)
    assert all(r["ID interno"] and r["Osservato il"] and r["Fonte"] for r in o)
    assert [r["Motivo della riga"] for r in o] == ["first", "price"]
    assert o[0]["Titolo"].startswith("'=")  # third-party text cannot run as a formula

    ana = await auth_client.get(f"{API}/items/export-analyses.csv?ref=751&delimiter=semicolon")
    a = list(csv.DictReader(io.StringIO(ana.text.lstrip("﻿")), delimiter=";"))
    assert [r["Motivo"] for r in a] == ["new", "price_change"]
    assert [r["Corrente"] for r in a] == ["no", "sì"]
    assert all(r["ID analisi"] and r["Versione algoritmo"] and r["Versione schema"] == "1" for r in a)

    # Everything, and a date range that excludes it.
    assert len(rows((await auth_client.get(f"{API}/items/export-observations.csv")).text)) >= 2
    none = await auth_client.get(f"{API}/items/export-analyses.csv?date_to=2000-01-01T00:00:00Z")
    assert rows(none.text) == []
    assert (await auth_client.get(f"{API}/items/export-analyses.csv?ref=nope")).status_code == 404
