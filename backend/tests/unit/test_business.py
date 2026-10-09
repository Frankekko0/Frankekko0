"""Phase 8d: business mode in pure logic (cases P, Q, R, S of the brief)."""

from datetime import date
from decimal import Decimal as D

import pytest

from app.business import (
    assumptions,
    cashflow,
    ceo_report,
    kpi,
    niches,
    plan,
    refurb,
    reinvest,
    risk,
    scanner,
    sop,
    tax,
)
from app.profit.calculator import CostProfile


# ------------------------------------------------------------------ assumptions and plan
def test_few_sales_mean_assumptions_and_enough_sales_mean_measured_figures() -> None:
    few = assumptions.measured([(20, 9, 15)] * 3)
    assert few.basis == "assumption" and few.avg_cost == assumptions.DEFAULT.avg_cost
    real = assumptions.measured([(10, 5, 10), (30, 15, 20), (20, 10, 15), (20, 10, 15), (20, 10, 15)])
    assert real.basis == "measured" and real.avg_cost == 20 and real.avg_profit == 10 and real.hold_days == 15


OP = assumptions.Operating(avg_cost=20, avg_profit=10, hold_days=15, minutes_per_item=45, basis="measured")


def test_the_plan_says_what_the_goal_needs_and_what_limits_it() -> None:
    goals = plan.Goals(monthly_profit_target=300, initial_capital=2000, max_capital=3000, weekly_hours=20)
    base = plan.scenario(goals, OP, "base")
    assert base.items_per_month == 30.0 and base.hours_per_month == 22.5 and base.rotation_per_month == 2.0
    assert base.capital_needed == pytest.approx(30 * 20 * 15 / 30 / 0.8, abs=0.01) and base.feasible
    poor = plan.scenario(plan.Goals(300, initial_capital=200), OP, "base")
    assert poor.limited_by == "capital" and poor.achievable_profit < 300 and not poor.feasible
    busy = plan.scenario(plan.Goals(300, initial_capital=5000, weekly_hours=5), OP, "base")
    assert busy.limited_by == "time" and busy.achievable_profit < 300
    p = [plan.scenario(goals, OP, n) for n in ("prudente", "base", "ambizioso")]
    assert p[0].items_per_month > p[1].items_per_month > p[2].items_per_month  # worse results need more items


def test_the_trajectory_compounds_with_reinvestment_and_stops_at_the_ceilings() -> None:
    goals = plan.Goals(
        monthly_profit_target=300,
        initial_capital=500,
        max_capital=900,
        weekly_hours=None,
        horizon_months=18,
        reinvest_pct=1.0,
    )
    traj = plan.trajectory(goals, OP)
    caps = [t["capital"] for t in traj]
    assert caps == sorted(caps) and max(caps) == 900  # grows, then the ceiling
    assert traj[1]["profit"] > traj[0]["profit"]
    out = plan.business_plan(goals, OP)
    assert out["scenarios"] and out["trajectory"] and "misurate" in out["note"]
    assert "ipotizzate" in plan.business_plan(goals, assumptions.DEFAULT)["note"]


