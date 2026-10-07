"""GET /pricing/evidence and POST /pricing/evidence/refresh (web app session)."""

from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.api.v1 import portfolio, pricing
from app.db.models import SoldSale
from app.db.session import session_scope
from app.market.state import set_state
from app.pricing.evidence import GATE_KEY
from tests.api.test_api import API, seed_deal
from tests.api.test_extension_api import _paired

STATUS = {
    "provider": "serper",
    "enabled": True,
    "key_configured": True,
    "cost_per_query_usd": 0.001,
    "free_queries": 2500,
    "month_used": 12,
    "month_budget": 900,
    "today_used": 2,
    "daily_max": 60,
    "refresh_days": 30,
    "models_cached": 40,
    "models_due": 3,
    "models_pending": 5,
    "prices": {"new": 30, "asking": 80, "sold": 12},
    "rejected": {"kids": 3, "replica": 2, "lot": 1, "other_model": 7},
    "last_run": "2026-10-07T12:00:00+00:00",
    "last_error": None,
    "expected_monthly_queries": 120,
}


async def test_evidence_status(auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def status(session: Any) -> dict[str, Any]:
        return dict(STATUS)

    monkeypatch.setattr(pricing, "external_status", status)
    r = await auth_client.get(f"{API}/pricing/evidence")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["external"] == STATUS
    sold = body["sold_sales"]
    assert sold["total"] == 0 and set(sold["by_source"]) == {
        "own_sale",
        "own_purchase",
        "vinted_sold",
        "external_sold",
    }
    assert body["negotiation"]["discount"] is None and body["negotiation"]["note"]
    # Not measured yet: extra sources in use with their low weights, and it says so.
    acc = body["accuracy"]
    assert acc["measured_at"] is None and acc["with_external"] is None
    assert acc["external_in_use"] is True and acc["own_purchases_in_use"] is True
    assert "misurabile" in acc["note"]

    metrics = {"mae": 7.5, "mape": 0.21, "n": 40}
    async with session_scope() as s:
        await set_state(
            s,
            GATE_KEY,
            {
                "measured_at": "2026-10-07T03:00:00+00:00",
                "without_external": metrics,
                "with_external": {**metrics, "mae": 8.1},
                "use_external": False,
                "use_own_purchases": True,
                "use_new_cap": False,
                "n_subjects": 40,
                "note": "I dati esterni peggiorano l'errore: non usati.",
            },
        )
    acc = (await auth_client.get(f"{API}/pricing/evidence")).json()["accuracy"]
    assert acc == {
        "measured_at": "2026-10-07T03:00:00+00:00",
        "without_external": metrics,
        "with_external": {**metrics, "mae": 8.1},
        "external_in_use": False,
        "own_purchases_in_use": True,
        "note": "I dati esterni peggiorano l'errore: non usati.",
    }


async def test_evidence_survives_external_failure(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(session: Any) -> dict[str, Any]:
        raise NotImplementedError

    monkeypatch.setattr(pricing, "external_status", broken)
    r = await auth_client.get(f"{API}/pricing/evidence")
    assert r.status_code == 200, r.text
    external = r.json()["external"]
    assert set(external) == set(STATUS)
    assert external["enabled"] is False and external["last_error"] == pricing.EXTERNAL_UNAVAILABLE
    assert r.json()["sold_sales"]["total"] == 0


async def test_refresh_queues_both_jobs_rate_limited(
    auth_client: httpx.AsyncClient, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str | None]] = []

    async def fake_enqueue(function: str, *args: Any, job_id: str | None = None, **kw: Any) -> bool:
        calls.append((function, job_id))
        return True

    monkeypatch.setattr(pricing, "enqueue", fake_enqueue)
    limited = {"x-test-rate-limit": "1"}
    r = await auth_client.post(f"{API}/pricing/evidence/refresh", headers=limited)
    assert r.status_code == 200 and r.json() == {"queued": True}
    assert calls == [
        ("sync_price_evidence_task", "price-evidence:manual"),
        ("refresh_external_prices_task", "external-prices:manual"),
    ]
    assert (await auth_client.post(f"{API}/pricing/evidence/refresh", headers=limited)).status_code == 200
    third = await auth_client.post(f"{API}/pricing/evidence/refresh", headers=limited)
    assert third.status_code == 429

    # Session only: an extension key does not reach it.
    headers = await _paired(auth_client)
    anon = httpx.AsyncClient(transport=client._transport, base_url="http://testserver")
    assert (await anon.get(f"{API}/pricing/evidence", headers=headers)).status_code == 401
    assert (await anon.post(f"{API}/pricing/evidence/refresh")).status_code == 401
    await anon.aclose()


async def test_refresh_reports_a_queue_outage(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(*args: Any, **kw: Any) -> bool:
        raise ConnectionError("redis down")

    monkeypatch.setattr(pricing, "enqueue", down)
    r = await auth_client.post(f"{API}/pricing/evidence/refresh")
    assert r.status_code == 503 and r.json()["error"]["code"] == "queue_unavailable"


async def test_analysis_detail_carries_provenance(auth_client: httpx.AsyncClient, make_listing: Any) -> None:
    opp_id = await seed_deal(make_listing)
    detail = (await auth_client.get(f"{API}/opportunities/{opp_id}")).json()
    prov = detail["provenance"]
    assert {
        "expected_price",
        "price_range",
        "days_to_sell",
        "sale_probability",
        "real_sales",
        "external",
    } <= set(prov)
    assert prov["expected_price"]["label"]
    assert prov["expected_price"]["basis"] in ("sold", "mixed", "asking", "prior", "none")
    # The item page (and the extension's live panel) shows it too.
    headers = await _paired(auth_client)
    captured = await auth_client.post(
        f"{API}/capture/item",
        json={
            "item": {
                "url": "https://www.vinted.it/items/7001-polo",
                "title": "Polo Ralph Lauren",
                "brand": "Ralph Lauren",
                "size": "M",
                "condition": "Ottime condizioni",
                "price": 14,
            }
        },
        headers=headers,
    )
    assert captured.status_code == 200, captured.text
    item = (await auth_client.get(f"{API}/items/7001")).json()
    assert item["analysis"]["provenance"]["expected_price"]["label"]


async def _own_counts(client: httpx.AsyncClient) -> tuple[int, int]:
    by_source = (await client.get(f"{API}/pricing/evidence")).json()["sold_sales"]["by_source"]
    return by_source["own_purchase"], by_source["own_sale"]


async def test_own_records_join_the_concluded_sales_right_away(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def none_status(session: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(pricing, "external_status", none_status)
    bought = await auth_client.post(
        f"{API}/purchases",
        json={
            "title": "Nike Air Max 90 bianche",
            "brand": "nike",
            "size": "42",
            "condition": "very_good",
            "purchase_price": 30,
            "purchase_date": "2026-09-01",
        },
    )
    assert bought.status_code == 201, bought.text
    purchase_id = bought.json()["purchase_id"]
    assert await _own_counts(auth_client) == (1, 0)

    sold = await auth_client.post(
        f"{API}/sales", json={"purchase_id": purchase_id, "sale_price": 55, "sale_date": "2026-09-20"}
    )
    assert sold.status_code == 201, sold.text
    assert await _own_counts(auth_client) == (1, 1)
    async with session_scope() as s:
        rows = (await s.execute(select(SoldSale.source, SoldSale.price, SoldSale.model_name))).all()
    assert {(r.source, float(r.price)) for r in rows} == {("own_purchase", 30.0), ("own_sale", 55.0)}
    assert {r.model_name for r in rows} == {"Air Max 90"}

    # Edited: the title is the record's title too.
    edited = await auth_client.patch(f"{API}/purchases/{purchase_id}", json={"title": "Air Max 90 nike"})
    assert edited.status_code == 200
    async with session_scope() as s:
        titles = set((await s.execute(select(SoldSale.title))).scalars())
    assert titles == {"Air Max 90 nike"}

    sale_id = sold.json()["sale"]["id"]
    assert (await auth_client.delete(f"{API}/sales/{sale_id}")).status_code == 200
    assert await _own_counts(auth_client) == (1, 0)
    assert (await auth_client.delete(f"{API}/purchases/{purchase_id}")).status_code == 200
    assert await _own_counts(auth_client) == (0, 0)


async def test_own_records_sync_failure_is_never_shown(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: Any, **kw: Any) -> dict[str, int]:
        raise RuntimeError("evidence store down")

    monkeypatch.setattr(portfolio, "sync_own_records", broken)
    r = await auth_client.post(
        f"{API}/purchases",
        json={"title": "Polo Ralph Lauren", "purchase_price": 9, "purchase_date": "2026-09-01"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Polo Ralph Lauren"


async def test_purchase_from_vinted_joins_the_concluded_sales(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def none_status(session: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(pricing, "external_status", none_status)
    headers = await _paired(auth_client)
    card = {
        "url": "https://www.vinted.it/items/7002-air-max",
        "title": "Nike Air Max 90",
        "brand": "Nike",
        "size": "42",
        "condition": "Ottime condizioni",
        "price": 35,
    }
    assert (
        await auth_client.post(f"{API}/capture/item", json={"item": card}, headers=headers)
    ).status_code == 200
    r = await auth_client.post(
        f"{API}/capture/vinted-actions",
        json={"vinted_id": "7002", "kind": "purchased", "price": 39.45, "source": "checkout"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert await _own_counts(auth_client) == (1, 0)
