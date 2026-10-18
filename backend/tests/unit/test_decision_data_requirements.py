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
        ({"comparable_tier": "model", "comparables_used": 4, "sold_comparables": 4}, "thin_evidence", V.NEGOTIATE),
        ({"comparable_tier": "line", "comparables_used": 9, "sold_comparables": 2}, "thin_evidence", V.NEGOTIATE),
        (
            {"comparable_tier": "model", "comparables_used": 9, "sold_comparables": 6, "market_confidence": 45},
            "thin_evidence",
            V.NEGOTIATE,
        ),
        ({"comparable_tier": "model", "market_mixed": True, "sold_comparables": 14}, "unstable_market", V.WATCHLIST),
        ({"comparable_tier": "model", "market_trend": "falling", "sold_comparables": 14}, "unstable_market", V.WATCHLIST),
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
        reliability=50, review_count=0, off_platform=True, reused_photos=True, price_too_good=True, seller_known=False
    )
    assert loud.level == "high" and loud.unverified


def test_a_deeply_discounted_card_without_a_seller_is_a_watchlist_item_not_a_pass() -> None:
    comps = sold_market("Nike Air Max 90 scarpe sneakers uomo 42", [58 + i for i in range(12)], model="Air Max 90")
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
    comps = sold_market("Nike giacca uomo M", [29.0, 30.0, 31.0], category="jackets", parent="outerwear", size="M")
    subject = prof("Nike giacca uomo M", 25, status="active", days=0.2, category="jackets", parent="outerwear", size="M")
    scored = select_comparables(subject, comps, NOW)
    prior = SegmentPrior(
        median_price=82.0, p25_price=80.0, p75_price=84.0, sample_size=33, level="brand_category", sold_count=3
    )
    with_prior = estimate_market_value(scored, "very_good", NOW, prior, subject=subject)
    without = estimate_market_value(select_comparables(subject, comps, NOW), "very_good", NOW, None, subject=subject)
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
    comps = sold_market("Nike Air Max 90 scarpe sneakers uomo 42", [58 + i for i in range(12)], model="Air Max 90")
    base = analyse(30, "Nike Air Max 90 scarpe sneakers uomo 42", comps, model="Air Max 90")
    assert base.max_buy_price is not None
    from dataclasses import replace

    from app.opportunities.engine import EconomicTargets, run_analysis

    ctx = replace(_ctx_of(base), buyer_protection_fee=D("6.00"))
    dearer = run_analysis(ctx, comps, NOW, CostProfile(), EconomicTargets())
    assert dearer.max_buy_price is not None and dearer.max_buy_price < base.max_buy_price


def _ctx_of(result):  # the SubjectContext an analysis was run with
    return result.subject
