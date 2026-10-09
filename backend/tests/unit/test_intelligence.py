"""Phase 8c: the advanced-intelligence modules. The data here is synthetic and says so: it checks that the
estimators recover what was put in, not that the system works on Vinted (no real outcomes exist yet)."""

import math
import random

import pytest

from app.intelligence import (
    baseline,
    champion,
    counterfactual,
    drift,
    exposure,
    kelly,
    montecarlo,
    premortem,
    probcal,
    survival,
    voi,
)
from app.intelligence.survival import Obs


# ------------------------------------------------------------------ survival: time to sell
def synthetic_obs(h0: float, beta: float, n: int = 1500, horizon: float = 90.0, seed: int = 3) -> list[Obs]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        ratio = rng.uniform(0.6, 1.5)
        t = rng.expovariate(h0 * math.exp(-beta * (ratio - 1)))
        out.append(Obs(min(t, horizon), t <= horizon, ratio))  # unsold at the horizon: censored
    return out


def test_the_hazard_fit_recovers_the_truth_from_censored_data() -> None:
    fit = survival.fit_hazard(synthetic_obs(0.05, 5.0))
    assert fit.reliable and fit.source == "measured"
    assert fit.h0 == pytest.approx(0.05, rel=0.15) and fit.beta == pytest.approx(5.0, abs=1.0)


def test_with_too_few_sales_the_fit_says_prior_not_measured() -> None:
    fit = survival.fit_hazard([Obs(10, True, 1.0), Obs(30, False, 1.1)])
    assert not fit.reliable and fit.source == "prior" and fit.beta == survival.PRIOR_BETA
    assert survival.fit_hazard([]).source == "prior"


def test_a_higher_price_sells_slower_and_the_best_price_is_not_the_highest() -> None:
    fit = survival.fit_hazard(synthetic_obs(0.05, 5.0))
    assert (
        survival.expected_days(fit, 1.3) > survival.expected_days(fit, 1.0) > survival.expected_days(fit, 0.8)
    )
    assert survival.p_sold_by(fit, 1.0, 30) > survival.p_sold_by(fit, 1.3, 30)
    cost, reference = 20.0, 40.0
    profit = lambda price: price * 0.9 - cost  # 10% of the price goes in costs  # noqa: E731
    best = survival.best_price(fit, reference, profit, min_profit=5)
    assert best is not None and best.profit >= 5
    curve = survival.price_curve(fit, reference, profit)
    top = max(curve, key=lambda p: p.price)
    assert best.profit_per_day >= top.profit_per_day and best.price < top.price
    # An impossible minimum profit gives no price rather than a bad one.
    assert survival.best_price(fit, reference, profit, min_profit=1000) is None


def test_the_markdown_plan_goes_down_in_steps_to_the_floor_with_waits_from_the_curve() -> None:
    fit = survival.fit_hazard(synthetic_obs(0.05, 5.0))
    plan = survival.markdown_plan(fit, 40.0, start_price=52.0, floor=33.0, steps=4)
    prices = [s.price for s in plan]
    assert prices == sorted(prices, reverse=True) and prices[0] == 52.0 and prices[-1] == 33.0
    days = [s.day for s in plan]
    assert days[0] == 0 and days == sorted(days) and len(set(days)) == len(days)
    assert "minimo" in plan[-1].why and "nessuna vendita" in plan[1].why
    flat = survival.markdown_plan(fit, 40.0, start_price=30.0, floor=33.0)
    assert len(flat) == 1 and flat[0].price == 30.0


# ------------------------------------------------------------------ Monte Carlo
NET = lambda price: price * 0.9 - 1.0  # noqa: E731


def inputs(**kw: float) -> montecarlo.MCInputs:
    base = dict(cost=20.0, resale_low=26.0, resale_mid=34.0, resale_high=42.0, net_of_price=NET)
    return montecarlo.MCInputs(**{**base, **kw})  # type: ignore[arg-type]


def test_the_simulation_is_replayable_and_ordered() -> None:
    a, b = montecarlo.simulate(inputs(), seed=5), montecarlo.simulate(inputs(), seed=5)
    assert a.mean == b.mean and a.p10 == b.p10
    assert a.p10 <= a.p50 <= a.p90 and a.p_loss < 0.05
    assert a.mean == pytest.approx(NET(34.0) - 20.0, abs=0.7)  # triangular mean = (26+34+42)/3 = 34


