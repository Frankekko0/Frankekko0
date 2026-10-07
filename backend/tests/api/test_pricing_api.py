"""GET /pricing/evidence and POST /pricing/evidence/refresh (web app session)."""

from typing import Any

import httpx
import pytest

from app.api.v1 import pricing
from app.db.session import session_scope
from app.market.state import set_state
from app.pricing.evidence import GATE_KEY
from tests.api.test_api import API
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
