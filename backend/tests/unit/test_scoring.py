"""Flip Score, Confidence, Risk, Seller reliability, demand/velocity, offers, explanations."""

from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from app.demand.analysis import analyze_demand, analyze_velocity, demand_level, velocity_bucket
from app.domain.enums import DealTier, DemandLevel, RecommendedAction, RiskLevel, VelocityBucket
from app.profit.offers import build_offer_plan
from app.scoring.confidence import ConfidenceInput, compute_confidence
from app.scoring.flip import (
    DEFAULT_WEIGHTS,
    FlipInput,
    compute_flip_score,
    deal_tier,
    is_ultra_deal,
    normalize_weights,
)
from app.scoring.risk import RiskInput, assess_risk
from app.scoring.seller import SellerProfile, seller_reliability

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def flip_input(**overrides: object) -> FlipInput:
    base = dict(
        discount_vs_market=0.52,
        expected_roi=0.73,
        expected_profit=17.0,
        demand_score=98,
        velocity_score=89,
        risk_score=15,
        condition="very_good",
        info_score=85,
    )
    base.update(overrides)
    return FlipInput(**base)  # type: ignore[arg-type]


def test_the_default_weights_are_the_ones_of_the_brief() -> None:
    assert DEFAULT_WEIGHTS == {
        "profit": 25,
        "roi": 15,
        "demand": 15,
        "price_vs_market": 15,
        "condition": 10,
        "risk": 10,
        "info": 5,
        "sale_time": 5,
    }
    assert sum(DEFAULT_WEIGHTS.values()) == 100


def test_reference_case_scores_high() -> None:
    """52% below market, ROI 73%, strong demand, ~4 days, low risk, clear listing."""
    r = compute_flip_score(flip_input())
    assert 85 <= r.score <= 93
    assert r.tier in (DealTier.EXCEPTIONAL, DealTier.EXCELLENT)
    assert r.penalties == []  # what used to be penalties is now a component or a veto
    assert abs(sum(c["contribution"] for c in r.components.values()) - r.base) < 0.1


def test_cheap_but_fairly_priced_item_is_not_a_deal() -> None:
    """Spec: a 15 EUR hoodie that normally sells for 20 EUR is NOT a great opportunity."""
    r = compute_flip_score(flip_input(discount_vs_market=0.25, expected_roi=-0.05, expected_profit=-1.0))
    assert r.score <= 30
    assert r.cap and r.cap["max"] == 30


def test_undervalued_item_beats_cheaper_fair_item() -> None:
    fair = compute_flip_score(flip_input(discount_vs_market=0.05, expected_roi=0.0, expected_profit=0.0))
    undervalued = compute_flip_score(
        flip_input(discount_vs_market=0.6, expected_roi=1.1, expected_profit=25.0)
    )
    assert undervalued.score > fair.score + 40


@pytest.mark.parametrize(
    "worse",
    [
        {"condition": "satisfactory"},
        {"info_score": 20},
        {"demand_score": 20},
        {"velocity_score": 20},
        {"risk_score": 80},
        {"expected_profit": 5.0},
        {"expected_roi": 0.2},
        {"discount_vs_market": 0.1},
    ],
)
def test_every_component_moves_the_score_the_right_way(worse: dict) -> None:
    assert compute_flip_score(flip_input(**worse)).score < compute_flip_score(flip_input()).score


@pytest.mark.parametrize(
    "field,low,high",
    [
        ("expected_profit", 2.0, 30.0),
        ("expected_roi", 0.05, 1.2),
        ("discount_vs_market", 0.02, 0.6),
        ("demand_score", 10, 95),
        ("velocity_score", 10, 95),
        ("risk_score", 90, 5),
        ("info_score", 5, 95),
    ],
)
def test_the_score_never_falls_when_an_input_improves(field: str, low: float, high: float) -> None:
    """Monotonicity, checked on a fine grid between a bad and a good value of each input."""
    steps = [low + (high - low) * i / 12 for i in range(13)]
    scores = [compute_flip_score(flip_input(**{field: v})).score for v in steps]
    assert scores == sorted(scores), (field, scores)


