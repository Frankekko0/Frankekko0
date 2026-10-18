"""What a purchase must rest on: enough comparables of the item's own model/line, a settled market, a read seller.

Regression tests of the pricing audit (PX-03, PX-06, PX-10, TT-05), each a case that used to come out as BUY:
three asking prices or three sales in the whole market, a segment prior inflated by listings nobody paid for,
a ceiling that ignored the buyer protection the listing shows, a seller-less search card read as a scam.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from app.decision.engine import DecisionVerdict as V
from app.decision.engine import decide
from app.intelligence.seller_risk import seller_risk_score
from app.pricing.comparables import select_comparables
from app.pricing.market_value import SegmentPrior, estimate_market_value
from app.profit.calculator import CostProfile, max_buy_price, profit_for
from tests.unit.test_decision_engine import strong
from tests.unit.test_pricing_tiers import NOW, analyse, prof, sold_market

BUYS = (V.BUY, V.STRONG_BUY)


# ------------------------------------------------------------------------- PX-03: the evidence floor
@pytest.mark.parametrize(
    ("overrides", "code", "verdict"),
    [
        ({"comparable_tier": "family"}, "no_same_model_comparables", V.WATCHLIST),
        (
            {"comparable_tier": "model", "comparables_used": 4, "sold_comparables": 4},
            "thin_evidence",
            V.NEGOTIATE,
        ),
        (
            {"comparable_tier": "line", "comparables_used": 9, "sold_comparables": 2},
            "thin_evidence",
            V.NEGOTIATE,
        ),
        (
            {
                "comparable_tier": "model",
                "comparables_used": 9,
                "sold_comparables": 6,
                "market_confidence": 45,
            },
            "thin_evidence",
            V.NEGOTIATE,
        ),
        (
            {"comparable_tier": "model", "market_mixed": True, "sold_comparables": 14},
            "unstable_market",
            V.WATCHLIST,
        ),
        (
            {"comparable_tier": "model", "market_trend": "falling", "sold_comparables": 14},
            "unstable_market",
            V.WATCHLIST,
        ),
        ({"seller_known": False}, "seller_unverified", V.WATCHLIST),
    ],
)
def test_a_good_margin_does_not_buy_without_the_evidence(overrides: dict, code: str, verdict: V) -> None:
    d = decide(strong(**overrides))
    assert d.candidate in BUYS  # the economics alone would buy
    assert d.verdict == verdict and d.verdict not in BUYS
    veto = next(v for v in d.vetoes if v.code == code)
    assert veto.binding and veto.label


def test_the_evidence_floor_lets_a_well_supported_purchase_through() -> None:
    ok = {"comparable_tier": "model", "comparables_used": 5, "sold_comparables": 3, "market_confidence": 50}
    d = decide(strong(comparables_used=5, market_confidence=50, sold_comparables=3, comparable_tier="model"))
    assert d.verdict in BUYS and not [v for v in d.vetoes if v.binding]
    # one comparable, one sale or one point less and it stops
    for key, value in (("comparables_used", 4), ("sold_comparables", 2), ("market_confidence", 49)):
        assert decide(strong(**{**ok, key: value})).verdict not in BUYS


def test_the_family_veto_names_the_missing_same_model_comparables_in_italian() -> None:
    d = decide(strong(comparable_tier="family"))
    assert any("Nessun confronto dello stesso modello" in w for w in d.warnings)


# ------------------------------------------------------------------------------------ TT-05: no seller
def test_a_seller_that_was_not_read_is_unverified_not_a_scam() -> None:
    """A search card has no seller: the default reliability (50), a "new profile" (+10) and a deep discount (+10) made
    70 = HIGH, the binding scam veto, and the deepest discounts were the ones thrown away."""
    unread = seller_risk_score(reliability=50, review_count=0, price_too_good=True, seller_known=False)
    assert unread.unverified and unread.level != "high" and unread.score < 45
    # a real profile that is just as thin is still judged on its evidence
    assert seller_risk_score(reliability=50, review_count=0, price_too_good=True).level == "high"
    # signals that need no profile still count
    loud = seller_risk_score(
        reliability=50,
        review_count=0,
        off_platform=True,
        reused_photos=True,
        price_too_good=True,
        seller_known=False,
    )
    assert loud.level == "high" and loud.unverified


def test_a_deeply_discounted_card_without_a_seller_is_a_watchlist_item_not_a_pass() -> None:
    comps = sold_market(
        "Nike Air Max 90 scarpe sneakers uomo 42", [58 + i for i in range(12)], model="Air Max 90"
    )
    kw = {"model": "Air Max 90"}
    card = analyse(20, "Nike Air Max 90 scarpe sneakers uomo 42", comps, seller=False, **kw)
    assert card.decision.candidate in BUYS
    assert card.decision.verdict == V.WATCHLIST
    codes = {v.code for v in card.decision.vetoes}
    assert "seller_unverified" in codes and "seller_scam_risk" not in codes
    # the same listing with the seller read is a purchase
    page = analyse(20, "Nike Air Max 90 scarpe sneakers uomo 42", comps, seller=True, **kw)
    assert page.decision.verdict in BUYS


# ------------------------------------------------------------------------- PX-06: the segment prior
def test_d8c_a_prior_made_of_unsold_asks_does_not_move_the_price() -> None:
    """3 sold at 30, and a brand-wide segment whose 'prices' are 30 removed listings asking 80-84: the prior lifted the
    estimate from 30 to 51 (+70%) and a EUR 25 item became a BUY."""
    comps = sold_market(
        "Nike giacca uomo M", [29.0, 30.0, 31.0], category="jackets", parent="outerwear", size="M"
    )
    subject = prof(
        "Nike giacca uomo M", 25, status="active", days=0.2, category="jackets", parent="outerwear", size="M"
    )
    scored = select_comparables(subject, comps, NOW)
    prior = SegmentPrior(
        median_price=82.0,
        p25_price=80.0,
        p75_price=84.0,
        sample_size=33,
        level="brand_category",
        sold_count=3,
    )
    with_prior = estimate_market_value(scored, "very_good", NOW, prior, subject=subject)
    without = estimate_market_value(
        select_comparables(subject, comps, NOW), "very_good", NOW, None, subject=subject
    )
    assert float(with_prior.fair_market_value) == float(without.fair_market_value)
    assert abs(float(with_prior.fair_market_value) - 30) / 30 < 0.10
    r = analyse(25, "Nike giacca uomo M", comps, category="jackets", parent="outerwear", size="M")
    assert r.decision.verdict not in BUYS


# --------------------------------------------------------------------- PX-10: the ceiling and the haircut
def test_the_ceiling_uses_the_buyer_protection_the_listing_shows() -> None:
    """With a buyer protection of 4.00 on a EUR 20 listing (the formula says 1.70) the 'maximum price' still gave a
    profit of 9.13 and an ROI of 0.30 against targets of 10 and 0.40."""
    costs, sale = CostProfile(), D("40")
    shown = D("4.00")
    ceiling = max_buy_price(
        sale, costs, D("10"), D("0.40"), D("3.49"), listing_buyer_protection=shown, listing_price=D("20")
    )
    plain = max_buy_price(sale, costs, D("10"), D("0.40"), D("3.49"))
    assert ceiling is not None and plain is not None and ceiling < plain
    extra = shown - (costs.buyer_protection_fixed + costs.buyer_protection_pct * D("20"))
    bp_at_ceiling = costs.buyer_protection_fixed + costs.buyer_protection_pct * ceiling + extra
    got = profit_for(ceiling, sale, costs, D("3.49"), bp_at_ceiling)
    assert got.net_profit >= D("10") and got.roi >= D("0.40")
    # and the dearer price would not meet them
    over = ceiling + D("1")
    bp_over = costs.buyer_protection_fixed + costs.buyer_protection_pct * over + extra
    worse = profit_for(over, sale, costs, D("3.49"), bp_over)
    assert worse.net_profit < D("10") or worse.roi < D("0.40")
    # the formula's own listing changes nothing
    same = max_buy_price(
        sale,
        costs,
        D("10"),
        D("0.40"),
        D("3.49"),
        listing_buyer_protection=costs.buyer_protection_fixed + costs.buyer_protection_pct * D("20"),
        listing_price=D("20"),
    )
    assert same == plain


def test_the_analysis_ceiling_follows_the_listings_buyer_protection() -> None:
    comps = sold_market(
        "Nike Air Max 90 scarpe sneakers uomo 42", [58 + i for i in range(12)], model="Air Max 90"
    )
    base = analyse(30, "Nike Air Max 90 scarpe sneakers uomo 42", comps, model="Air Max 90")
    assert base.max_buy_price is not None
    from dataclasses import replace

    from app.opportunities.engine import EconomicTargets, run_analysis

    ctx = replace(_ctx_of(base), buyer_protection_fee=D("6.00"))
    dearer = run_analysis(ctx, comps, NOW, CostProfile(), EconomicTargets())
    assert dearer.max_buy_price is not None and dearer.max_buy_price < base.max_buy_price


def _ctx_of(result):  # the SubjectContext an analysis was run with
    return result.subject


# --------------------------------------------------------------- PX-03: the range of a thin estimate
def _coverage(n: int, trials: int = 400) -> tuple[float, float]:
    """Share of fresh sales that fall inside [quick, optimistic] when n comparables set the price (seeded)."""
    import math
    import random
    from datetime import timedelta

    from app.pricing.comparables import ScoredComparable

    rng = random.Random(7)

    def draw() -> float:
        return 40 * math.exp(rng.gauss(0, 0.20))

    inside = total = 0
    width = []
    for _ in range(trials):
        comps = []
        for _ in range(n):
            price, age = draw(), rng.uniform(1, 90)
            item = prof(
                "t", round(price, 2), brand="b", category="c", parent="p", model="m", size="M", days=age
            )
            comps.append(
                ScoredComparable(
                    item=item, similarity=0.95, weight=0.9 * math.exp(-age / 75) * 1.5, adjusted_price=price
                )
            )
        m = estimate_market_value(comps, "very_good", NOW + timedelta(seconds=0))
        if m.fair_market_value is None:  # a split sample (two price levels) leaves too few to estimate
            continue
        q, o = float(m.quick_sale_price), float(m.optimistic_sale_price)
        width.append((o - q) / float(m.fair_market_value))
        for _ in range(5):
            total += 1
            inside += q <= draw() <= o
    return inside / total, sum(width) / len(width)


def test_the_range_of_a_thin_estimate_is_wider_so_that_half_of_the_next_sales_fall_inside() -> None:
    """A fixed band caught 44% of the next sales at n=3 and 50% at n=40: the error of a median of three prices is part
    of the spread of the next sale."""
    cover3, width3 = _coverage(3)
    cover40, width40 = _coverage(40)
    assert width3 > width40 * 1.08
    assert 0.47 <= cover3 <= 0.57 and 0.45 <= cover40 <= 0.57


# ------------------------------------------------------------------ PX-04: a slow, steady slide
def _drift_market(monthly: float, n: int, trials: int = 250, seed: int = 3) -> tuple[float, float]:
    """(mean error of the estimate against today's level, share of estimates marked as easing), for sold prices
    that move ``monthly`` a month (today's level is 60), n of them over the last 110 days (seeded)."""
    import math
    import random
    import statistics

    from app.pricing.comparables import ScoredComparable

    rng = random.Random(seed)
    errors, eased = [], 0
    for _ in range(trials):
        comps = []
        for _ in range(n):
            age = rng.uniform(1, 110)
            price = 60 * math.exp(math.log(1 + monthly) * (-age / 30)) * math.exp(rng.gauss(0, 0.10))
            item = prof(
                "t", round(price, 2), brand="b", category="c", parent="p", model="m", size="M", days=age
            )
            comps.append(
                ScoredComparable(
                    item=item, similarity=0.95, weight=0.9 * math.exp(-age / 45) * 1.5, adjusted_price=price
                )
            )
        m = estimate_market_value(comps, "very_good", NOW)
        if m.fair_market_value is None:
            continue
        errors.append(float(m.fair_market_value) / 60 - 1)
        eased += m.trend == "easing"
    return statistics.mean(errors), eased / len(errors)


def test_a_slow_steady_decline_is_brought_to_todays_level_and_a_flat_market_is_left_alone() -> None:
    """Sales sliding 10% a month leave the weighted median ~11% above today (measured); a significant downward slope over
    enough sales and time brings it to today. A flat market, and a rising one (prudent by design), are not touched."""
    bias, eased = _drift_market(-0.10, 12)
    assert bias < 0.07 and eased > 0.3  # +11.0% without the correction, +4.7% with it
    flat_bias, flat_eased = _drift_market(0.0, 12)
    assert abs(flat_bias) < 0.02 and flat_eased < 0.05
    _, rising_eased = _drift_market(0.05, 12)
    assert rising_eased == 0
