"""Case K of the brief: a purchased item produces a listing, a markdown plan, offer evaluations and the
right ledger entries, and the realised profit equals the real figures entered. Plus the negotiation assistant."""

from typing import Any

import httpx

from app.negotiation.assistant import pressure_phrases
from tests.api.test_api import API
from tests.api.test_decision_api import analyse


async def buy(client: httpx.AsyncClient, opp_id: Any, price: float = 12) -> str:
    r = await client.post(
        f"{API}/purchases",
        json={
            "title": "Polo Ralph Lauren Custom Slim Fit blu navy M",
            "opportunity_id": str(opp_id),
            "purchase_price": price,
            "shipping_cost": 3.0,
            "buyer_protection_fee": 1.0,
            "purchase_date": "2026-09-01",
            "condition": "very_good",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["purchase_id"]


async def test_case_k_the_whole_selling_cycle_with_real_numbers(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    (opp,) = await analyse(make_listing, None)
    pid = await buy(auth_client, opp.id)  # total cost 12 + 3 + 1 = 16

    inv = (await auth_client.get(f"{API}/selling/inventory")).json()
    assert inv["by_stage"] == {"to_list": 1} and inv["items"][0]["stage_label"] == "Da pubblicare"

    # 1. the listing draft and the price plan
    plan = (await auth_client.get(f"{API}/selling/inventory/{pid}/plan")).json()
    assert (
        plan["draft"]["title"].startswith("Ralph Lauren")
        and "ottime condizioni" in plan["draft"]["description"]
    )
    assert plan["draft"]["to_confirm"] and plan["draft"]["not_claimed"]  # unknowns are asked, not invented
    p = plan["plan"]
    assert p is not None and p["floor"] <= p["best_price"] < p["start_price"]
    assert p["markdowns"][0]["price"] == p["start_price"] and p["markdowns"][-1]["price"] == p["floor"]
    assert p["basis"] in ("measured", "prior") and p["note"]
    assert plan["reprice"] is None  # not listed yet

    # 2. an invalid jump is refused, then it is listed at the start price
    jump = await auth_client.patch(f"{API}/selling/inventory/{pid}", json={"stage": "sold"})
    assert jump.status_code == 400 and jump.json()["error"]["code"] == "invalid_stage_move"
    listed = await auth_client.patch(
        f"{API}/selling/inventory/{pid}",
        json={
            "stage": "listed",
            "listed_price": p["start_price"],
            "reason": "prezzo di partenza",
            "views": 3,
        },
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["stage"] == "listed" and body["initial_price"] == p["start_price"]
    assert body["price_history"][0]["reason"] == "prezzo di partenza" and body["listed_at"]
    assert (await auth_client.get(f"{API}/flips")).json()[0]["status"] == "listed"  # the old view follows

    # 3. offers: a good one is accepted, a low one declined, the middle countered above the floor
    def offer(v: float) -> Any:
        return auth_client.post(
            f"{API}/selling/inventory/{pid}/offer", json={"offer": v, "buyer_name": "Anna"}
        )

    good = (await offer(p["start_price"])).json()
    assert good["action"] == "accept" and "Anna" in good["reply"]
    bad = (await offer(max(0.5, p["floor"] * 0.5))).json()
    assert bad["action"] == "decline" and bad["counter_price"] is None
    mid = (await offer(round(p["floor"] * 0.95, 2))).json()
    assert mid["action"] == "counter" and mid["counter_price"] >= p["floor"]
    assert all(pressure_phrases(x["reply"]) == [] for x in (good, bad, mid))

    # 4. today's repricing advice exists for a listed item
    again = (await auth_client.get(f"{API}/selling/inventory/{pid}/plan")).json()
    assert again["reprice"]["action"] in ("hold", "lower", "improve_listing", "raise")

    # 5. sold: the realised profit is exactly what was entered, in the sale, the ledger and the summary
    sale = await auth_client.post(
        f"{API}/sales",
        json={
            "purchase_id": pid,
            "sale_price": 30,
            "selling_fees": 0,
            "shipping_cost": 3.5,
            "packaging_cost": 0.5,
            "sale_date": "2026-09-10",
        },
    )
    assert sale.status_code == 201, sale.text
    assert sale.json()["profit"] == 30 - 3.5 - 0.5 - 16  # 10.00
    assert (await auth_client.get(f"{API}/selling/inventory")).json()["by_stage"] == {"sold": 1}
    await auth_client.post(
        f"{API}/accounting/expenses",
        json={"spent_on": "2026-09-02", "kind": "packaging", "amount": 2.5, "note": "buste"},
    )
    s = (await auth_client.get(f"{API}/accounting/summary?start=2026-09-01&end=2026-09-30")).json()
    assert s["revenue"] == 30 and s["cost_of_goods_sold"] == 16 and s["selling_costs"] == 4
    assert s["realized_profit"] == 10 and s["realized_profit_after_expenses"] == 7.5
    assert s["cash_in"] == 30 and s["cash_out"] == 16 + 4 + 2.5 and s["stock_items"] == 0
    ledger = (await auth_client.get(f"{API}/accounting/ledger?start=2026-09-01&end=2026-09-30")).json()
    assert [r["kind"] for r in ledger] == ["purchase", "expense", "sale", "shipping"]
    assert round(sum(r["amount"] for r in ledger), 2) == s["cash_flow"] == 7.5  # 30 in, 16 + 4 + 2.5 out
    csv = await auth_client.get(f"{API}/accounting/ledger?format=csv&start=2026-09-01&end=2026-09-30")
    assert (
        csv.headers["content-type"].startswith("text/csv")
        and "importo_eur" in csv.text
        and "30,00" in csv.text
    )

    # 6. forecast against reality, kept apart
    out = (await auth_client.get(f"{API}/learning/outcomes")).json()
    assert out["sales"] == 1 and out["overall"]["actual_profit"] == 10.0
    assert out["overall"]["predicted_profit"] is not None and "previsto" in out["note"]


async def test_unsold_items_and_returns_move_through_the_stages(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    (opp,) = await analyse(make_listing, None)
    pid = await buy(auth_client, opp.id)
    for stage in ("listed", "unsold", "listed", "returned", "to_list"):
        r = await auth_client.patch(f"{API}/selling/inventory/{pid}", json={"stage": stage})
        assert r.status_code == 200, (stage, r.text)
    assert (await auth_client.get(f"{API}/selling/inventory")).json()["by_stage"] == {"to_list": 1}
    other = await auth_client.patch(
        f"{API}/selling/inventory/00000000-0000-0000-0000-000000000000", json={"stage": "listed"}
    )
    assert other.status_code == 404
    # an offer on an item that is not listed makes no sense
    off = await auth_client.post(f"{API}/selling/inventory/{pid}/offer", json={"offer": 20})
    assert off.status_code == 400 and off.json()["error"]["code"] == "not_listed"


async def test_deleting_a_sale_returns_the_item_and_removes_its_outcome(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    (opp,) = await analyse(make_listing, None)
    pid = await buy(auth_client, opp.id)
    await auth_client.patch(f"{API}/selling/inventory/{pid}", json={"stage": "listed", "listed_price": 30})
    sale = await auth_client.post(
        f"{API}/sales", json={"purchase_id": pid, "sale_price": 30, "sale_date": "2026-09-10"}
    )
    sale_id = sale.json()["sale"]["id"]
    assert (await auth_client.get(f"{API}/learning/outcomes")).json()["sales"] == 1
    assert (await auth_client.delete(f"{API}/sales/{sale_id}")).status_code == 200
    assert (await auth_client.get(f"{API}/learning/outcomes")).json()["sales"] == 0
    assert (await auth_client.get(f"{API}/selling/inventory")).json()["by_stage"] == {"listed": 1}


async def test_the_negotiation_assistant_for_a_listing(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    (opp,) = await analyse(make_listing, None, price=24)
    n = (await auth_client.get(f"{API}/opportunities/{opp.id}/negotiation")).json()
    assert n["asked"] == 24.0 and n["profit_table"] and n["messages"]
    prices = [r["price"] for r in n["profit_table"]]
    assert prices == sorted(prices, reverse=True) and 24.0 in prices
    assert 0 <= n["willingness"] <= 100
    for text in n["messages"].values():
        assert pressure_phrases(text) == [] and "grazie" in text.lower()
    if n["max_acceptable"] is not None and n["asked"] > n["max_acceptable"]:
        assert (
            n["discount_needed"] == round(n["asked"] - n["max_acceptable"], 2)
            and "first_offer" in n["messages"]
        )
    assert (
        await auth_client.get(f"{API}/opportunities/00000000-0000-0000-0000-000000000000/negotiation")
    ).status_code == 404


async def test_the_accountant_report_uses_only_the_users_own_thresholds(
    auth_client: httpx.AsyncClient,
) -> None:
    r = (await auth_client.get(f"{API}/accounting/accountant?year=2026")).json()
    assert r["holder_status"] == "private" and r["thresholds"] == [] and r["year_revenue"] == 0
    assert any("Nessuna aliquota" in n for n in r["notes"])
    bad = await auth_client.get(f"{API}/accounting/summary?start=2026-10-01&end=2026-09-01")
    assert bad.status_code == 400
    assert (
        await auth_client.post(
            f"{API}/accounting/expenses", json={"spent_on": "2026-09-02", "kind": "x", "amount": 1}
        )
    ).status_code == 422