def test_one_lopsided_metric_cannot_carry_the_score() -> None:
    """A 90% discount on a 3 EUR item: huge discount, tiny profit. The economic pillar mixes in its
    weakest member, so this does not look like a great deal."""
    lopsided = compute_flip_score(flip_input(discount_vs_market=0.9, expected_profit=1.5, expected_roi=0.2))
    balanced = compute_flip_score(flip_input(discount_vs_market=0.3, expected_profit=14.0, expected_roi=0.6))
    assert balanced.score > lopsided.score


def test_the_three_money_components_share_one_pillar() -> None:
    """Their contributions add up to the pillar, never to more than the sum of their weights."""
    r = compute_flip_score(flip_input())
    money = sum(r.components[k]["contribution"] for k in ("profit", "roi", "price_vs_market"))
    assert money <= 55.0 + 0.1


def test_condition_scores_follow_the_condition_scale() -> None:
    order = ["new_with_tags", "new_without_tags", "very_good", "good", "satisfactory"]
    scores = [compute_flip_score(flip_input(condition=c)).components["condition"]["score"] for c in order]
    assert scores == sorted(scores, reverse=True)
    unknown = compute_flip_score(flip_input(condition="unknown")).components["condition"]["score"]
    assert unknown < scores[2]  # an undeclared condition is worth less than a "very good" one


def test_missing_market_value_caps_score() -> None:
    r = compute_flip_score(flip_input(discount_vs_market=None, expected_roi=None, expected_profit=None))
    assert r.score <= 35


def test_weights_are_configurable_and_normalized() -> None:
    w = normalize_weights({"price_vs_market": 60, "roi": 0})
    assert abs(sum(w.values()) - 1) < 1e-9
    assert w["roi"] == 0
    default = compute_flip_score(flip_input(expected_roi=0.05))
    no_roi = compute_flip_score(flip_input(expected_roi=0.05), {"roi": 0})
    assert no_roi.components["roi"]["weight"] == 0
    assert no_roi.score != default.score
    assert normalize_weights({"bogus": 5}) == normalize_weights(None)
    assert set(DEFAULT_WEIGHTS) == set(normalize_weights(None))


def test_weights_saved_under_the_old_names_keep_their_meaning() -> None:
    assert normalize_weights({"undervaluation": 60}) == normalize_weights({"price_vs_market": 60})
    assert normalize_weights({"velocity": 30}) == normalize_weights({"sale_time": 30})
    assert normalize_weights({"freshness": 30, "seller": 30}) == normalize_weights(None)


@pytest.mark.parametrize(
    "score,tier",
    [
        (95, DealTier.EXCEPTIONAL),
        (85, DealTier.EXCELLENT),
        (75, DealTier.GOOD),
        (65, DealTier.MODERATE),
        (40, DealTier.LOW),
    ],
)
def test_tiers(score: int, tier: DealTier) -> None:
    assert deal_tier(score) == tier


def test_ultra_deal_rule() -> None:
    assert is_ultra_deal(91, 81, D("0.61"))
    assert not is_ultra_deal(90, 95, D("1.0"))  # strictly greater than 90
    assert not is_ultra_deal(95, 80, D("1.0"))
    assert not is_ultra_deal(95, 95, D("0.60"))
    assert not is_ultra_deal(95, 95, None)


def test_confidence_is_separate_from_flip() -> None:
    thin = compute_confidence(ConfidenceInput(30, 40, 1, 10, False, True, False, 3))
    rich = compute_confidence(ConfidenceInput(90, 90, 6, 200, True, True, True, 80))
    assert thin.score < 45 < 85 < rich.score


def risk_input(**overrides: object) -> RiskInput:
    base = dict(
        price=D("30"),
        fair_market_value=D("40"),
        brand_counterfeit_risk=0.05,
        seller=SellerProfile(D("4.9"), 200),
        photo_count=5,
        description_length=150,
        identification_confidence=85,
        comparables_used=30,
        market_confidence=80,
        condition="very_good",
    )
    base.update(overrides)
    return RiskInput(**base)  # type: ignore[arg-type]


