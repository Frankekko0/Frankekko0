"""Phase 8d through the API, on real records: goals, plan, KPIs, stress test, niches, the CEO report matching the
ledger (case S), fiscal thresholds with alerts, refurbishing (Q) and the in-store scanner (R)."""

from typing import Any

import httpx
from sqlalchemy import select

from app.db.models import Alert, Brand, Category, MarketStatistic
from app.db.session import session_scope
from tests.api.test_api import API
from tests.api.test_decision_api import analyse

GOALS = {
    "monthly_profit_target": 300, "initial_capital": 1000, "max_capital": 2000, "weekly_hours": 15, "horizon_months": 12,
    "reinvest_pct": 0.7, "min_reserve": 100, "explore_share": 0.1, "holder_status": "private", "tax_thresholds": [],
}  # fmt: skip


async def put_goals(client: httpx.AsyncClient, **kw: Any) -> httpx.Response:
    return await client.put(f"{API}/business/goals", json={**GOALS, **kw})


async def sell(
    client: httpx.AsyncClient,
    title: str,
    cost: float,
    price: float,
    bought: str,
    sold: str,
    brand: str | None = None,
) -> None:
    p = await client.post(
        f"{API}/purchases",
        json={
            "title": title,
            "brand": brand,
            "purchase_price": cost,
            "shipping_cost": 0,
            "buyer_protection_fee": 0,
            "purchase_date": bought,
        },
    )
    assert p.status_code == 201, p.text
    s = await client.post(
        f"{API}/sales", json={"purchase_id": p.json()["purchase_id"], "sale_price": price, "sale_date": sold}
    )
    assert s.status_code == 201, s.text


async def test_goals_are_validated_and_thresholds_need_a_source(auth_client: httpx.AsyncClient) -> None:
    ok = await put_goals(auth_client)
    assert ok.status_code == 200 and ok.json()["monthly_profit_target"] == 300
    assert (await put_goals(auth_client, max_capital=500)).status_code == 400  # below the initial capital
    bad = await put_goals(auth_client, tax_thresholds=[{"name": "x", "amount": 100}])
    assert (
        bad.status_code == 400
        and bad.json()["error"]["code"] == "invalid_threshold"
        and "fonte" in bad.json()["error"]["message"]
    )
    assert (await put_goals(auth_client, reinvest_pct=2)).status_code == 422
    # the plan needs goals
    fresh = (await auth_client.get(f"{API}/business/goals")).json()
    assert fresh["holder_status"] == "private"


async def test_the_plan_uses_assumptions_until_there_are_real_sales(auth_client: httpx.AsyncClient) -> None:
    missing = await auth_client.get(f"{API}/business/plan")
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "goals_missing"
    await put_goals(auth_client)
    plan = (await auth_client.get(f"{API}/business/plan")).json()
    assert plan["operating"]["basis"] == "assumption" and "ipotizzate" in plan["note"]
    assert [s["name"] for s in plan["scenarios"]] == ["prudente", "base", "ambizioso"] and len(
        plan["trajectory"]
    ) == 12
    for i in range(5):  # five closed sales: the figures become measured
        await sell(auth_client, f"Felpa {i}", 20, 32, "2026-09-01", "2026-09-10")
    real = (await auth_client.get(f"{API}/business/plan")).json()
    assert (
        real["operating"]["basis"] == "measured"
        and real["operating"]["avg_profit"] == 12.0
        and real["operating"]["hold_days"] == 9.0
    )


async def test_the_ceo_report_matches_the_ledger_and_the_kpis(auth_client: httpx.AsyncClient) -> None:
    from datetime import UTC, datetime, timedelta

    await put_goals(auth_client)
    today = datetime.now(UTC).date()
    d = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    await sell(auth_client, "Felpa RL", 25, 45, d(5), d(1), brand="ralph-lauren")
    await sell(auth_client, "Giacca", 30, 28, d(5), d(2))  # a small loss
    await auth_client.post(
        f"{API}/accounting/expenses", json={"spent_on": d(3), "kind": "packaging", "amount": 6}
    )
    rep = (await auth_client.get(f"{API}/business/report?period=week")).json()
    summary = (await auth_client.get(f"{API}/accounting/summary?start={d(6)}&end={today.isoformat()}")).json()
    f = rep["figures"]
    assert (
        f["revenue"] == summary["revenue"] == 73 and f["realized_profit"] == summary["realized_profit"] == 18
    )  # 20 - 2
    assert (
        f["profit_after_expenses"] == summary["realized_profit_after_expenses"] == 12
        and f["cash_flow"] == summary["cash_flow"]
    )
    assert f["target"] == round(300 * 7 / 30, 2) and f["deviation"] == round(12 - f["target"], 2)
    text = rep["text"]
    assert (
        "Profitto realizzato: 18,00 €" in text and "Bene:" in text and "Male: " in text and rep["lines"] <= 45
    )
    kp = (await auth_client.get(f"{API}/business/kpis?start={d(6)}&end={today.isoformat()}")).json()
    assert kp["sales"] == 2 and kp["realized_profit"] == 18 and kp["contribution_margin_per_item"] == 9.0
    assert kp["cash_conversion_days"] == 3.5  # 4 and 3 days