def test_risks_move_the_distribution_the_way_they_should() -> None:
    clean = montecarlo.simulate(inputs())
    risky = montecarlo.simulate(inputs(p_fake=0.2, p_return=0.1, return_cost=6.0, p_defect=0.3))
    assert risky.mean < clean.mean and risky.p10 < clean.p10 and risky.p_loss > clean.p_loss
    assert 0.3 <= risky.p_loss < 0.45  # fake + return are certain losses; a defect at a low price adds some
    slow = montecarlo.simulate(inputs(hazard=0.01, horizon_days=30, liquidation_factor=0.5))
    assert slow.p_unsold > 0.6 and slow.mean < clean.mean and slow.expected_days is not None
    with pytest.raises(ValueError):
        montecarlo.simulate(inputs(cost=0))


# ------------------------------------------------------------------ Kelly and exposure (test O)
def test_kelly_sizes_by_edge_and_uncertainty() -> None:
    good = montecarlo.simulate(inputs()).returns
    k = kelly.kelly_stake(good, capital=200, cost=20, confidence=0.8)
    assert k.full_fraction > 0 and 0 < k.fraction_used <= 0.20 and k.affordable
    tiny = kelly.kelly_stake(good, capital=40, cost=20, confidence=0.8)
    assert not tiny.affordable and "trattare o rinunciare" in tiny.reason
    losing = montecarlo.simulate(inputs(resale_low=15, resale_mid=19, resale_high=23)).returns
    none = kelly.kelly_stake(losing, capital=500, cost=20)
    assert none.full_fraction == 0 and not none.affordable and "nessun vantaggio" in none.reason
    assert kelly.kelly_stake(good, capital=0, cost=20).affordable is False
    assert kelly.kelly_stake(good, 500, 20, confidence=0.0).fraction_used == 0.0


def test_exposure_blocks_concentration_but_not_the_first_purchases() -> None:
    H = exposure.Holding
    held = [H(30, brand="rl", category="polo", size="M", price_band="20-40", seller="s1") for _ in range(3)]
    cand = H(30, brand="rl", category="polo", size="M", price_band="20-40", seller="s1")
    v = exposure.check_exposure(held, cand)
    assert {x.dimension for x in v} == {"brand", "category", "size", "price_band", "seller"}
    assert all(x.share_after == 1.0 for x in v) and "capitale investito" in v[0].label()
    # fewer than four items: nothing to concentrate yet
    assert exposure.check_exposure(held[:2], cand) == []
    other = H(30, brand="nike", category="felpa", size="L", price_band="40-80", seller="s2")
    assert exposure.check_exposure([*held[:2], H(30, brand="rl"), H(30, brand="nike")], other) == []
    assert exposure.shares(held, "brand") == {"rl": 1.0}


# ------------------------------------------------------------------ value of information (test M)
def test_voi_is_zero_for_a_clear_pass_and_for_a_clear_buy_and_positive_at_the_boundary() -> None:
    clear_pass = voi.decide_analysis(p_bad=0.2, profit_fine=-4, profit_bad=-20, cost_of_analysis=0.05)
    assert not clear_pass.worth_it and clear_pass.value == 0 and "scartare" in clear_pass.reason
    # An analysis costs about half a euro (the model call plus the wait): at 2% risk it would save 0.32 € on
    # average, not enough; at the boundary it saves several euros.
    clear_buy = voi.decide_analysis(p_bad=0.02, profit_fine=12, profit_bad=-20, cost_of_analysis=0.5)
    assert not clear_buy.worth_it and "non copre il costo" in clear_buy.reason
    boundary = voi.decide_analysis(p_bad=0.35, profit_fine=12, profit_bad=-20, cost_of_analysis=0.5)
    assert boundary.worth_it and boundary.resolvable > 0.5 * 1.5
    # the same case is not worth a very expensive check
    assert not voi.decide_analysis(p_bad=0.35, profit_fine=12, profit_bad=-20, cost_of_analysis=50).worth_it
    # robust to out-of-range probabilities
    assert voi.voi_binary(1.5, 10, -5) == voi.voi_binary(1.0, 10, -5)


# ------------------------------------------------------------------ pre-mortem
def facts(**kw: object) -> premortem.PremortemFacts:
    base = dict(
        cost=30.0, resale_low=34.0, resale_mid=48.0, p_authentic=0.9, p_sale_30d=0.7, seller_risk=20,
        comparables=40, sold_comparables=25, photos_analysed=True, label_seen=True,
    )  # fmt: skip
    return premortem.PremortemFacts(**{**base, **kw})  # type: ignore[arg-type]