def test_clean_listing_is_low_risk() -> None:
    r = assess_risk(risk_input())
    assert r.level == RiskLevel.LOW
    assert r.score < 10


def test_counterfeit_signals_raise_risk_with_reasons() -> None:
    r = assess_risk(
        risk_input(
            price=D("40"),
            fair_market_value=D("350"),
            brand_counterfeit_risk=0.55,
            suspicious_terms=("replica",),
            photo_count=1,
            photos_reused_by_other_seller=True,
        )
    )
    assert r.level == RiskLevel.VERY_HIGH
    codes = {f.code for f in r.factors}
    assert {"price_too_low", "suspicious_text", "one_photo", "reused_photos", "counterfeit_brand"} <= codes
    assert all(f.label for f in r.factors)


def test_new_seller_is_not_treated_as_scammer() -> None:
    new = SellerProfile(None, 0)
    r = assess_risk(risk_input(seller=new))
    assert r.score <= 20
    assert any("non necessariamente" in f.label for f in r.factors)
    s = seller_reliability(new, NOW)
    assert 45 <= s.score <= 65
    assert s.level == "new"


def test_seller_reliability_ranks_evidence() -> None:
    veteran = seller_reliability(SellerProfile(D("4.95"), 400), NOW)
    bad = seller_reliability(SellerProfile(D("3.6"), 80), NOW)
    # Rating and reviews are all that is known (account age and sales are not collected).
    assert veteran.score >= 85 and veteran.level == "high"
    assert bad.score < 45
    assert seller_reliability(None, NOW).level == "unknown"


def test_demand_levels_and_smoothing() -> None:
    assert analyze_demand(80, 20).level in (DemandLevel.VERY_HIGH,)
    tiny = analyze_demand(2, 0)
    assert tiny.sell_through_rate < 1.0  # 2/2 is not reported as 100% demand
    assert demand_level(0.1) == DemandLevel.VERY_LOW
    assert analyze_demand(5, 95).level in (DemandLevel.VERY_LOW, DemandLevel.LOW)


def test_velocity_from_sold_samples() -> None:
    fast = analyze_velocity([(2, 1), (3, 1), (1.5, 1), (2.5, 1), (2, 1)], 0.7, 14)
    slow = analyze_velocity([(25, 1), (40, 1), (30, 1)], 0.2, 20)
    assert fast.estimated_days < 7 and fast.score > slow.score
    assert slow.bucket in (VelocityBucket.D15_30, VelocityBucket.D30_PLUS)
    assert velocity_bucket(2) == VelocityBucket.D0_3
    assert velocity_bucket(10) == VelocityBucket.D8_14


def test_offer_engine_spec_like_example() -> None:
    plan = build_offer_plan(D("25"), max_buy=D("24"), good_buy=D("22"), flip_score=75, risk_score=20)
    assert plan.action == RecommendedAction.MAKE_OFFER
    assert plan.suggested_offer is not None and D("19") <= plan.suggested_offer <= D("22.8")


def test_offer_engine_buy_now_on_great_deal() -> None:
    plan = build_offer_plan(D("18"), max_buy=D("40"), good_buy=D("34"), flip_score=92, risk_score=10)
    assert plan.action == RecommendedAction.BUY_NOW
    assert plan.suggested_offer == D("18")


def test_offer_engine_waits_or_skips_when_targets_unreachable() -> None:
    assert build_offer_plan(D("50"), None, None, 60, 10).action == RecommendedAction.WATCH
    assert build_offer_plan(D("50"), None, None, 20, 10).action == RecommendedAction.SKIP
    assert build_offer_plan(D("50"), D("45"), D("40"), 80, 90).action == RecommendedAction.SKIP
    assert (
        build_offer_plan(D("50"), D("45"), D("40"), 80, 10, market_reliable=False).action
        == RecommendedAction.WATCH
    )