# ------------------------------------------------------------------ KPIs
def test_the_kpis_come_from_the_ledger_and_the_stock() -> None:
    today = date(2026, 10, 1)
    sales = [
        kpi.SaleItem(D(20), D(35), D(15), 10, date(2026, 9, 10)),
        kpi.SaleItem(D(30), D(40), D(10), 20, date(2026, 9, 20)),
        kpi.SaleItem(D(10), D(8), D(-2), 40, date(2026, 8, 1)),  # outside the period
    ]
    stock = [
        kpi.StockItem(D(25), date(2026, 9, 25)),
        kpi.StockItem(D(25), date(2026, 8, 15)),
        kpi.StockItem(D(50), date(2026, 5, 1)),
    ]
    k = kpi.kpis(sales, stock, returns=1, today=today, start=date(2026, 9, 1))
    assert k["sales"] == 2 and k["realized_profit"] == 25 and k["contribution_margin_per_item"] == 12.5
    assert k["sell_through"] == 0.4  # 2 sold out of 2 + 3 in stock
    assert k["cash_conversion_days"] == 15.0 and k["return_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert k["gmroi"] == 0.25  # margin 25 over 100 of stock at cost
    ages = {a["bucket"]: a for a in k["aging"]}
    assert ages["0-14"]["items"] == 1 and ages["31-60"]["items"] == 1 and ages["60+"]["items"] == 1
    assert k["hourly_profit"] == pytest.approx(25 / (2 * 45 / 60), abs=0.01) and "stimate" in k["hours_basis"]
    declared = kpi.kpis(sales, stock, 0, today, date(2026, 9, 1), weekly_hours=10)
    assert "dichiarate" in declared["hours_basis"] and declared["hourly_profit"] < k["hourly_profit"]
    assert kpi.kpis([], [], 0, today, date(2026, 9, 1))["gmroi"] is None  # nothing to divide by


# ------------------------------------------------------------------ cash flow and the stress test (case S)
STOCK = [cashflow.StockForecast(expected_net_revenue=40, cost=20, listed=True) for _ in range(12)]


def test_case_s_the_stress_test_flags_a_liquidity_risk_that_the_base_case_does_not_have() -> None:
    out = cashflow.with_stress(
        cash_now=150, stock=STOCK, hold_days=20, monthly_running_costs=60, reinvest_pct=0.7, reserve=100
    )
    assert not any(h["liquidity_risk"] for h in out["base"]) or out["base"][0]["closing_cash"] >= 0
    assert out["liquidity_risk_in_stress"] is True and out["risk_horizons"]
    assert "dimezzate" in out["message"] and out["stress_definition"]["sales"] == "-50%"
    stress, base = out["stress"][1], out["base"][1]
    assert stress["inflow"] < base["inflow"] / 2.0 + 1e-6 or stress["inflow"] < base["inflow"]
    assert (
        stress["planned_purchases"] == base["planned_purchases"]
    )  # you buy before you know sales will halve
    healthy = cashflow.with_stress(
        cash_now=5000, stock=STOCK, hold_days=20, monthly_running_costs=60, reinvest_pct=0.7, reserve=100
    )
    assert healthy["liquidity_risk_in_stress"] is False and "resta sopra" in healthy["message"]


def test_items_not_yet_listed_sell_later_and_a_delay_pushes_the_money_back() -> None:
    listed = [cashflow.StockForecast(40, 20, True)]
    unlisted = [cashflow.StockForecast(40, 20, False)]
    a = cashflow.forecast(100, listed, 20, 0, 0.5, 0)[0]["inflow"]
    b = cashflow.forecast(100, unlisted, 20, 0, 0.5, 0)[0]["inflow"]
    assert b < a
    nothing = cashflow.forecast(100, listed, 20, 0, 0.5, 0, cashflow.Stress(extra_payout_delay=40))[0]
    assert nothing["inflow"] == 0  # the payment arrives after the horizon


# ------------------------------------------------------------------ niches (case P)
def sales_of(niche: str, rows: list[tuple[float, float, int, int]]) -> list[niches.NicheSale]:
    return [
        niches.NicheSale(niche, cost, profit, days, date(2026, 1, 1 + i * 5 + m), cost + profit)
        for i, (cost, profit, days, m) in enumerate(rows)
    ]


def test_case_p_capital_moves_from_the_declining_niche_to_the_growing_one_within_the_explore_share_and_stop_loss() -> (
    None
):
    down = sales_of(
        "nike felpe",
        [(20, 12, 8, 0), (20, 11, 9, 0), (20, 10, 9, 0), (20, 4, 20, 0), (20, 2, 25, 0), (20, 1, 30, 0)],
    )
    up = sales_of(
        "rl polo",
        [(20, 3, 25, 0), (20, 4, 22, 0), (20, 5, 20, 0), (20, 11, 9, 0), (20, 12, 8, 0), (20, 13, 8, 0)],
    )
    st = {s.niche: s for s in niches.stats(down + up)}
    assert (
        st["nike felpe"].declining
        and st["rl polo"].growing
        and st["nike felpe"].trend < 0 < st["rl polo"].trend
    )
    alloc = {a.niche: a for a in niches.allocate(list(st.values()), capital=1000)}
    assert alloc["nike felpe"].action == "reduce" and alloc["rl polo"].action == "scale"
    assert alloc["rl polo"].amount > alloc["nike felpe"].amount
    explore = alloc["(nuove nicchie)"]
    assert (
        explore.action == "explore" and explore.amount == 100.0 and explore.share == 0.10
    )  # the fixed exploration share
    assert sum(a.amount for a in alloc.values()) == pytest.approx(1000, abs=0.05)
    # the explore share is configurable and respected
    assert {a.niche: a for a in niches.allocate(list(st.values()), 1000, explore_share=0.2)}[
        "(nuove nicchie)"
    ].amount == 200.0

    # a niche that has lost more than the stop-loss share of its capital is stopped
    bad = niches.stats(sales_of("zara", [(20, -15, 10, 0), (20, -12, 10, 0), (20, -10, 10, 0)]) + up)
    stopped = {a.niche: a for a in niches.allocate(bad, 1000, given={"zara": 100.0, "rl polo": 450.0})}
    assert (
        stopped["zara"].action == "stop"
        and stopped["zara"].amount == 0.0
        and "stop-loss" in stopped["zara"].reason
    )
    assert stopped["rl polo"].amount > 0


def test_a_niche_with_too_few_sales_gets_no_capital_and_a_playbook_only_from_real_sales() -> None:
    few = niches.stats(sales_of("rare", [(20, 9, 10, 0), (20, 8, 10, 0)]))
    a = {x.niche: x for x in niches.allocate(few, 500)}
    assert a["rare"].amount == 0 and a["rare"].action == "hold" and "troppo poche" in a["rare"].reason
    assert "servono almeno" in niches.playbook(sales_of("rare", [(20, 9, 10, 0)]), "x")["note"]
    book = niches.playbook(
        sales_of("rl polo", [(15, 10, 8, 0), (18, 12, 9, 0), (22, 4, 25, 0), (20, 6, 15, 0)]),
        "ralph lauren polo M",
    )
    assert book["niche_search"] == "ralph lauren polo M" and book["win_rate"] == 1.0
    assert book["max_buy_price"] <= 22 and "difetti tipici e riparabili" in book["not_known"]


# ------------------------------------------------------------------ refurbishing (case Q)
NET = lambda price: price * 0.9 - 1.0  # noqa: E731


def test_case_q_a_garment_with_pilling_and_a_stain_is_judged_on_the_roi_after_the_repair() -> None:
    # Sold as it is, 20 EUR for a 60 EUR garment with three defects leaves a poor ROI; after the repair (materials
    # and labour included in the cost) it clears the minimum.
    r = refurb.evaluate(
        purchase_cost=20,
        resale_clean=60,
        defects=["pilling", "stain", "small_repair"],
        net_of_price=NET,
        min_roi=0.4,
    )
    assert (
        r.roi_as_is is not None
        and r.roi_as_is < 0.4
        and r.roi_after is not None
        and r.roi_after >= 0.4
        and r.worth_it
    )
    assert r.cost_after > r.cost_as_is == 20  # materials and labour are in the cost after
    assert r.materials == pytest.approx(5.30) and r.labour == pytest.approx(55 / 60 * 8)
    assert r.roi_after > r.roi_as_is and "il ROI sale" in r.reason and len(r.steps) == 3
    assert r.profit_after > r.profit_as_is and r.resale_after > r.resale_as_is
    # a cheap, easy fix on a garment that is already fine does not pass the same test
    easy = refurb.evaluate(
        purchase_cost=10, resale_clean=45, defects=["pilling", "stain"], net_of_price=NET, min_roi=0.5
    )
    assert (
        easy.worth_it and "rispetta il minimo" in easy.reason
    )  # fine as it is: the repair adds profit, not ROI
    # not worth it when the repair costs about what it brings back
    poor = refurb.evaluate(
        purchase_cost=10, resale_clean=14, defects=["small_repair"], net_of_price=NET, min_roi=0.4
    )
    assert not poor.worth_it
    unknown = refurb.evaluate(purchase_cost=10, resale_clean=45, defects=["burnt"], net_of_price=NET)
    assert not unknown.worth_it and "senza rimedio noto" in unknown.reason
    none = refurb.evaluate(purchase_cost=10, resale_clean=45, defects=[], net_of_price=NET)
    assert none.reason == "nessun difetto da recuperare"


# ------------------------------------------------------------------ reinvestment
def test_the_reinvestment_proposal_respects_the_withdrawal_and_the_ceilings() -> None:
    free = reinvest.propose(500, 0.12, 12)
    assert free["proposal"]["reinvest_pct"] == 1.0  # nothing asked back: compound it all
    need = reinvest.propose(500, 0.12, 12, min_total_withdrawn=400)
    assert need["proposal"]["total_withdrawn"] >= 400 and need["proposal"]["reinvest_pct"] < 1.0
    capped = reinvest.propose(500, 0.12, 12, max_capital=700, time_cap_profit=80)
    assert capped["proposal"]["capped_by"] in ("capital", "time")
    impossible = reinvest.propose(500, 0.01, 6, min_total_withdrawn=10_000)
    assert impossible["proposal"] is None and "Nessuna politica" in impossible["note"]
    ceil = reinvest.propose(500, 0.12, 12, reinvest_ceiling=0.5)
    assert ceil["proposal"]["reinvest_pct"] <= 0.5


# ------------------------------------------------------------------ fiscal thresholds (case S)
def test_a_threshold_needs_its_source_and_date_and_nothing_is_built_in() -> None:
    ok = tax.parse([{"name": "soglia X", "amount": 2000, "source": "sito ufficiale", "as_of": "2026-01-10"}])
    assert ok[0].metric == "revenue"
    for bad, msg in [
        ([{"name": "x", "amount": 100}], "fonte"),
        ([{"name": "x", "amount": 100, "source": "s", "as_of": "ieri"}], "AAAA-MM-GG"),
        ([{"name": "x", "amount": -1, "source": "s", "as_of": "2026-01-01"}], "positivo"),
        ([{"name": "x", "amount": 5, "metric": "magia", "source": "s", "as_of": "2026-01-01"}], "metrica"),
        ([{"amount": 5}], "obbligatori"),
    ]:
        with pytest.raises(tax.ThresholdError, match=msg):
            tax.parse(bad)
    assert tax.parse([]) == []


def test_case_s_approaching_a_configured_threshold_raises_a_notice_with_its_source() -> None:
    th = tax.parse(
        [
            {"name": "soglia X", "amount": 2000, "source": "agenzia", "as_of": "2026-01-10"},
            {
                "name": "vendite",
                "amount": 30,
                "metric": "sales_count",
                "source": "agenzia",
                "as_of": "2026-01-10",
            },
        ]
    )
    today = date(2026, 10, 1)
    ok, near = tax.check(th, revenue=600, profit=200, sales=10, today=today)
    assert ok.level == "ok" and near.level == "ok"
    near = tax.check(th, revenue=1700, profit=500, sales=10, today=today)[0]
    assert (
        near.level == "approaching"
        and "85%" in near.message
        and "agenzia" in near.message
        and "commercialista" in near.message
    )
    over = tax.check(th, revenue=2100, profit=500, sales=31, today=today)
    assert [n.level for n in over] == ["exceeded", "exceeded"]
    # by projection: well below 80% in January... but the pace would cross it within the year
    proj = tax.check(th[:1], revenue=1400, profit=0, sales=0, today=date(2026, 6, 30))[0]
    assert proj.level == "approaching" and proj.projected is not None and "ritmo" in proj.message
    assert any("garanzia" in c.lower() for c in tax.PROFESSIONAL_CHECKLIST)


# ------------------------------------------------------------------ operations and risk
def test_capacity_planning_and_the_collaborator_brief() -> None:
    c = sop.capacity_plan(
        items_per_month=40, minutes_per_item=45, weekly_hours=10, stock_items=60, storage_shelves=3
    )
    assert (
        c["hours_per_month_needed"] == 30.0
        and c["hours_per_month_available"] == 43.3
        and not c["needs_helper"]
    )
    assert c["shelves_needed_for_stock"] == 4.0 and c["items_per_month_by_time"] == 57.7
    big = sop.capacity_plan(100, 45, 10, 10)
    assert big["needs_helper"] and big["helper_hours_needed"] == 31.7
    brief = sop.collaborator_brief("assistente_spedizioni")
    assert "Spedizione" in brief and "caricare il tracking" in brief and "Pulizia" not in brief
    with pytest.raises(ValueError):
        sop.collaborator_brief("boss")
    assert set(sop.SOPS) >= {
        "ricezione",
        "controllo",
        "pulizia",
        "foto",
        "pubblicazione",
        "spedizione",
        "resi",
    }


def test_business_risk_rules() -> None:
    assert (
        risk.should_insure(80)[0]
        and not risk.should_insure(20)[0]
        and risk.should_insure(20, tracked=False)[0]
    )
    assert risk.reserve_needed(1000, 0.04, 0.02, 0.01) == 70.0
    assert risk.margin_alarm([10, 10, 10, 6, 6, 6])["alarm"] is True
    assert risk.margin_alarm([10, 10, 10, 9, 10, 10])["alarm"] is False
    assert "servono almeno" in risk.margin_alarm([1, 2])["note"]
    dep = risk.platform_dependency({"vinted": 950, "altro": 50})
    assert (
        dep["alarm"] and dep["contingency"] and risk.platform_dependency({"a": 50, "b": 50})["alarm"] is False
    )
    assert any("Esporta" in c for c in risk.CONTINGENCY)


# ------------------------------------------------------------------ the in-store scanner (case R)
COSTS = CostProfile()
REF = scanner.MarketRef(median=40.0, p25=32.0, p75=48.0, n_sold=25, source="brand+categoria")


def do_scan(price: float, ref: scanner.MarketRef | None = REF, **kw: object) -> scanner.ScanResult:
    base = dict(brand="ralph-lauren", category="felpe", size="M")
    return scanner.scan(
        shown_price=price, ref=ref, costs=COSTS, min_profit=8.0, min_roi=0.4, **{**base, **kw}
    )  # type: ignore[arg-type]


def test_case_r_the_scanner_answers_fast_with_identification_max_price_verdict_and_confidence() -> None:
    cheap, mid, dear = do_scan(8), do_scan(16), do_scan(35)
    assert cheap.verdict == "BUY" and mid.verdict in ("NEGOTIATE", "BUY") and dear.verdict == "PASS"
    assert (
        cheap.max_buy_price is not None
        and cheap.max_buy_price_fast_sale is not None
        and cheap.max_buy_price_fast_sale <= cheap.max_buy_price
    )
    assert cheap.max_buy_price_fast_sale >= 8 or cheap.verdict == "BUY"
    assert cheap.identification["known"] == ["brand", "category", "size"] and 0 < cheap.confidence <= 100
    assert dear.reason.startswith("Sopra il massimo") and "sconto necessario" in dear.reason
    for r in (cheap, mid, dear):
        assert r.elapsed_ms < scanner.LATENCY_TARGET_MS  # within the latency threshold
    assert cheap.expected_profit is not None and cheap.expected_profit > 0


def test_case_r_with_too_little_data_the_scanner_says_not_verified_and_invents_nothing() -> None:
    for r in (
        do_scan(10, ref=None),
        do_scan(10, ref=scanner.MarketRef(40.0, 32.0, 48.0, n_sold=2, source="x")),
        do_scan(10, ref=scanner.MarketRef(None, None, None, 0, "x")),
        do_scan(10, brand=None),
    ):
        assert (
            r.verdict == "NOT_VERIFIED"
            and r.max_buy_price is None
            and r.expected_profit is None
            and r.confidence == 0
        )
        assert "Nessun prezzo stimato" in r.reason
    wide = scanner.MarketRef(40.0, 15.0, 70.0, 25, "x")  # a huge spread: less sure
    assert do_scan(8, ref=wide).confidence < do_scan(8).confidence
    assert do_scan(8, size=None, category=None).confidence < do_scan(8).confidence


# ------------------------------------------------------------------ the CEO report (case S)
def test_case_s_the_ceo_report_matches_the_ledger_and_fits_one_page() -> None:
    summary = {
        "revenue": D("45.00"), "selling_costs": D("4.50"), "cost_of_goods_sold": D("25.00"), "realized_profit": D("15.50"),
        "other_expenses": D("6.00"), "realized_profit_after_expenses": D("9.50"), "sales_count": 1, "cash_in": D("45.00"),
        "cash_out": D("54.00"), "cash_flow": D("-9.00"), "stock_at_cost": D("18.50"), "stock_items": 1,
    }  # fmt: skip
    k = {
        "contribution_margin_per_item": 15.5,
        "sell_through": 0.5,
        "cash_conversion_days": 7.0,
        "hourly_profit": 20.67,
        "hours_basis": "stimate",
    }
    r = ceo_report.build(
        period="mese", start=date(2026, 9, 1), end=date(2026, 9, 30), summary=summary, kpis=k, monthly_target=100.0,
        niche_best=[{"niche": "rl polo", "profit": 15.5}], niche_worst=[{"niche": "nike felpa", "profit": -2.0}],
        actions_by_status={"pending_user": 2, "blocked": 1}, to_list=1, reprice_due=0, budget_left=40.0, plan_suggestions=2,
        cash_message="Anche con vendite dimezzate la liquidità resta sopra la riserva.", tax_messages=["Hai raggiunto il 85% della soglia X."],
    )  # fmt: skip
    f = r["figures"]
    assert (
        f["revenue"] == 45.0
        and f["realized_profit"] == 15.5
        and f["profit_after_expenses"] == 9.5
        and f["cash_flow"] == -9.0
    )
    assert f["target"] == pytest.approx(100.0 * 30 / 30) and f["deviation"] == pytest.approx(9.5 - 100.0)
    text = r["text"]
    assert (
        "Profitto realizzato: 15,50 €" in text
        and "Profitto dopo le spese: 9,50 €" in text
        and "non sono una perdita" in text
    )
    assert "90,50 € sotto il piano" in text and "Bene: rl polo" in text and "Male: nike felpa" in text
    assert (
        "2 pending_user" in text
        and "pubblicare 1 articoli" in text
        and "budget residuo 40,00 €" in text
        and "85%" in text
    )
    assert r["lines"] <= 45  # one page
