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


async def test_closed_registration_lets_only_the_owner_sign_up(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "allow_registration", False)
    assert (await client.get(f"{API}/auth/config")).json() == {"registration_enabled": True}  # no account yet
    await register(client, "owner@example.com")
    assert (await client.get(f"{API}/auth/config")).json() == {"registration_enabled": False}
    r = await client.post(
        f"{API}/auth/register", json={"email": "other@example.com", "password": "Another-pass-2026!"}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "registration_disabled"
    assert (await client.post(f"{API}/auth/demo")).status_code == 404  # no demo sign-in at all


def test_production_refuses_dev_secrets() -> None:
    from app.core.config import Settings

    base = {
        "environment": "production",
        "cookie_secure": True,
        "jwt_secret": "x" * 40,
        "database_url": "postgresql+asyncpg://flipfinder:s3cret-from-env@db:5432/flipfinder",
    }
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings(**{**base, "jwt_secret": "dev-only-change-me-dev-only-change-me"}).validate_for_production()
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings(
            **{**base, "database_url": "postgresql+asyncpg://flipfinder:flipfinder@db/flipfinder"}
        ).validate_for_production()
    Settings(**base).validate_for_production()


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


async def test_batch_import_of_a_search_page_ranks_listings(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    await seed_deal(make_listing)
    base = {"brand": "Ralph Lauren", "size": "M", "condition": "Ottime condizioni"}
    page = [
        {
            **base,
            "url": "https://www.vinted.it/items/111-polo",
            "title": "Polo Ralph Lauren Custom Slim Fit blu",
            "price": 30,
        },
        {
            **base,
            "url": "https://www.vinted.it/items/222-polo",
            "title": "Polo Ralph Lauren Custom Slim Fit rossa",
            "price": 9,
        },
        {
            **base,
            "url": "https://www.vinted.it/items/333-polo",
            "title": "Polo Ralph Lauren Custom Slim Fit verde",
            "price": 16,
        },
        # The same item twice on one page (promoted + organic) counts once.
        {
            **base,
            "url": "https://www.vinted.it/items/222-polo",
            "title": "Polo Ralph Lauren Custom Slim Fit rossa",
            "price": 9,
        },
    ]
    r = await auth_client.post(
        f"{API}/listings/import/batch", json={"items": page, "source": "vinted_search"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["received"], body["unique"], body["imported"], body["analyzed"]) == (4, 3, 3, 3)
    assert [c["listing_price"] for c in body["items"]] == [9, 16, 30]
    scores = [c["personal_flip_score"] or c["flip_score"] for c in body["items"]]
    assert scores == sorted(scores, reverse=True)

    # Re-importing the same search later updates known listings and spots price drops.
    page[0]["price"] = 20
    again = await auth_client.post(f"{API}/listings/import/batch", json={"items": page[:3]})
    assert again.status_code == 201, again.text
    assert (again.json()["imported"], again.json()["updated"], again.json()["price_drops"]) == (0, 3, 1)
    feed = await auth_client.get(f"{API}/opportunities", params={"page_size": 50})
    assert {"111", "222", "333"} <= {c["url"].split("/items/")[1].split("-")[0] for c in feed.json()["items"]}

    too_many = await auth_client.post(f"{API}/listings/import/batch", json={"items": [page[0]] * 201})
    assert too_many.status_code == 422
    assert "Traceback" not in too_many.text


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


async def test_feed_ranks_by_risk_adjusted_profit_and_detail_explains_it(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    opp_id = await seed_deal(make_listing)
    feed = (await auth_client.get(f"{API}/opportunities", params={"page_size": 50})).json()["items"]
    raps = [i["risk_adjusted_profit"] for i in feed]
    known = [v for v in raps if v is not None]
    assert known == sorted(known, reverse=True)  # default order: risk-adjusted profit
    assert raps[: len(known)] == known  # items without probabilities come last
    card = next(i for i in feed if i["id"] == opp_id)
    assert card["sale_probability"] is not None and 0 < card["authenticity_probability"] <= 0.97
    expected = card["expected_profit"] * card["sale_probability"] * card["authenticity_probability"]
    assert card["risk_adjusted_profit"] == pytest.approx(expected, abs=0.02)

    d = (await auth_client.get(f"{API}/opportunities/{opp_id}")).json()["insights"]
    assert len(d["reason"]) == 3 and {p["key"] for p in d["pillars"]} == {
        "margin",
        "demand",
        "risk",
        "seller",
    }
    assert d["net_margin"] == pytest.approx(card["expected_profit"])
    assert d["resale"]["low"] <= d["resale"]["probable"] <= d["resale"]["high"]
    assert d["authenticity"]["verdict"] == "not_verifiable"  # photos not analysed: never "authentic"

    acc = (await auth_client.get(f"{API}/analytics/accuracy")).json()
    assert acc["active"] is False and acc["metrics"] is None  # nothing measured yet: no numbers shown


async def test_error_log_keeps_redacted_warnings(
    auth_client: httpx.AsyncClient, tmp_path, monkeypatch
) -> None:
    import logging

    from app.core.config import get_settings
    from app.core.logging import configure_logging, get_logger

    assert (await auth_client.get(f"{API}/system/errors")).json() == {"enabled": False, "items": []}
    monkeypatch.setattr(get_settings(), "error_log_dir", str(tmp_path))
    configure_logging("INFO", True, str(tmp_path), "api")
    try:
        log = get_logger("test")
        log.info("all.good")
        log.error("feed.failed", api_key="sk-secret-value", status=502)
        for h in logging.getLogger().handlers:
            h.flush()
        items = (await auth_client.get(f"{API}/system/errors")).json()["items"]
    finally:
        configure_logging("INFO", True)
    assert [i["event"] for i in items] == ["feed.failed"]  # info is not an error
    assert items[0]["process"] == "api" and items[0]["detail"]["status"] == 502
    assert "sk-secret-value" not in str(items)
