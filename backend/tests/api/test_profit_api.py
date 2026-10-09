"""/profit/calculate: exact figures, cost statuses, restoration, reserve, break-even."""

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Analysis
from app.ingestion.service import IngestionService
from app.opportunities.pipeline import AnalysisPipeline
from tests.conftest import NOW
from tests.integration.test_pipeline import build_market

API = "/api/v1"
BRIEF_COSTS = {
    "buyer_protection_fixed": 2,
    "buyer_protection_pct": 0,
    "shipping_in": 4,
    "use_listing_shipping": False,
    "packaging": 1,
}


async def save_costs(client: httpx.AsyncClient, **costs) -> None:
    prefs = (await client.get(f"{API}/settings/preferences")).json()
    prefs["cost_profile"].update(costs)
    assert (await client.put(f"{API}/settings/preferences", json=prefs)).status_code == 200


async def calc(client: httpx.AsyncClient, **body) -> dict:
    r = await client.post(f"{API}/profit/calculate", json={"purchase_price": 20, "sale_price": 45, **body})
    assert r.status_code == 200, r.text
    return r.json()


async def test_the_old_fields_are_unchanged_and_the_new_ones_are_added(
    auth_client: httpx.AsyncClient,
) -> None:
    body = await calc(auth_client)
    assert body["total_acquisition_cost"] == pytest.approx(20 + 0.70 + 1.00 + 3.49)
    assert body["net_profit"] == pytest.approx(45 - 0.5 - 25.19)
    assert body["capital_tied_up"] == pytest.approx(25.19)
    assert body["margin_on_sale"] == pytest.approx(body["net_profit"] / 45, abs=1e-4)
    assert body["break_even_price"] == pytest.approx(25.19 + 0.5, abs=0.011)
    assert body["taxes_included"] is False and "Imposte escluse" in body["taxes_note"]


async def test_without_saved_costs_the_missing_ones_are_unknown_not_invented(
    auth_client: httpx.AsyncClient,
) -> None:
    body = await calc(auth_client)
    assert body["cost_status"] == "unknown" and body["gross_of_unknown_costs"] is True
    assert set(body["unknown_costs"]) == {"Promozione / advertising", "Spedizione a tuo carico"}
    statuses = {line["key"]: (line["status"], line["source"]) for line in body["lines"]}
    assert statuses["purchase_price"] == ("confirmed", "listing")
    assert statuses["packaging"] == ("estimated", "default")


async def test_after_saving_your_costs_the_reference_case_is_exact(auth_client: httpx.AsyncClient) -> None:
    await save_costs(auth_client, **BRIEF_COSTS)
    body = await calc(auth_client)
    assert body["total_acquisition_cost"] == 26.0  # 20 + 4 + 2
    assert body["net_sale_revenue"] == 44.0  # 45 - 1
    assert body["net_profit"] == 18.0
    assert body["roi"] == pytest.approx(0.6923)  # 69.23 %
    assert body["margin_on_sale"] == pytest.approx(0.4)
    assert body["break_even_price"] == 27.0
    assert body["unknown_costs"] == [] and body["gross_of_unknown_costs"] is False
    assert body["cost_status"] == "estimated"  # the protection is a formula until checkout shows it


async def test_restoration_and_reserve_flow_through_every_figure(auth_client: httpx.AsyncClient) -> None:
    await save_costs(auth_client, **BRIEF_COSTS)
    plain = await calc(auth_client)
    body = await calc(auth_client, restoration_cost=3, contingency_pct=0.05)
    assert body["total_acquisition_cost"] == 29.0 and body["capital_tied_up"] == 29.0
    assert body["net_sale_revenue"] == pytest.approx(41.75)  # 45 - 1 - 2.25
    assert body["net_profit"] == pytest.approx(12.75)
    assert body["break_even_price"] == pytest.approx(31.58)
    assert {"Ripristino"} <= {c["label"] for c in body["acquisition_breakdown"]}
    assert {"Riserva per imprevisti"} <= {c["label"] for c in body["sale_breakdown"]}
    assert {line["key"] for line in body["lines"]} >= {"restoration", "contingency"}
    # both costs make the recommended maximum purchase price lower
    assert body["max_buy_price"] < plain["max_buy_price"]


async def test_the_breakdown_is_unchanged_when_there_is_no_restoration_or_reserve(
    auth_client: httpx.AsyncClient,
) -> None:
    body = await calc(auth_client)
    assert [c["label"] for c in body["acquisition_breakdown"]] == [
        "Prezzo d'acquisto",
        "Protezione acquisti",
        "Spedizione",
        "Altri costi d'acquisto",
    ]
    assert "Riserva per imprevisti" not in [c["label"] for c in body["sale_breakdown"]]


@pytest.mark.parametrize(
    "bad", [{"contingency_pct": 0.6}, {"contingency_pct": -0.1}, {"restoration_cost": -1}]
)
async def test_out_of_range_inputs_are_rejected(auth_client: httpx.AsyncClient, bad: dict) -> None:
    r = await auth_client.post(
        f"{API}/profit/calculate", json={"purchase_price": 20, "sale_price": 45, **bad}
    )
    assert r.status_code == 422


async def test_a_stored_analysis_carries_the_evaluation_with_its_basis(session, make_listing) -> None:
    await build_market(session, make_listing)
    deal = make_listing(
        title="Polo Ralph Lauren Custom Slim Fit blu navy M", price=12, published_days_ago=0.05
    )
    res = await IngestionService(session, "test").ingest([deal], now=NOW)
    await session.commit()
    await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    await session.commit()
    analysis = (
        (await session.execute(select(Analysis).where(Analysis.listing_id == res.new_ids[0])))
        .scalars()
        .first()
    )
    assert analysis is not None
    ev = analysis.economic["evaluation"]
    assert ev["basis"] == "default_cost_profile"
    assert ev["taxes_included"] is False
    assert ev["cost_status"] in ("estimated", "unknown")
    assert float(ev["capital_tied_up"]) == pytest.approx(float(analysis.economic["total_acquisition_cost"]))
    assert float(ev["net_profit"]) == pytest.approx(
        float(analysis.economic["scenarios"]["expected"]["profit"])
    )
    assert ev["break_even_price"] is not None and ev["margin_on_sale"] is not None
