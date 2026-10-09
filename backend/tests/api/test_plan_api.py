"""Phase 7: the purchase plan (budget -> exact knapsack), verdict views and the user's own weights."""

from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.db.session import session_scope
from app.scoring.flip import DEFAULT_WEIGHTS, normalize_weights, rescore
from tests.api.test_api import API
from tests.api.test_decision_api import analyse


async def set_budget(client: httpx.AsyncClient, **kw: Any) -> None:
    prefs = (await client.get(f"{API}/settings/preferences")).json()
    r = await client.put(f"{API}/settings/preferences", json={**prefs, **kw})
    assert r.status_code == 200, r.text


async def force(opps: list[Any], rows: list[tuple[str, float, float, int]]) -> None:
    """Give the analysed listings known verdicts, costs and risk-adjusted profits."""
    async with session_scope() as s:
        for o, (verdict, cost, value, risk) in zip(opps, rows, strict=True):
            await s.execute(
                text(
                    "UPDATE opportunities SET decision_verdict=:v, total_acquisition_cost=:c, "
                    "risk_adjusted_profit=:p, risk_score=:r WHERE id=:id"
                ),
                {"v": verdict, "c": cost, "p": value, "r": risk, "id": o.id},
            )


async def test_without_a_budget_the_plan_says_so(auth_client: httpx.AsyncClient) -> None:
    r = (await auth_client.get(f"{API}/plan")).json()
    assert r["budget"] is None and r["selected"] == [] and "budget" in r["reason"]
    assert r["invested"] == 0 and r["realized_profit"] == 0 and r["potential_profit"] == 0


