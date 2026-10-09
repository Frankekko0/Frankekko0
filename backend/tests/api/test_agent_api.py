"""AI spend and the latest review, through the API."""

from decimal import Decimal as D
from typing import Any

import httpx

from app.ai.budget import AiBudget
from app.core.config import get_settings
from tests.api.test_api import API
from tests.integration.test_agent import run_review, seed


async def test_usage_requires_a_login_and_reports_the_caps(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{API}/ai/usage")).status_code == 401


async def test_usage_shows_spend_against_the_caps(auth_client: httpx.AsyncClient) -> None:
    s = get_settings()
    await AiBudget(s).record(
        purpose="deal_analysis",
        model="claude-sonnet-5-5",
        tier="strong",
        input_tokens=10_000,
        output_tokens=2_000,
    )
    body = (await auth_client.get(f"{API}/ai/usage")).json()
    assert D(body["day_spent"]) == D("0.06") and D(body["day_cap"]) == s.ai_daily_budget_usd
    assert D(body["day_left"]) == s.ai_daily_budget_usd - D("0.06")
    assert body["calls_today"] == 1 and body["exhausted"] is False
    assert body["by_purpose"] == {"deal_analysis": "0.060000"}
    assert body["prices_are_assumptions"] is True and body["ai_enabled"] is False  # no key in tests
    assert body["models"]["cheap"] == s.ai_model_cheap


async def test_the_latest_review_lists_the_choices_with_their_listings(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    assert (await auth_client.get(f"{API}/ai/review/latest")).json() == {"run": None, "picks": []}
    ids = await seed(make_listing)
    run = await run_review(None)  # the rules answer when there is no model
    assert run is not None
    body = (await auth_client.get(f"{API}/ai/review/latest")).json()
    assert body["run"]["provider"] == "rules" and body["run"]["fallback"] is True
    by_id = {p["opportunity_id"]: p for p in body["picks"]}
    assert by_id[ids["strong"]]["action"] == "buy" and by_id[ids["strong"]]["verdict_now"] == "STRONG_BUY"
    assert by_id[ids["strong"]]["title"] and by_id[ids["strong"]]["url"].startswith("https://")
    assert by_id[ids["negotiate"]]["threshold_price"] is not None
    assert ids["dear"] not in by_id
