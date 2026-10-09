"""The decision engine: verdicts, the four separate scores, vetoes, ranking and capital allocation.

Cases A, B (decision side), C, E, G and H of the product brief, STRONG BUY impossible with high risk
or thin data, and an exact capital allocation checked against brute force.
"""

import itertools
import random
from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.ai.deal_analyst import DealContext, clamp_to_decision
from app.decision.allocation import Candidate, CapitalRules, allocate_capital
from app.decision.completeness import CompletenessInput, compute_completeness
from app.decision.engine import (
    DecisionInput,
    action_for,
    decide,
    rank_value,
)
from app.decision.engine import (
    DecisionVerdict as V,
)
from app.decision.ranking import Ranked, rank_opportunities
from app.domain.enums import RecommendedAction, Verdict
from app.profit.calculator import CostProfile, max_buy_price, profit_for
from app.scoring.flip import FlipInput, compute_flip_score


def strong(**overrides: object) -> DecisionInput:
    """A listing that clears every requirement: Ralph Lauren sweatshirt at 18 EUR (case A)."""
    base = dict(
        price=D("18"),
        expected_profit=D("17"),
        conservative_profit=D("9"),
        expected_roi=D("0.73"),
        max_buy_price=D("22"),
        suggested_offer=D("16"),
        min_profit=D("10"),
        min_roi=D("0.40"),
        flip_score=88,
        confidence_score=82,
        risk_score=15,
        completeness_score=90,
        data_quality="ok",
        comparables_used=14,
        market_confidence=78,
        identification_confidence=90,
        model_known=True,
        condition_known=True,
        photo_count=6,
        acquisition_cost_verified=True,
        photos_analysed=True,
        label_photo_seen=True,
        brand_counterfeit_risk=0.12,
        authenticity_verdict="probably_authentic",
        discount_vs_market=0.5,
        demand_level="high",
        risk_adjusted_profit=11.0,
    )
    base.update(overrides)
    return DecisionInput(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ case A
def test_case_a_strong_buy_only_when_everything_is_verified() -> None:
    d = decide(strong())
    assert d.verdict == V.STRONG_BUY
    assert all(r.met for r in d.strong_buy_requirements)
    assert d.threshold_price == D("22")
    assert d.legacy_verdict == Verdict.BUY
    assert d.reasons[0].startswith("Tutti i requisiti verificati")


@pytest.mark.parametrize(
    "gap,requirement",
    [
        ({"model_known": False}, "model"),
        ({"acquisition_cost_verified": False}, "total_cost"),
        ({"data_quality": "limited", "comparables_used": 5}, "comparables"),
        ({"comparables_used": 6}, "comparables"),
        ({"market_confidence": 40}, "comparables"),
        ({"condition_known": False}, "condition"),
        ({"photos_analysed": False}, "photos_checked"),
        ({"label_photo_seen": None}, "label"),
        ({"label_photo_seen": False}, "label"),
        ({"photo_count": 2}, "photos"),
        ({"identification_confidence": 55}, "model"),
        ({"conservative_profit": D("-1")}, "conservative_profit"),
    ],
)
def test_one_missing_requirement_is_enough_to_stop_a_strong_buy(gap: dict, requirement: str) -> None:
    d = decide(strong(**gap))
    assert d.verdict != V.STRONG_BUY
    unmet = [r.code for r in d.strong_buy_requirements if not r.met]
    assert requirement in unmet
    if d.verdict == V.BUY:  # the missing piece is listed, so the user knows what to ask for
        assert any(m["code"] == f"strong_buy:{requirement}" for m in d.missing_info)


# ------------------------------------------------------------------ vetoes cannot be bought back
def test_a_huge_margin_cannot_buy_back_counterfeit_risk() -> None:
    d = decide(strong(authenticity_verdict="counterfeit_risk", flip_score=97, expected_roi=D("3.0")))
    assert d.verdict == V.PASS
    assert any(v.code == "counterfeit_risk" and v.binding for v in d.vetoes)


def test_suspicious_wording_is_a_veto() -> None:
    assert decide(strong(suspicious_terms=True)).verdict == V.PASS


def test_a_risky_brand_without_proof_is_never_bought() -> None:
    for verdict in ("not_verifiable", "uncertain", None):
        d = decide(strong(brand_counterfeit_risk=0.45, authenticity_verdict=verdict))
        assert d.verdict == V.WATCHLIST, verdict
        assert any(v.code == "authenticity_unproven" for v in d.vetoes)
    proven = decide(strong(brand_counterfeit_risk=0.45, authenticity_verdict="probably_authentic"))
    assert proven.verdict == V.STRONG_BUY


def test_very_high_risk_is_a_veto() -> None:
    assert decide(strong(risk_score=80)).verdict == V.PASS


def test_a_score_of_95_with_confidence_30_is_not_acted_on() -> None:
    d = decide(strong(flip_score=95, confidence_score=30))
    assert d.verdict == V.WATCHLIST
    assert any(v.code == "low_confidence" for v in d.vetoes)


@pytest.mark.parametrize(
    "status,expected",
    [("to_verify", V.WATCHLIST), ("reserved", V.WATCHLIST), ("sold", V.PASS), ("removed", V.PASS)],
)
def test_an_unavailable_listing_is_never_a_buy(status: str, expected: V) -> None:
    assert decide(strong(status=status)).verdict == expected


def test_strong_buy_is_impossible_when_risk_is_high_or_data_is_thin() -> None:
    """Randomised check: whatever else is true, these combinations never give a STRONG BUY."""
    rng = random.Random(7)
    for _ in range(3000):
        inp = strong(
            flip_score=rng.randint(0, 100),
            confidence_score=rng.randint(0, 100),
            risk_score=rng.randint(0, 100),
            completeness_score=rng.randint(0, 100),
            data_quality=rng.choice(["ok", "limited", "insufficient"]),
            comparables_used=rng.randint(0, 30),
            market_confidence=rng.randint(0, 100),
            photo_count=rng.randint(0, 10),
            model_known=rng.random() > 0.3,
            condition_known=rng.random() > 0.3,
            acquisition_cost_verified=rng.random() > 0.3,
            photos_analysed=rng.random() > 0.3,
            label_photo_seen=rng.choice([True, False, None]),
            authenticity_verdict=rng.choice(
                ["probably_authentic", "uncertain", "not_verifiable", "counterfeit_risk", None]
            ),
            brand_counterfeit_risk=rng.choice([0.05, 0.2, 0.4, 0.6]),
            suspicious_terms=rng.random() > 0.9,
            status=rng.choice(["active", "active", "active", "to_verify", "sold"]),
            expected_roi=D(str(round(rng.uniform(-0.2, 3), 2))),
            expected_profit=D(str(round(rng.uniform(-5, 60), 2))),
            conservative_profit=D(str(round(rng.uniform(-8, 40), 2))),
        )
        d = decide(inp)
        if d.verdict == V.STRONG_BUY:
            assert inp.risk_score < 35
            assert inp.data_quality == "ok" and inp.completeness_score >= 70
            assert inp.authenticity_verdict != "counterfeit_risk" and not inp.suspicious_terms
            assert inp.brand_counterfeit_risk < 0.3 or inp.authenticity_verdict == "probably_authentic"
            assert inp.status == "active" and inp.confidence_score >= 70
            assert inp.model_known and inp.condition_known and inp.acquisition_cost_verified
            assert inp.photos_analysed and inp.label_photo_seen is True
        if d.verdict in (V.STRONG_BUY, V.BUY):
            assert inp.risk_score < 75 and inp.authenticity_verdict != "counterfeit_risk"


# ------------------------------------------------------------------ states and the other verdicts
def test_case_g_no_reliable_market_is_insufficient_evidence_not_a_low_score() -> None:
    d = decide(
        strong(
            data_quality="insufficient",
            insufficient_reason="Solo 1 comparabili utilizzabili su 3 annunci simili trovati.",
            expected_profit=None,
            conservative_profit=None,
            expected_roi=None,
            max_buy_price=None,
            flip_score=30,
        )
    )
    assert d.verdict == V.INSUFFICIENT_EVIDENCE
    assert "comparabili" in d.warnings[0]
    assert d.threshold_price is None and d.rank_value == 0.0
    assert d.action == RecommendedAction.WATCH


def test_a_listing_that_barely_can_be_read_is_insufficient_evidence() -> None:
    d = decide(strong(completeness_score=20))
    assert d.verdict == V.INSUFFICIENT_EVIDENCE
    assert "troppo poco" in d.warnings[0]


def test_negotiate_names_the_price_at_which_it_works() -> None:
    d = decide(
        strong(
            price=D("30"),
            max_buy_price=D("25"),
            suggested_offer=D("24"),
            expected_profit=D("6"),
            expected_roi=D("0.2"),
            conservative_profit=D("1"),
        )
    )
    assert d.verdict == V.NEGOTIATE
    assert d.threshold_price == D("25")
    assert d.action == RecommendedAction.MAKE_OFFER
    assert any("€25" in r for r in d.reasons)


def test_a_threshold_the_seller_will_not_reach_is_not_a_negotiation() -> None:
    d = decide(
        strong(
            price=D("30"),
            max_buy_price=D("15"),
            expected_profit=D("-4"),
            expected_roi=D("-0.1"),
            conservative_profit=D("-9"),
            flip_score=30,
        )
    )
    assert d.verdict == V.PASS


def test_promising_but_too_expensive_is_watchlist() -> None:
    d = decide(
        strong(
            price=D("30"),
            max_buy_price=D("21"),
            expected_profit=D("-1"),
            expected_roi=D("0"),
            conservative_profit=D("-5"),
            flip_score=40,
        )
    )
    assert d.verdict == V.WATCHLIST
    assert any("sotto" in r for r in d.reasons)


def test_buy_below_strong_when_a_requirement_is_missing() -> None:
    d = decide(strong(photo_count=2, flip_score=70, confidence_score=60, completeness_score=60))
    assert d.verdict == V.BUY
    assert d.candidate == V.BUY


# ------------------------------------------------------------------ case E: costs eat the margin
def test_case_e_the_margin_absorbed_by_costs_is_a_pass() -> None:
    profile = CostProfile()
    price, resale = D("15"), D("23")
    result = profit_for(price, resale, profile, listing_shipping=D("3.49"))
    cap = max_buy_price(resale, profile, D("10"), D("0.40"), D("3.49"))
    flip = compute_flip_score(
        FlipInput(
            discount_vs_market=float((resale - price) / resale),
            expected_roi=float(result.roi),
            expected_profit=float(result.net_profit),
            demand_score=55,
            velocity_score=55,
            risk_score=20,
            condition="very_good",
            info_score=80,
        )
    )
    assert result.net_profit < D("3")  # protection, shipping and fees eat most of the 8 EUR
    d = decide(
        strong(
            price=price,
            expected_profit=result.net_profit,
            expected_roi=result.roi,
            conservative_profit=result.net_profit - D("4"),
            max_buy_price=cap,
            flip_score=flip.score,
            confidence_score=70,
            risk_adjusted_profit=float(result.net_profit) * 0.6,
        )
    )
    assert d.verdict == V.PASS
    assert d.action == RecommendedAction.SKIP


# ------------------------------------------------------------------ case C and B (decision side)
def test_case_c_signed_jumper_two_photos_no_label_is_never_a_buy() -> None:
    comp = compute_completeness(
        CompletenessInput(
            brand_known=True,
            model_known=False,
            size_known=True,
            condition_known=True,
            photo_count=2,
            description_length=30,
            shipping_known=True,
            seller_known=True,
            label_photo_seen=False,
            photos_analysed=False,
            identification_confidence=55,
        )
    )
    d = decide(
        strong(
            price=D("40"),
            brand_counterfeit_risk=0.45,
            authenticity_verdict="not_verifiable",
            photo_count=2,
            model_known=False,
            completeness_score=comp.score,
            missing_info=tuple(comp.missing),
            confidence_score=55,
            identification_confidence=55,
        )
    )
    assert d.verdict not in (V.STRONG_BUY, V.BUY)
    labels = " ".join(m["label"] for m in d.missing_info)
    assert "Etichetta non fotografata" in labels and "Poche foto (2)" in labels
    text = " ".join(d.reasons + d.warnings).lower()
    assert "autentic" not in text.replace("autenticità", "")  # it never claims the item is authentic


def test_case_b_condition_worse_than_declared_is_said_and_priced_in() -> None:
    d = decide(strong(condition_downgraded=True))
    assert any("peggiori di quelle dichiarate" in w for w in d.warnings)


# ------------------------------------------------------------------ case H
def test_case_h_a_sure_15_euro_can_beat_an_uncertain_20() -> None:
    a = strong(  # 20 EUR profit, high uncertainty, low demand
        expected_profit=D("20"),
        confidence_score=40,
        demand_level="low",
        risk_score=45,
        flip_score=0,
        risk_adjusted_profit=20 * 0.3 * 0.9,
    )
    b = strong(  # 15 EUR profit, low uncertainty, demand supported
        expected_profit=D("15"),
        confidence_score=85,
        demand_level="high",
        risk_score=12,
        flip_score=0,
        risk_adjusted_profit=15 * 0.8 * 0.97,
    )
    flip_a = compute_flip_score(FlipInput(0.35, 0.8, 20.0, 25, 30, 45, "good", 55))
    flip_b = compute_flip_score(FlipInput(0.35, 0.7, 15.0, 80, 85, 12, "very_good", 90))
    assert flip_b.score > flip_a.score  # the Flip Score already prefers B
    da = decide(replace(a, flip_score=flip_a.score))
    db = decide(replace(b, flip_score=flip_b.score))
    assert db.rank_value > da.rank_value
    ranked = rank_opportunities([Ranked("A", da), Ranked("B", db)])
    assert [r.id for r in ranked] == ["B", "A"]


def test_rank_value_discounts_the_uncertain_and_ignores_missing_values() -> None:
    assert rank_value(10.0, 100) > rank_value(10.0, 50) > rank_value(10.0, 0) > 0
    assert rank_value(None, 90) == 0.0


def test_ranking_puts_a_verdict_above_a_bigger_number_and_breaks_ties_by_id() -> None:
    buy = decide(strong(risk_adjusted_profit=3.0))
    watch = decide(strong(risk_adjusted_profit=50.0, status="to_verify"))
    same1, same2 = decide(strong()), decide(strong())
    ranked = rank_opportunities([Ranked("w", watch), Ranked("b", buy)])
    assert [r.id for r in ranked] == ["b", "w"]
    assert [r.id for r in rank_opportunities([Ranked("z", same1), Ranked("a", same2)])] == ["a", "z"]


# ------------------------------------------------------------------ vocabulary and actions
def test_legacy_verdicts_and_actions_follow_the_decision() -> None:
    legacy = {
        V.STRONG_BUY: Verdict.BUY,
        V.BUY: Verdict.BUY,
        V.NEGOTIATE: Verdict.CONSIDER,
        V.WATCHLIST: Verdict.CONSIDER,
        V.PASS: Verdict.SKIP,
        V.INSUFFICIENT_EVIDENCE: Verdict.SKIP,
    }
    assert {v: v.legacy for v in V} == legacy
    assert action_for(V.STRONG_BUY, RecommendedAction.BUY_NOW) == RecommendedAction.BUY_NOW
    assert action_for(V.BUY, RecommendedAction.WATCH) == RecommendedAction.MAKE_OFFER
    assert action_for(V.NEGOTIATE, None) == RecommendedAction.MAKE_OFFER
    assert action_for(V.WATCHLIST, RecommendedAction.BUY_NOW) == RecommendedAction.WATCH
    assert action_for(V.PASS, RecommendedAction.MAKE_OFFER) == RecommendedAction.SKIP
    assert V.INSUFFICIENT_EVIDENCE.label == "INSUFFICIENT EVIDENCE" and V.STRONG_BUY.label == "STRONG BUY"


def test_the_decision_is_json_ready_and_keeps_the_four_scores_apart() -> None:
    import json

    d = decide(strong()).as_dict()
    json.dumps(d)
    assert set(d["scores"]) == {"flip", "confidence", "risk", "completeness"}
    assert d["rules_version"] == "decision-v1" and d["verdict"] == "STRONG_BUY"


def test_a_language_model_may_lower_the_verdict_but_never_raise_it() -> None:
    ctx = DealContext(
        title="x",
        condition="good",
        listing_price=D("10"),
        total_acquisition_cost=D("14"),
        demand_level="medium",
        sell_through_rate=0.3,
        estimated_days_to_sell=10,
        flip_score=50,
        confidence_score=50,
        risk_score=30,
        decision_verdict="WATCHLIST",
    )
    assert clamp_to_decision(ctx, Verdict.BUY) == Verdict.CONSIDER
    assert clamp_to_decision(ctx, Verdict.SKIP) == Verdict.SKIP
    assert (
        clamp_to_decision(ctx.model_copy(update={"decision_verdict": "STRONG_BUY"}), Verdict.BUY)
        == Verdict.BUY
    )
    assert clamp_to_decision(ctx.model_copy(update={"decision_verdict": None}), Verdict.BUY) == Verdict.BUY


# ------------------------------------------------------------------ completeness
def test_completeness_is_its_own_score() -> None:
    full = compute_completeness(CompletenessInput(True, True, True, True, 6, 200, True, True, True, True, 90))
    empty = compute_completeness(CompletenessInput(False, False, False, False, 0, 0, False, False))
    assert full.score >= 95 and full.missing == []
    assert empty.score <= 5 and len(empty.missing) >= 8
    assert {m.blocks for m in empty.missing} <= {"strong_buy", "buy", "confidence"}


def test_an_unverifiable_label_is_half_a_label_and_is_listed() -> None:
    seen = compute_completeness(CompletenessInput(True, True, True, True, 6, 200, True, True, True, True, 90))
    unknown = compute_completeness(
        CompletenessInput(True, True, True, True, 6, 200, True, True, None, True, 90)
    )
    absent = compute_completeness(
        CompletenessInput(True, True, True, True, 6, 200, True, True, False, True, 90)
    )
    assert seen.score > unknown.score > absent.score
    assert [m.label for m in unknown.missing] == ["Etichetta non verificabile"]
    assert [m.label for m in absent.missing] == ["Etichetta non fotografata"]


# ------------------------------------------------------------------ capital allocation (exact)
def cand(i: str, cost: str, value: float, verdict: V = V.BUY, risk: int = 10) -> Candidate:
    return Candidate(i, D(cost), value, verdict, risk)


def brute_force(cands: list[Candidate], rules: CapitalRules) -> float:
    best = 0.0
    for k in range(len(cands) + 1):
        if rules.max_items is not None and k > rules.max_items:
            break
        for combo in itertools.combinations(cands, k):
            if sum(c.cost for c in combo) <= rules.budget:
                best = max(best, sum(c.value for c in combo))
    return best


def test_two_good_purchases_beat_ten_small_ones_inside_the_budget() -> None:
    pool = [cand("big1", "40", 30.0), cand("big2", "40", 28.0)] + [cand(f"s{i}", "8", 3.0) for i in range(10)]
    out = allocate_capital(pool, CapitalRules(budget=D("80")))
    assert set(out.ids) == {"big1", "big2"}
    assert out.total_cost == D("80") and out.left == D("0") and out.total_value == 58.0


def test_the_allocation_is_exact_not_greedy() -> None:
    # Greedy by value per euro takes "a" (9 EUR, 10 back) and then cannot afford "b" or "c".
    pool = [cand("a", "9", 10.0), cand("b", "8", 8.5), cand("c", "8", 8.5)]
    out = allocate_capital(pool, CapitalRules(budget=D("16")))
    assert set(out.ids) == {"b", "c"} and out.total_value == 17.0


def test_the_allocation_matches_brute_force_on_random_sets() -> None:
    rng = random.Random(11)
    for _ in range(60):
        n = rng.randint(1, 9)
        pool = [cand(f"c{i}", str(rng.randint(3, 60)), round(rng.uniform(0.5, 30), 2)) for i in range(n)]
        rules = CapitalRules(
            budget=D(rng.randint(10, 150)),
            max_items=rng.choice([None, 1, 2, 3]),
            max_per_item=rng.choice([None, D("50")]),
        )
        eligible = [c for c in pool if rules.max_per_item is None or c.cost <= rules.max_per_item]
        out = allocate_capital(pool, rules)
        assert out.total_cost <= rules.budget
        assert rules.max_items is None or len(out.selected) <= rules.max_items
        assert abs(out.total_value - brute_force(eligible, rules)) < 0.011


def test_the_allocation_only_spends_on_purchases_and_says_why_not_the_rest() -> None:
    pool = [
        cand("ok", "20", 9.0),
        cand("watch", "10", 20.0, V.WATCHLIST),
        cand("risky", "10", 12.0, risk=70),
        cand("huge", "90", 40.0),
        cand("dear", "60", 25.0),
        cand("meh", "5", 0.0),
    ]
    out = allocate_capital(pool, CapitalRules(budget=D("100"), max_per_item=D("50"), max_risk=50))
    assert out.ids == ["ok"]
    reasons = dict(out.rejected)
    assert "non è un acquisto" in reasons["watch"]
    assert reasons["risky"] == "rischio sopra il limite"
    assert reasons["huge"] == "oltre il massimo per articolo"
    assert reasons["dear"] == "oltre il massimo per articolo"
    assert reasons["meh"] == "valore atteso troppo basso"


def test_negotiated_purchases_compete_only_when_asked() -> None:
    pool = [cand("n", "20", 9.0, V.NEGOTIATE)]
    assert allocate_capital(pool, CapitalRules(budget=D("50"))).ids == []
    assert allocate_capital(pool, CapitalRules(budget=D("50"), include_negotiate=True)).ids == ["n"]


def test_a_budget_in_cents_is_never_exceeded() -> None:
    pool = [cand("a", "10.01", 5.0), cand("b", "10.01", 5.0)]
    out = allocate_capital(pool, CapitalRules(budget=D("20.00")))
    assert len(out.selected) == 1 and out.total_cost <= D("20.00")