async def test_the_plan_is_the_best_combination_not_the_best_ratio(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    opps = await analyse(make_listing, None, None, None, None)
    await force(
        opps,
        [
            ("BUY", 9, 10, 10),
            ("BUY", 8, 8.5, 10),
            ("STRONG_BUY", 8, 8.5, 10),
            ("WATCHLIST", 5, 40, 10),  # not a purchase: never competes
        ],
    )
    await set_budget(auth_client, total_budget=16)
    r = (await auth_client.get(f"{API}/plan")).json()
    assert r["reason"] is None and r["budget"] == 16
    chosen = {p["opportunity_id"] for p in r["selected"]}
    assert chosen == {str(opps[1].id), str(opps[2].id)}  # 8 + 8 = 17 beats the 9 a greedy ratio picks
    assert r["total_cost"] == 16 and r["left"] == 0 and r["potential_profit"] == 17.0
    assert r["considered"] == 3  # the watchlist item was never a candidate
    assert all(p["last_seen_at"] for p in r["selected"])

    # A cap per item and a cap on risk are respected.
    await force(
        opps, [("BUY", 9, 10, 90), ("BUY", 8, 8.5, 10), ("STRONG_BUY", 8, 8.5, 10), ("PASS", 1, 1, 0)]
    )
    await set_budget(auth_client, total_budget=16, max_risk_score=50)
    r = (await auth_client.get(f"{API}/plan")).json()
    assert {p["opportunity_id"] for p in r["selected"]} == {str(opps[1].id), str(opps[2].id)}
    assert any("rischio" in n["why"] for n in r["not_selected"])


async def test_what_you_hold_comes_out_of_the_budget_and_realized_is_kept_apart(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    opps = await analyse(make_listing, None, None)
    await force(opps, [("BUY", 8, 8.5, 10), ("BUY", 8, 8.5, 10)])
    await set_budget(auth_client, total_budget=20, max_owned_items=3)
    held = await auth_client.post(
        f"{API}/purchases",
        json={
            "title": "Felpa RL",
            "purchase_price": 10,
            "shipping_cost": 0,
            "buyer_protection_fee": 0,
            "purchase_date": "2026-09-01",
        },
    )
    assert held.status_code == 201
    sold = await auth_client.post(
        f"{API}/purchases",
        json={
            "title": "Polo",
            "purchase_price": 10,
            "shipping_cost": 0,
            "buyer_protection_fee": 0,
            "purchase_date": "2026-09-01",
        },
    )
    await auth_client.post(
        f"{API}/sales",
        json={"purchase_id": sold.json()["purchase_id"], "sale_price": 30, "sale_date": "2026-09-08"},
    )
    r = (await auth_client.get(f"{API}/plan")).json()
    assert r["invested"] == 10 and r["owned_items"] == 1 and r["available"] == 10  # 20 - 10 held
    assert r["realized_profit"] == 20  # closed sale only
    assert len(r["selected"]) == 1 and r["total_cost"] == 8
    assert r["potential_profit"] == 8.5  # the forecast is a separate number


async def test_a_full_inventory_or_a_spent_budget_gives_a_reason(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    opps = await analyse(make_listing, None)
    await force(opps, [("BUY", 8, 8.5, 10)])
    await set_budget(auth_client, total_budget=10, max_owned_items=1)
    await auth_client.post(
        f"{API}/purchases",
        json={
            "title": "Felpa RL",
            "purchase_price": 10,
            "shipping_cost": 0,
            "buyer_protection_fee": 0,
            "purchase_date": "2026-09-01",
        },
    )
    r = (await auth_client.get(f"{API}/plan")).json()
    assert r["selected"] == [] and r["available"] == 0 and "impegnato" in r["reason"]


# ------------------------------------------------------------------ verdict views
async def test_the_feed_filters_by_verdict(auth_client: httpx.AsyncClient, make_listing: Any) -> None:
    opps = await analyse(make_listing, None, None, None)
    await force(opps, [("STRONG_BUY", 8, 9, 10), ("WATCHLIST", 8, 9, 10), ("PASS", 8, 9, 10)])

    def ids(r: httpx.Response) -> set[str]:
        return {i["id"] for i in r.json()["items"]}

    base = f"{API}/opportunities?include_insufficient=true"
    strong = await auth_client.get(f"{base}&preset=strong_buy")
    assert ids(strong) == {str(opps[0].id)}
    watch = await auth_client.get(f"{base}&preset=to_watch")
    assert ids(watch) == {str(opps[1].id)}
    both = await auth_client.get(f"{base}&verdicts=WATCHLIST&verdicts=PASS")
    assert ids(both) == {str(opps[1].id), str(opps[2].id)}
    assert (await auth_client.get(f"{base}&verdicts=MAYBE")).status_code == 422


# ------------------------------------------------------------------ the user's own weights
STORED = {
    "components": {
        "profit": {"score": 80},
        "roi": {"score": 60},
        "price_vs_market": {"score": 70},
        "demand": {"score": 50},
        "condition": {"score": 40},
        "risk": {"score": 90},
        "info": {"score": 30},
        "sale_time": {"score": 20},
    },
    "cap": None,
}


def test_default_weights_give_the_stored_score_and_other_weights_move_it() -> None:
    base = rescore(STORED, None)
    assert base == rescore(STORED, dict(DEFAULT_WEIGHTS))
    risk_heavy = rescore(STORED, {**DEFAULT_WEIGHTS, "risk": 400})  # risk is 90: pulls the score up
    demand_heavy = rescore(STORED, {**DEFAULT_WEIGHTS, "sale_time": 400})  # 20: pulls it down
    assert base is not None and risk_heavy is not None and demand_heavy is not None
    assert demand_heavy < base < risk_heavy
    assert abs(sum(normalize_weights(None).values()) - 1) < 1e-9


def test_the_cap_and_a_broken_breakdown_are_respected() -> None:
    assert rescore({**STORED, "cap": {"max": 30, "reason": "x"}}, None) == 30
    assert rescore({"components": {"profit": {"score": 1}}}, None) is None
    assert rescore(None, None) is None
    assert rescore({}, {"profit": 10}) is None


async def test_the_detail_shows_the_score_with_your_weights_only_when_they_differ(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    opps = await analyse(make_listing, None)
    oid = opps[0].id
    d = (await auth_client.get(f"{API}/opportunities/{oid}")).json()["score"]
    assert d["weighted_flip_score"] is None  # default weights: nothing to add
    await set_budget(auth_client, score_weights={**DEFAULT_WEIGHTS, "risk": 60, "profit": 5})
    d = (await auth_client.get(f"{API}/opportunities/{oid}")).json()["score"]
    assert isinstance(d["weighted_flip_score"], int) and d["weighted_flip_score"] != d["flip_score"]
    assert d["flip_score"] == opps[0].flip_score  # the shared score and the verdict do not move


@pytest.mark.parametrize("bad", [{"total_budget": 0}, {"total_budget": -5}, {"max_owned_items": 0}])
async def test_budget_rules_are_validated(auth_client: httpx.AsyncClient, bad: dict[str, Any]) -> None:
    prefs = (await auth_client.get(f"{API}/settings/preferences")).json()
    assert (await auth_client.put(f"{API}/settings/preferences", json={**prefs, **bad})).status_code == 422
