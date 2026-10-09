"""Case N of the brief on real records: a discarded item that then sells well is a recorded false negative, a shift in
prices or brands raises the drift alarm, and what cannot be measured says so. Plus the experiment registry."""

import uuid
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select, text, update

from app.db.models import Experiment, Listing, Opportunity
from app.db.session import session_scope
from app.ingestion.service import IngestionService
from app.intelligence import learning_report as lr
from tests.api.test_api import API, NOW
from tests.api.test_decision_api import analyse


async def user_id(client: httpx.AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get(f"{API}/auth/me")).json()["id"])


async def test_case_n_a_discarded_item_that_sold_fast_is_a_false_negative_with_its_veto(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    opps = await analyse(make_listing, None, None)
    keep, miss = opps
    async with session_scope() as s:
        for o, verdict in ((keep, "PASS"), (miss, "WATCHLIST")):
            await s.execute(
                text(
                    "UPDATE opportunities SET decision_verdict=:v, expected_profit=14, analyzed_at=:t WHERE id=:i"
                ),
                {"v": verdict, "t": NOW, "i": o.id},
            )
        # the WATCHLIST one sold two days later at the analysed price; the PASS one is still on sale
        row = (await s.execute(select(Opportunity).where(Opportunity.id == miss.id))).scalar_one()
        await s.execute(
            update(Listing)
            .where(Listing.id == row.listing_id)
            .values(status="sold", sold_at=NOW + timedelta(days=2), last_active_price=row.listing_price)
        )
        await s.execute(
            text(
                "UPDATE opportunities SET decision = jsonb_set(coalesce(decision, CAST('{}' AS jsonb)), '{vetoes}', CAST(:v AS jsonb)) WHERE id = :i"
            ),
            {"v": '[{"code": "authenticity_unproven", "binding": true}]', "i": miss.id},
        )
    async with session_scope() as s:
        rep = await lr.false_negatives(s, min_profit=10.0, since=NOW - timedelta(days=1))
    assert rep["measurable"] and rep["rejected"] == 2 and rep["sold_quickly"] == 1
    assert rep["false_negatives"] == [str(miss.id)] and rep["by_veto"] == {"authenticity_unproven": 1}
    assert rep["rate"] == 1.0 and "scartato" in rep["definition"]
    # nothing sold yet: not measurable, and it says so
    async with session_scope() as s:
        await s.execute(update(Listing).values(status="active", sold_at=None))
        again = await lr.false_negatives(s, min_profit=10.0, since=NOW - timedelta(days=1))
    assert again["measurable"] is False and "non sono misurabili" in again["note"]


async def test_case_n_a_shift_in_prices_and_brands_raises_the_drift_alarm(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    async with session_scope() as s:
        svc = IngestionService(s, "test")
        old = [
            make_listing(
                price=28 + (i % 7), brand="Ralph Lauren", published_days_ago=40, now=NOW - timedelta(days=40)
            )
            for i in range(40)
        ]
        new = [
            make_listing(
                price=66 + (i % 9), brand="Carhartt", published_days_ago=2, now=NOW - timedelta(days=2)
            )
            for i in range(40)
        ]
        await svc.ingest(old, now=NOW - timedelta(days=40))
        await svc.ingest(new, now=NOW - timedelta(days=2))
    async with session_scope() as s:
        rep = await lr.drift_report(s, NOW)
    assert rep["measurable"] and rep["baseline_n"] == 40 and rep["recent_n"] == 40
    kinds = {a["metric"]: a for a in rep["alarms"]}
    assert kinds["price"]["level"] == "alarm" and kinds["brand_mix"]["level"] == "alarm"
    assert "ricalibrazione controllata" in rep["action"]
    # a stable market raises none
    async with session_scope() as s:
        await s.execute(text("DELETE FROM listings"))
        svc = IngestionService(s, "test")
        await svc.ingest(
            [
                make_listing(price=28 + (i % 7), published_days_ago=40, now=NOW - timedelta(days=40))
                for i in range(40)
            ],
            now=NOW - timedelta(days=40),
        )
        await svc.ingest(
            [
                make_listing(price=28 + (i % 7), published_days_ago=2, now=NOW - timedelta(days=2))
                for i in range(40)
            ],
            now=NOW - timedelta(days=2),
        )
    async with session_scope() as s:
        calm = await lr.drift_report(s, NOW)
    assert calm["alarms"] == [] and "Nessuna deriva" in calm["action"]


async def test_without_real_outcomes_nothing_is_claimed(auth_client: httpx.AsyncClient) -> None:
    rep = (await auth_client.get(f"{API}/learning/report")).json()
    assert rep["closed_sales"] == 0
    assert rep["calibration"]["measurable"] is False and rep["calibration"]["status"] == "not_enough_data"
    assert rep["baseline"]["measurable"] is False and "senza esiti reali" in rep["baseline"]["note"]
    assert rep["counterfactual"]["measurable"] is False and rep["drift"]["measurable"] is False
    assert "Nessuna affermazione di superiorità" in rep["note"]


async def test_the_experiment_registry_remembers_what_was_tried(auth_client: httpx.AsyncClient) -> None:
    first = (await auth_client.get(f"{API}/learning/report?register=true")).json()
    assert first["already_rejected_with_same_data"] == []
    exps = (await auth_client.get(f"{API}/learning/experiments")).json()
    assert {e["name"] for e in exps} == {"ai_vs_baseline", "probability_calibration", "false_negative_rate"}
    assert all(e["status"] == "inconclusive" and e["kind"] == "evaluation" for e in exps)
    # a hypothesis rejected on this data is not repeated silently
    uid = await user_id(auth_client)
    async with session_scope() as s:
        await s.execute(
            update(Experiment).where(Experiment.name == "ai_vs_baseline").values(status="rejected")
        )
    second = (await auth_client.get(f"{API}/learning/report?register=true")).json()
    assert second["already_rejected_with_same_data"] == ["ai_vs_baseline"]
    assert lr.fingerprint("a", {"x": 1}) == lr.fingerprint("a", {"x": 1}) != lr.fingerprint("a", {"x": 2})
    assert uid


async def test_stale_listed_items_are_offered_for_liquidation_with_the_capital_they_free(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    (opp,) = await analyse(make_listing, None)
    p = await auth_client.post(
        f"{API}/purchases",
        json={
            "title": "Felpa ferma",
            "opportunity_id": str(opp.id),
            "purchase_price": 20,
            "shipping_cost": 0,
            "buyer_protection_fee": 0,
            "purchase_date": "2026-07-01",
        },
    )
    pid = p.json()["purchase_id"]
    await auth_client.patch(f"{API}/selling/inventory/{pid}", json={"stage": "listed", "listed_price": 45})
    fresh = await auth_client.post(
        f"{API}/purchases",
        json={
            "title": "Felpa nuova",
            "purchase_price": 20,
            "shipping_cost": 0,
            "buyer_protection_fee": 0,
            "purchase_date": "2026-10-08",
        },
    )
    await auth_client.patch(
        f"{API}/selling/inventory/{fresh.json()['purchase_id']}", json={"stage": "listed", "listed_price": 40}
    )
    out = (await auth_client.get(f"{API}/selling/liquidation?threshold_days=45")).json()
    assert [i["title"] for i in out["items"]] == ["Felpa ferma"]  # the new one is not stale
    item = out["items"][0]
    assert item["days_held"] >= 45 and item["capital_freed"] == 20.0 and out["capital_tied_up"] == 20.0
    assert item["floor"] is not None and "minimo" in item["action"]