async def test_case_s_the_stress_test_through_the_api(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await put_goals(auth_client, initial_capital=60, max_capital=None, min_reserve=50)
    opps = await analyse(make_listing, None, None, None)
    for o in opps:
        b = await auth_client.post(
            f"{API}/purchases",
            json={
                "title": f"Articolo {o.id}",
                "opportunity_id": str(o.id),
                "purchase_price": 20,
                "shipping_cost": 0,
                "buyer_protection_fee": 0,
                "purchase_date": "2026-09-01",
            },
        )
        await auth_client.patch(
            f"{API}/selling/inventory/{b.json()['purchase_id']}", json={"stage": "listed", "listed_price": 40}
        )
    cf = (await auth_client.get(f"{API}/business/cashflow")).json()
    assert (
        [h["horizon_days"] for h in cf["base"]] == [30, 60, 90] == [h["horizon_days"] for h in cf["stress"]]
    )
    assert cf["stress_definition"]["sales"] == "-50%" and "capitale iniziale" in cf["basis"]
    assert cf["stress"][0]["inflow"] < cf["base"][0]["inflow"]
    assert cf["stress"][1]["planned_purchases"] == cf["base"][1]["planned_purchases"]
    assert isinstance(cf["liquidity_risk_in_stress"], bool) and cf["message"]


async def test_niches_and_the_playbook_come_from_real_sales(auth_client: httpx.AsyncClient) -> None:
    await put_goals(auth_client, initial_capital=1000, max_capital=None)
    empty = (await auth_client.get(f"{API}/business/niches")).json()
    assert empty["niches"] == [] and "Nessuna vendita" in empty["note"]
    for i in range(4):
        await sell(
            auth_client, f"Felpa {i}", 20, 34, f"2026-0{5 + i}-01", f"2026-0{5 + i}-09", brand="ralph-lauren"
        )
    n = (await auth_client.get(f"{API}/business/niches")).json()
    assert (n["niches"][0]["n"] == 4 and n["niches"][0]["niche"].startswith("ralph-lauren")) or "Ralph" in n[
        "niches"
    ][0]["niche"]
    assert n["allocation"][-1]["action"] == "explore" and n["allocation"][-1]["amount"] == 100.0
    book = next(iter(n["playbooks"].values()))
    assert book["win_rate"] == 1.0 and book["resale_price_median"] == 34.0 and book["not_known"]


async def test_case_s_fiscal_thresholds_raise_an_alert_once_with_their_source(
    auth_client: httpx.AsyncClient,
) -> None:
    from datetime import UTC, datetime, timedelta

    th = [
        {
            "name": "soglia X",
            "amount": 100,
            "metric": "revenue",
            "source": "sito ufficiale",
            "as_of": "2026-01-10",
        }
    ]
    await put_goals(auth_client, tax_thresholds=th, holder_status="occasional")
    today = datetime.now(UTC).date()
    await sell(
        auth_client,
        "Felpa",
        20,
        85,
        (today - timedelta(days=3)).isoformat(),
        (today - timedelta(days=1)).isoformat(),
    )
    t = (await auth_client.get(f"{API}/business/tax?notify=true")).json()
    n = t["notices"][0]
    assert (
        n["level"] == "approaching"
        and "85%" in n["message"]
        and n["source"] == "sito ufficiale"
        and t["alerts_raised"] == 1
    )
    assert t["holder_status"] == "occasional" and any("garanzia" in c.lower() for c in t["checklist"])
    again = (await auth_client.get(f"{API}/business/tax?notify=true")).json()
    assert again["alerts_raised"] == 0  # once per threshold, year and level
    async with session_scope() as s:
        alerts = (await s.execute(select(Alert))).scalars().all()
    assert len(alerts) == 1 and alerts[0].type == "system" and "ti stai avvicinando" in alerts[0].title
    await sell(auth_client, "Giacca", 10, 30, (today - timedelta(days=3)).isoformat(), today.isoformat())
    crossed = (await auth_client.get(f"{API}/business/tax?notify=true")).json()
    assert crossed["notices"][0]["level"] == "exceeded" and crossed["alerts_raised"] == 1
    rep = (await auth_client.get(f"{API}/business/report?period=month")).json()
    assert "SOGLIE FISCALI" in rep["text"] and "Superata la soglia" in rep["text"]


async def test_case_q_refurbishing_through_the_api(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.post(
        f"{API}/business/refurb",
        json={
            "purchase_cost": 20,
            "resale_clean": 60,
            "defects": ["pilling", "stain", "small_repair"],
            "min_roi": 0.4,
        },
    )
    j = r.json()
    assert r.status_code == 200 and j["worth_it"] is True and j["roi_after"] >= 0.4 > j["roi_as_is"]
    assert j["materials"] == 5.3 and "assumption" in j and "stain" in j["known_remedies"]
    no = (
        await auth_client.post(
            f"{API}/business/refurb",
            json={"purchase_cost": 10, "resale_clean": 14, "defects": ["small_repair"]},
        )
    ).json()
    assert no["worth_it"] is False
    assert (
        await auth_client.post(
            f"{API}/business/refurb", json={"purchase_cost": 10, "resale_clean": 14, "defects": []}
        )
    ).status_code == 422


async def seed_segment(sold: int, median: float = 40.0) -> None:
    async with session_scope() as s:
        brand = (await s.execute(select(Brand).where(Brand.slug == "ralph-lauren"))).scalar_one()
        cat = (await s.execute(select(Category).where(Category.slug == "felpe"))).scalar_one_or_none() or (
            await s.execute(select(Category))
        ).scalars().first()
        assert cat is not None
        s.add(
            MarketStatistic(
                segment_key=f"scan-{brand.id}-{cat.id}", brand_id=brand.id, category_id=cat.id, model_name=None, size_normalized=None,
                sample_size=sold + 5, sold_count=sold, active_count=5, median_price=median, mean_price=median, p25_price=median * 0.8,
                p75_price=median * 1.2, min_reasonable_price=median * 0.5, max_reasonable_price=median * 1.6, avg_listing_price=median,
                median_sold_price=median, sell_through_rate=0.5, window_days=90,
            )
        )  # fmt: skip


async def test_case_r_the_scanner_answers_in_time_and_says_not_verified_without_data(
    auth_client: httpx.AsyncClient,
) -> None:
    body = {"shown_price": 8, "brand": "Ralph Lauren", "category": None, "size": "m"}
    blank = (await auth_client.post(f"{API}/business/scan", json=body)).json()
    assert (
        blank["verdict"] == "NOT_VERIFIED"
        and blank["max_buy_price"] is None
        and "Nessun prezzo stimato" in blank["reason"]
    )
    await seed_segment(sold=20)
    r = (await auth_client.post(f"{API}/business/scan", json=body)).json()
    assert r["verdict"] == "BUY" and r["max_buy_price"] > 8 and r["confidence"] > 0
    assert r["elapsed_ms"] < r["latency_target_ms"] and r["identification"]["brand"] == "ralph-lauren"
    dear = (await auth_client.post(f"{API}/business/scan", json={**body, "shown_price": 60})).json()
    assert dear["verdict"] == "PASS" and "sconto necessario" in dear["reason"]
    # the label text fills what is missing
    label = (
        await auth_client.post(
            f"{API}/business/scan",
            json={"shown_price": 8, "label_text": "Polo Ralph Lauren\nSIZE M\n67% COTTON 33% POLYESTER"},
        )
    ).json()
    assert (
        label["identification"]["brand"] == "ralph-lauren"
        and label["identification"]["size"] == "M"
        and label["verdict"] == "BUY"
    )


async def test_operations_and_risk_endpoints(auth_client: httpx.AsyncClient) -> None:
    sops = (await auth_client.get(f"{API}/business/sop")).json()
    assert "spedizione" in sops["sops"] and "non sono ancora applicati" in sops["note"]
    brief = (await auth_client.get(f"{API}/business/sop/assistente_foto")).json()
    assert "Foto" in brief["instructions"]
    assert (await auth_client.get(f"{API}/business/sop/capo")).status_code == 400
    await put_goals(auth_client)
    cap = (await auth_client.get(f"{API}/business/capacity?storage_shelves=4")).json()
    assert (
        cap["hours_per_month_needed"] > 0 and cap["assumption"] and cap["operating"]["basis"] == "assumption"
    )
    rk = (await auth_client.get(f"{API}/business/risk")).json()
    assert rk["contingency"] and rk["platform"]["alarm"] is False
    await sell(auth_client, "Felpa", 20, 30, "2026-09-01", "2026-09-05")
    ex = (await auth_client.get(f"{API}/business/export")).json()
    assert (
        len(ex["purchases"]) == 1
        and len(ex["sales"]) == 1
        and ex["sales"][0]["sale_price"] == "30.00"
        and ex["inventory"]
    )
    assert (await auth_client.get(f"{API}/business/risk")).json()["platform"]["top_platform"] == "vinted"