def test_the_premortem_names_the_three_costliest_ways_to_lose_and_checks_each() -> None:
    risky = facts(
        p_authentic=0.55, label_seen=False, photos_analysed=False, unobserved_parts=("retro", "colletto"),
        seller_risk=70, sold_comparables=2, contradictions=("taglia dichiarata L, etichetta M",),
    )  # fmt: skip
    top = premortem.premortem(risky)
    assert len(top) == 3
    assert [m.expected_loss for m in top] == sorted((m.expected_loss for m in top), reverse=True)
    codes = {m.code for m in top}
    assert "not_authentic" in codes and "seller_does_not_deliver" in codes
    fake = next(m for m in top if m.code == "not_authentic")
    assert fake.check == "unverifiable" and "etichetta non vista" in fake.evidence[0]
    # With a verified label and good evidence the same mode is marked as checked, not erased.
    good = premortem.premortem(facts(positive_auth_signals=("font coerente",)), top=7)
    assert next(m for m in good if m.code == "not_authentic").check == "verified_ok"
    assert premortem.required(40) and not premortem.required(10)


# ------------------------------------------------------------------ probability calibration
def miscalibrated(n: int = 600, seed: int = 4) -> tuple[list[float], list[int]]:
    rng = random.Random(seed)
    p, y = [], []
    for _ in range(n):
        truth = rng.uniform(0.05, 0.95)
        y.append(1 if rng.random() < truth else 0)
        p.append(min(0.99, max(0.01, truth**0.5)))  # overconfident: reports much more than reality
    return p, y


def test_brier_and_ece_measure_what_they_should() -> None:
    assert probcal.brier([1.0, 0.0], [1, 0]) == 0 and probcal.brier([0.5, 0.5], [1, 0]) == 0.25
    perfect = ([0.2] * 50 + [0.8] * 50, [1] * 10 + [0] * 40 + [1] * 40 + [0] * 10)
    assert probcal.ece(*perfect) == pytest.approx(0, abs=1e-9)
    assert probcal.ece([0.9] * 10, [0] * 10) == pytest.approx(0.9)
    assert sum(b.n for b in probcal.reliability(*miscalibrated())) == 600


def test_calibration_is_kept_only_if_it_improves_data_it_did_not_learn_from() -> None:
    p, y = miscalibrated()
    report, model = probcal.evaluate_and_calibrate(p, y)
    assert report.status == "recalibrated" and model is not None
    assert report.brier_after < report.brier_before and report.ece_after < report.ece_before
    assert 0 <= model(0.3) <= model(0.9) <= 1  # monotone
    # already calibrated data is left alone
    rng = random.Random(1)
    good_p = [rng.uniform(0.1, 0.9) for _ in range(600)]
    good_y = [1 if rng.random() < v else 0 for v in good_p]
    r2, m2 = probcal.evaluate_and_calibrate(good_p, good_y)
    assert r2.status in ("calibrated_ok", "kept_raw") and m2 is None
    # not enough outcomes: says so instead of fitting
    few, none = probcal.evaluate_and_calibrate(p[:10], y[:10])
    assert few.status == "not_enough_data" and none is None and "esiti reali" in few.note
    assert probcal.evaluate_and_calibrate([0.5] * 40, [1] * 40)[0].status == "not_enough_data"


def test_isotonic_is_monotone_and_platt_fixes_a_shift() -> None:
    iso = probcal.Isotonic.fit([0.1, 0.2, 0.3, 0.4], [1, 0, 1, 0])
    assert all(iso(a) >= iso(b) - 1e-12 or a < b for a, b in [(0.1, 0.2)]) and iso(0.1) <= iso(0.4) + 1e-9
    p, y = miscalibrated()
    pl = probcal.Platt.fit(p, y)
    assert probcal.brier([pl(v) for v in p], y) < probcal.brier(p, y)


# ------------------------------------------------------------------ drift and false negatives (test N)
def test_a_price_shift_and_a_brand_shift_raise_alarms_and_no_shift_does_not() -> None:
    rng = random.Random(2)
    base = [rng.gauss(30, 6) for _ in range(500)]
    same = [rng.gauss(30, 6) for _ in range(500)]
    moved = [rng.gauss(38, 6) for _ in range(500)]
    assert drift.detect("price", base, same, "prezzi") is None
    alarm = drift.detect("price", base, moved, "prezzi")
    assert alarm is not None and alarm.level == "alarm" and "cambiata molto" in alarm.note
    stat, crit = drift.ks(base, moved)
    assert stat > crit and drift.ks(base, same)[0] < crit
    mix_a = {"rl": 300, "nike": 150, "carhartt": 50}
    assert drift.detect_mix("brands", mix_a, {"rl": 290, "nike": 160, "carhartt": 50}, "brand") is None
    mix_alarm = drift.detect_mix("brands", mix_a, {"rl": 60, "nike": 40, "carhartt": 400}, "brand")
    assert mix_alarm is not None and mix_alarm.level == "alarm"
    assert drift.detect("price", base[:5], moved[:5], "prezzi") is None  # too little data: no verdict


