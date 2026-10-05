"""HTTP API tests (FastAPI app over httpx ASGI transport, real PostgreSQL and Redis)."""

from collections.abc import Callable
from datetime import date, timedelta

import httpx
import pytest

from app.ingestion.service import IngestionService
from app.marketplace.base import ProviderListing
from app.opportunities.pipeline import AnalysisPipeline
from tests.conftest import NOW, register
from tests.integration.test_pipeline import build_market

API = "/api/v1"


async def seed_deal(make_listing: Callable[..., ProviderListing], price: float = 12) -> str:
    from app.db.session import session_scope

    async with session_scope() as s:
        await build_market(s, make_listing)
        res = await IngestionService(s, "test").ingest(
            [make_listing(price=price, published_days_ago=0.05)], now=NOW
        )
        outcome = await AnalysisPipeline(s).analyze_listing(res.new_ids[0], now=NOW)
        assert outcome is not None
        return str(outcome.opportunity_id)


# --------------------------------------------------------------------------------- auth
async def test_register_login_me_logout(client: httpx.AsyncClient) -> None:
    data = await register(client, "Mario@Example.com")
    assert data["user"]["email"] == "mario@example.com"
    assert "ff_session" in client.cookies
    me = await client.get(f"{API}/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "mario@example.com"

    dup = await client.post(
        f"{API}/auth/register", json={"email": "mario@example.com", "password": "another-pass-1"}
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "email_taken"

    out = await client.post(f"{API}/auth/logout")
    assert out.status_code == 200
    client.cookies.clear()
    assert (await client.get(f"{API}/auth/me")).status_code == 401

    bad = await client.post(
        f"{API}/auth/login", json={"email": "mario@example.com", "password": "wrong-password"}
    )
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "invalid_credentials"
    good = await client.post(
        f"{API}/auth/login?include_token=true",
        json={"email": "mario@example.com", "password": "s3cure-pass!"},
    )
    assert good.status_code == 200
    token = good.json()["access_token"]
    client.cookies.clear()
    bearer = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert bearer.status_code == 200


async def test_weak_password_is_rejected_with_readable_error(client: httpx.AsyncClient) -> None:
    r = await client.post(f"{API}/auth/register", json={"email": "x@y.it", "password": "1234567890"})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error" and body["message"] == "Alcuni campi non sono validi."
    assert "Traceback" not in r.text


async def test_csrf_is_required_for_cookie_sessions(auth_client: httpx.AsyncClient) -> None:
    payload = {"name": "Test", "brand_slugs": ["nike"]}
    token = auth_client.headers.pop("X-CSRF-Token")
    r = await auth_client.post(f"{API}/watchlists", json=payload)
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = await auth_client.post(f"{API}/watchlists", json=payload, headers={"X-CSRF-Token": token})
    assert r.status_code == 201


async def test_login_rate_limit(client: httpx.AsyncClient) -> None:
    statuses = []
    for _ in range(12):
        r = await client.post(
            f"{API}/auth/login",
            json={"email": "nobody@x.it", "password": "whatever-123"},
            headers={"x-test-rate-limit": "1"},
        )
        statuses.append(r.status_code)
    assert 429 in statuses
    assert statuses.index(429) >= 10


async def test_security_headers_and_error_shape(client: httpx.AsyncClient) -> None:
    r = await client.get(f"{API}/opportunities/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 401
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "x-request-id" in r.headers
    assert set(r.json()["error"]) >= {"code", "message"}
    health = await client.get(f"{API}/health/ready")
    assert health.json()["checks"] == {"database": "ok", "redis": "ok"}


# ---------------------------------------------------------------------- opportunities
async def test_feed_detail_states_and_profit(auth_client: httpx.AsyncClient, make_listing) -> None:
    opp_id = await seed_deal(make_listing)
    feed = await auth_client.get(f"{API}/opportunities", params={"preset": "best_deals"})
    assert feed.status_code == 200
    items = feed.json()["items"]
    assert items and items[0]["id"] == opp_id
    card = items[0]
    for key in (
        "image_url",
        "listing_price",
        "total_acquisition_cost",
        "expected_sale_price",
        "expected_profit",
        "expected_roi",
        "flip_score",
        "confidence_score",
        "risk_score",
        "demand_level",
        "velocity_bucket",
        "published_at",
        "url",
        "top_reasons",
    ):
        assert key in card
    assert card["expected_profit"] > 8

    detail = (await auth_client.get(f"{API}/opportunities/{opp_id}")).json()
    assert len(detail["scenarios"]) == 3
    assert detail["comparables"] and detail["market"]["histogram"]
    assert detail["smart_buy"]["max_buy_price"] > card["listing_price"]
    assert detail["explanation"] and detail["ai_analysis"]["verdict"] in ("BUY", "CONSIDER")
    assert detail["listing"]["seller"]["review_count"] > 0

    # State: ignored items disappear from the default feed but stay reachable.
    r = await auth_client.put(f"{API}/opportunities/{opp_id}/state", json={"state": "ignored"})
    assert r.status_code == 200
    ids = [i["id"] for i in (await auth_client.get(f"{API}/opportunities")).json()["items"]]
    assert opp_id not in ids
    saved = (await auth_client.get(f"{API}/opportunities", params={"state": "ignored"})).json()["items"]
    assert [i["id"] for i in saved] == [opp_id]
    await auth_client.delete(f"{API}/opportunities/{opp_id}/state")

    calc = await auth_client.post(f"{API}/profit/calculate", json={"purchase_price": 20, "sale_price": 45})
    body = calc.json()
    assert calc.status_code == 200
    assert body["total_acquisition_cost"] == pytest.approx(20 + 0.70 + 1.00 + 3.49)
    assert body["net_profit"] == pytest.approx(45 - 0.5 - 25.19)


async def test_user_cost_profile_changes_profit_filters(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    base = (await auth_client.get(f"{API}/opportunities", params={"min_profit": 8})).json()
    assert base["total"] == 1
    prefs = (await auth_client.get(f"{API}/settings/preferences")).json()
    prefs["cost_profile"]["advertising"] = 15  # expensive promotions kill the margin
    r = await auth_client.put(f"{API}/settings/preferences", json=prefs)
    assert r.status_code == 200
    after = (await auth_client.get(f"{API}/opportunities", params={"min_profit": 8})).json()
    assert after["total"] == 0


async def test_search_and_parse(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    parsed = (
        await auth_client.get(f"{API}/search/parse", params={"q": "polo ralph lauren sotto 20 euro"})
    ).json()
    assert parsed["filters"]["brands"] == ["ralph-lauren"] and parsed["filters"]["max_price"] == 20
    res = (await auth_client.get(f"{API}/search", params={"q": "polo ralph lauren sotto 20 euro"})).json()
    assert res["results"]["total"] == 1


async def test_listing_import_and_adhoc_analysis(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    payload = {
        "url": "https://www.vinted.it/items/987654321-polo",
        "title": "Polo Ralph Lauren Custom Slim Fit blu",
        "price": 10,
        "brand": "Ralph Lauren",
        "size": "M",
        "condition": "Ottime condizioni",
    }
    imported = await auth_client.post(f"{API}/listings/import", json=payload)
    assert imported.status_code == 201, imported.text
    assert imported.json()["flip_score"] > 60
    adhoc = await auth_client.post(f"{API}/analyze", json={**payload, "price": 30})
    assert adhoc.status_code == 200
    body = adhoc.json()
    assert body["fair_market_value"] > 20 and isinstance(body["max_buy_price"], float)
    assert body["flip_score"] < 50


# --------------------------------------------------------------------------- portfolio
async def test_purchase_sale_and_portfolio(auth_client: httpx.AsyncClient) -> None:
    p = await auth_client.post(
        f"{API}/purchases",
        json={
            "title": "Felpa RL",
            "brand": "ralph-lauren",
            "category": "hoodies",
            "purchase_price": 20,
            "shipping_cost": 3,
            "buyer_protection_fee": 2,
            "purchase_date": "2026-09-01",
        },
    )
    assert p.status_code == 201, p.text
    pid = p.json()["purchase_id"]
    assert p.json()["total_cost"] == 25
    s = await auth_client.post(
        f"{API}/sales",
        json={"purchase_id": pid, "sale_price": 45, "selling_fees": 4, "sale_date": "2026-09-08"},
    )
    flip = s.json()
    assert s.status_code == 201
    assert flip["profit"] == 16 and flip["roi"] == pytest.approx(0.64) and flip["holding_days"] == 7
    again = await auth_client.post(
        f"{API}/sales", json={"purchase_id": pid, "sale_price": 1, "sale_date": "2026-09-09"}
    )
    assert again.status_code == 409
    summary = (await auth_client.get(f"{API}/analytics/portfolio")).json()
    assert summary["profit"] == 16 and summary["win_rate"] == 1 and summary["average_holding_days"] == 7
    future = (date.today() + timedelta(days=3)).isoformat()
    bad = await auth_client.post(
        f"{API}/purchases", json={"title": "x", "purchase_price": 5, "purchase_date": future}
    )
    assert bad.status_code == 422


# ---------------------------------------------------------------- watchlists & settings
async def test_watchlist_crud_and_matches(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    r = await auth_client.post(
        f"{API}/watchlists",
        json={"name": "RL cheap", "brand_slugs": ["ralph-lauren"], "max_buy_price": 25, "min_profit": 5},
    )
    assert r.status_code == 201
    wid = r.json()["id"]
    listed = (await auth_client.get(f"{API}/watchlists")).json()
    assert listed[0]["match_count"] == 1
    matches = (await auth_client.get(f"{API}/watchlists/{wid}/matches")).json()
    assert matches["total"] == 1
    upd = await auth_client.patch(f"{API}/watchlists/{wid}", json={"name": "RL", "brand_slugs": ["nike"]})
    assert upd.json()["brand_slugs"] == ["nike"]
    assert (await auth_client.delete(f"{API}/watchlists/{wid}")).status_code == 200
    assert (await auth_client.get(f"{API}/watchlists/{wid}/matches")).status_code == 404


async def test_notification_settings_mask_secrets(auth_client: httpx.AsyncClient) -> None:
    ns = (await auth_client.get(f"{API}/settings/notifications")).json()
    ns["discord_enabled"] = True
    ns["discord_webhook_url"] = "https://discord.com/api/webhooks/123/very-secret-token"
    r = await auth_client.put(f"{API}/settings/notifications", json=ns)
    assert r.status_code == 200
    assert "very-secret" not in r.text and r.json()["discord_webhook_url"].startswith("•")
    # Sending back the masked value keeps the stored secret.
    again = await auth_client.put(f"{API}/settings/notifications", json=r.json())
    assert again.status_code == 200
    bad = await auth_client.put(
        f"{API}/settings/notifications", json={**ns, "discord_webhook_url": "https://evil.example/x"}
    )
    assert bad.status_code == 422


async def test_alerts_endpoints(auth_client: httpx.AsyncClient) -> None:
    unread = (await auth_client.get(f"{API}/alerts/unread-count")).json()
    assert unread == {"unread": 0, "latest_high_priority": None}
    assert (await auth_client.post(f"{API}/alerts/read-all")).status_code == 200
    assert (await auth_client.get(f"{API}/alerts")).json()["total"] == 0