def test_a_discarded_item_that_sold_fast_is_a_false_negative_and_the_vetoes_are_named() -> None:
    D = counterfactual.Discarded
    items = [
        D("a", "WATCHLIST", 20, 14.0, 2, 20, ("photos_missing",)),  # sold in 2 days, profitable: missed
        D("b", "PASS", 20, -3.0, 1, 20, ("loss",)),  # sold, but we saw no profit: right call
        D("c", "WATCHLIST", 20, 12.0, 30, 20, ("photos_missing",)),  # sold too late to count
        D("d", "PASS", 20, 15.0, None, None, ("seller_risk",)),  # still on sale
        D("e", "BUY", 20, 15.0, 1, 20, ()),  # not rejected
        D("f", "PASS", 20, 10.0, 3, 28, ("seller_risk",)),  # sold at a higher price: not comparable
    ]
    r = counterfactual.evaluate(items, min_profit=8.0)
    assert r.rejected == 5 and r.sold_quickly == 2
    assert [d.opportunity_id for d in r.false_negatives] == ["a"] and r.by_veto == {"photos_missing": 1}
    assert r.rate == 0.5
    empty = counterfactual.evaluate([D("x", "PASS", 10, 9.0, None, None)], 5.0)
    assert empty.rate is None and "non sono misurabili" in empty.note


# ------------------------------------------------------------------ champion / challenger and baseline (test L-O)
def cases(n: int = 200, seed: int = 8) -> list[champion.Outcome]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        cost = rng.uniform(8, 50)
        quality = rng.random()  # hidden: the better policy sees it, the worse does not
        profit = cost * (quality * 1.2 - 0.45) + rng.gauss(0, 2)
        out.append(champion.Outcome(f"c{i}", cost, profit, rng.uniform(3, 30), "rl" if i % 2 else "nike"))
    return out


def test_a_challenger_is_promoted_only_when_it_wins_by_a_pre_registered_rule() -> None:
    data = cases()

    def always(c: champion.Outcome) -> bool:
        return True

    def selective(c: champion.Outcome) -> bool:  # an oracle on this data: certainly better
        return c.profit > 0

    win = champion.compare(data, always, selective)
    assert win.promote and win.lower_bound is not None and win.lower_bound > champion.MARGIN
    same = champion.compare(data, always, always)
    assert not same.promote and "non è dimostrato" in same.reason
    worse = champion.compare(data, selective, always)
    assert not worse.promote and worse.champion.mean_per_euro_day > worse.challenger.mean_per_euro_day
    assert not champion.compare(data[:10], always, selective).promote
    rare = champion.compare(data, always, lambda c: c.case_id == "c1")
    assert not rare.promote and "avrebbe comprato solo" in rare.reason
    assert "esito reale" in win.as_dict()["note"]


def test_the_baseline_is_beaten_only_where_it_is_measured_to_be() -> None:
    rng = random.Random(6)
    cand = []
    for i in range(80):
        good = rng.random() < 0.5
        cost = rng.uniform(10, 40)
        resale_net = cost * (1.5 if good else 1.45)  # the median comparables cannot tell them apart
        profit = cost * (0.6 if good else -0.4)  # but the AI (which saw the photos) buys the good ones
        cand.append(baseline.Candidate(f"r{i}", "rl-polo", cost, resale_net, good, profit, 10))
    for i in range(30):  # a segment where the AI is just noise
        cost = rng.uniform(10, 40)
        profit = cost * rng.choice([0.5, -0.4])
        cand.append(
            baseline.Candidate(f"n{i}", "nike-felpa", cost, cost * 1.5, rng.random() < 0.5, profit, 10)
        )
    cand.append(baseline.Candidate("lonely", "rare", 20, None, True, None, None))
    res = {r.segment: r for r in baseline.compare_by_segment(cand)}
    assert not res["rl-polo"].degrade_to_baseline and res["rl-polo"].ai > res["rl-polo"].baseline
    assert res["nike-felpa"].degrade_to_baseline
    assert not res["rare"].degrade_to_baseline and "non misurabile" in res["rare"].reason
    s = baseline.summary(list(res.values()))
    assert s["degraded"] == ["nike-felpa"] and s["measurable"] == 2
    assert "senza esiti reali" in baseline.summary([])["note"]
