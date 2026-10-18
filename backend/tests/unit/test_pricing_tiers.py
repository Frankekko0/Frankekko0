"""Comparables in tiers: a price is built from the item's own model or line, never from the rest of the brand.

Each case is a scenario of the pricing audit (PX-01 ... PX-04, MODEL-007, KIDS-002), run through the real
engine (``run_analysis``) with in-memory listings: before the fix the engine called most of them BUY or STRONG
BUY on prices of other lines, other heights of the shoe, children's items or a market that no longer exists.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest

from app.decision.engine import DecisionVerdict as V
from app.opportunities.engine import EconomicTargets, SubjectContext, run_analysis
from app.pricing.comparables import (
    TIER_FAMILY,
    TIER_LINE,
    TIER_MODEL,
    ItemProfile,
    select_comparables,
    subject_kind,
)
from app.pricing.lines import audience_of, line_overlap, line_tokens, profile_extras, same_line, variant_of
from app.pricing.market_value import estimate_market_value
from app.pricing.stats import cluster_prices
from app.profit.calculator import CostProfile
from app.scoring.seller import SellerProfile

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
VISION_OK = {"analyzer": "claude_vision", "photo_quality": {"has_label_photo": True}, "defects": []}
BUYS = (V.BUY, V.STRONG_BUY)

AM90 = "Nike Air Max 90 scarpe sneakers uomo 42"
REV = "Nike Revolution 6 scarpe running uomo 42"


def prof(
    title: str,
    price: float,
    *,
    brand: str | None = "nike",
    category: str | None = "sneakers",
    parent: str | None = "footwear",
    model: str | None = None,
    status: str = "sold",
    days: float = 10,
    condition: str = "very_good",
    size: str | None = "EU 42",
    gender: str | None = "men",
    with_id: bool = False,
    seen_days: float | None = None,
) -> ItemProfile:
    """A listing seen ``days`` ago (sold ``days`` ago; an ask is online since ``days``, last seen ``seen_days``)."""
    seen = NOW - timedelta(days=days if seen_days is None else seen_days)
    sold = status == "sold"
    return ItemProfile(
        id=uuid.uuid4() if with_id else None,
        title=title,
        price=D(str(price)),
        brand=brand,
        category=category,
        parent_category=parent,
        model=model,
        condition=condition,
        size=size,
        gender=gender,
        color="white",
        status=status,
        published_at=seen - timedelta(days=5 if sold else days / 2),
        sold_at=seen if sold else None,
        last_seen_at=seen,
        **profile_extras(title, gender, category, parent),
    )


def sold_market(
    title: str, prices: list[float], *, ages: list[float] | None = None, **kw: Any
) -> list[ItemProfile]:
    ages = ages or [3 + i for i in range(len(prices))]
    return [prof(title, p, days=a, **kw) for p, a in zip(prices, ages, strict=True)]


def analyse(
    price: float,
    title: str,
    comps: list[ItemProfile],
    *,
    seller: bool = True,
    shipping: float | None = 3.49,
    **kw: Any,
):
    subject = prof(title, price, status="active", days=0.2, with_id=True, **kw)
    ctx = SubjectContext(
        profile=subject,
        description_length=160,
        photo_count=6,
        favourite_count=4,
        listing_age_hours=6,
        shipping_fee=D(str(shipping)) if shipping is not None else None,
        brand_name=(subject.brand or "").title() or None,
        brand_counterfeit_risk=0.1,
        category_baseline_days=14,
        identification={},
        identification_confidence=88,
        seller=SellerProfile(D("4.9"), 80) if seller else None,
        description="Scarpe in ottime condizioni, indossate poche volte, suola pulita, spedizione tracciata.",
        vision=VISION_OK,
    )
    return run_analysis(ctx, comps, NOW, CostProfile(), EconomicTargets())


AM90_PRICES = [
    62,
    64,
    66,
    68,
    68,
    70,
    70,
    70,
    72,
    72,
    74,
    75,
    75,
    76,
    78,
    78,
    80,
    80,
    82,
    85,
    66,
    69,
    71,
    73,
    77,
]
REV_PRICES = [14, 15, 16, 17, 18, 18, 19, 20]


def air_max() -> list[ItemProfile]:
    return sold_market(AM90, [float(p) for p in AM90_PRICES], model="Air Max 90")


# ---------------------------------------------------------------------------------- PX-01: the line is a gate
def test_b1_a_line_the_catalog_does_not_know_is_not_priced_with_another_line() -> None:
    """Nike Revolution 6 at 30 next to 25 sold Air Max 90 (~71): FMV 72, BUY flip 96. It resells at ~17."""
    r = analyse(30, REV, air_max())
    assert r.market.fair_market_value is None
    assert r.data_quality == "insufficient" and r.decision.verdict == V.INSUFFICIENT_EVIDENCE
    assert "stessa linea" in r.insufficient_reason and "dati insufficienti" in r.insufficient_reason
    assert r.confidence.score < 50
    # the other line is reference, labelled and kept out of the price
    assert r.market.reference is not None and r.market.reference["n"] == 25
    assert all(not c.included and c.exclusion_reason == "other_line" for c in r.market.comparables)


def test_b2_the_best_matching_minority_is_not_deleted_as_an_outlier() -> None:
    """Pool = 25 Air Max 90 (~71) + the 8 real Revolution comps (~17.5, the MOST similar): all 8 were flagged
    "outlier" and the 71 set the price. The line's own comparables are the price now."""
    r = analyse(30, REV, air_max() + sold_market(REV, [float(p) for p in REV_PRICES]))
    assert r.market.tier == TIER_LINE and r.market.n_used == 8 and r.market.n_outliers == 0
    assert abs(float(r.market.fair_market_value) - 17.5) / 17.5 < 0.15
    assert r.decision.verdict not in BUYS and r.expected_profit < 0


def test_j1_a_known_model_with_no_comparable_of_its_own_gives_no_estimate() -> None:
    """Adidas Superstar (model known, true ~34) at 26 next to 10 sold Samba (~62): BUY flip 97, profit +30."""
    samba = sold_market(
        "Adidas Samba scarpe uomo 42", [58 + i for i in range(10)], brand="adidas", model="Samba"
    )
    r = analyse(26, "Adidas Superstar scarpe uomo 42", samba, brand="adidas", model="Superstar")
    assert r.market.fair_market_value is None and r.decision.verdict == V.INSUFFICIENT_EVIDENCE
    assert "Nessun confronto dello stesso modello" in r.insufficient_reason
    assert r.headline.startswith("Dati insufficienti: nessun confronto dello stesso modello")
    own = sold_market(
        "Adidas Superstar scarpe uomo 42", [32, 34, 35, 33, 36], brand="adidas", model="Superstar"
    )
    control = analyse(26, "Adidas Superstar scarpe uomo 42", samba + own, brand="adidas", model="Superstar")
    assert control.market.n_used == 5 and abs(float(control.market.fair_market_value) - 34) < 3


@pytest.mark.parametrize(
    "case",
    ["e1_thin_windbreaker", "e2_tshirt_vs_hoodies", "i1_gilet_without_category"],
)
def test_sibling_categories_and_a_missing_category_give_no_estimate(case: str) -> None:
    """A subject with nothing distinctive ("Nike giacca") is compared within its own brand and category only: puffers are
    not a windbreaker's price and hoodies are not a t-shirt's (the other category is a reference); with no category at all
    there is nothing to compare within. No estimate, no purchase."""
    if case == "e1_thin_windbreaker":
        comps = sold_market(
            "Nike piumino imbottito uomo M",
            [68 + i for i in range(10)],
            category="puffer-jackets",
            parent="outerwear",
            size="M",
        )
        r = analyse(28, "Nike giacca leggera uomo M", comps, category="jackets", parent="outerwear", size="M")
    elif case == "e2_tshirt_vs_hoodies":
        comps = sold_market(
            "Nike felpa con cappuccio uomo M",
            [42 + i % 4 for i in range(10)],
            category="hoodies",
            parent="tops",
            size="M",
        )
        r = analyse(15, "Nike maglietta uomo M", comps, category="t-shirts", parent="tops", size="M")
    else:
        comps = sold_market(
            "Adidas piumino imbottito uomo M",
            [68 + i for i in range(10)],
            brand="adidas",
            category="puffer-jackets",
            parent="outerwear",
            size="M",
        )
        r = analyse(25, "Adidas gilet uomo M", comps, brand="adidas", category=None, parent=None, size="M")
    assert r.market.fair_market_value is None and r.decision.verdict == V.INSUFFICIENT_EVIDENCE
    assert r.data_quality == "insufficient" and r.market.insufficient_reason
    if case != "i1_gilet_without_category":
        # the other category is kept as a labelled reference, never in the price
        assert (
            r.market.tier == TIER_FAMILY and r.market.reference is not None and r.market.reference["n"] == 10
        )
        assert all(c.exclusion_reason == "other_category" and not c.included for c in r.market.comparables)


@pytest.mark.parametrize("brand", ["nike", None])
def test_a_brand_and_category_estimate_is_indicative_capped_and_never_a_buy(brand: str | None) -> None:
    """E3 (an unbranded jacket at 12 next to 8 padded ones at ~43): BUY flip 92. With nothing but brand and category
    the price is the same-category median, labelled and capped, and the verdict stops at the watchlist."""
    title = "Nike giacca imbottita uomo M" if brand else "giacca imbottita uomo M"
    comps = sold_market(
        title, [41 + i % 5 for i in range(8)], brand=brand, category="jackets", parent="outerwear", size="M"
    )
    r = analyse(
        12,
        "Nike giacca leggera uomo M" if brand else "giacca leggera uomo M",
        comps,
        brand=brand,
        category="jackets",
        parent="outerwear",
        size="M",
    )
    assert r.market.tier == TIER_FAMILY and r.data_quality == "limited"
    assert r.market.fair_market_value is not None and r.market.confidence <= 35
    assert r.decision.verdict in (V.WATCHLIST, V.PASS) and r.decision.verdict not in BUYS
    assert any(v.code == "no_same_model_comparables" for v in r.decision.vetoes)


def test_the_line_key_is_what_the_taxonomy_does_not_name() -> None:
    assert line_tokens(REV, "EU 42", "nike") == {"revolution", "6", "running"} - {"running"}
    assert line_tokens("Nike scarpe bellissime bianche EU 42", "EU 42", "nike") == frozenset()
    assert same_line(
        line_tokens(REV, "EU 42", "nike"),
        line_tokens("Nike Revolution 6 Next Nature bianche 44", "EU 44", "nike"),
    )
    assert not same_line(
        line_tokens(REV, "EU 42", "nike"), line_tokens("Nike Revolution 5 bianche 42", "EU 42", "nike")
    )
    # a model number decides: two words in three in common is still another shoe
    assert line_overlap(line_tokens("Nike Air Max 97", None, "nike"), line_tokens(AM90, None, "nike")) == 0.0
    assert subject_kind(prof(REV, 30, status="active")) == TIER_LINE
    assert subject_kind(prof("Nike scarpe", 30, status="active")) == TIER_FAMILY
    assert subject_kind(prof(AM90, 30, status="active", model="Air Max 90")) == TIER_MODEL


# ---------------------------------------------------------------------------------- PX-02: levels, not outliers
def test_cluster_prices_keeps_the_level_with_the_weight_and_reports_the_other() -> None:
    old, new = [40.0] * 12, [22.0] * 3
    res = cluster_prices(old + new, [0.1] * 12 + [0.85] * 3)
    assert res.mixed and sorted(res.kept) == [12, 13, 14] and sorted(res.other) == list(range(12))
    lone = cluster_prices([38, 40, 42, 43, 45, 46, 49, 150])
    assert not lone.mixed and lone.outliers == [7]  # an isolated absurd price is still an outlier
    assert cluster_prices([22.0, 22.0, 40.0]).kept == [0, 1]


def test_c2_the_newest_sales_after_a_price_drop_are_not_outliers() -> None:
    """12 sold polos at ~40 aged 90-114 days and the 3 newest at ~22 (3-9 days): the 3 newest were flagged "outlier",
    FMV 40, STRONG BUY at 17 (true profit about -0.5)."""
    t = "Polo Ralph Lauren Custom Slim Fit blu navy M"
    kw = {
        "brand": "ralph-lauren",
        "category": "polo-shirts",
        "parent": "tops",
        "model": "Custom Slim Fit",
        "size": "M",
    }
    old = sold_market(
        t, [40, 41, 39, 42, 40, 38, 41, 40, 39, 42, 40, 41], ages=[90 + 2 * i for i in range(12)], **kw
    )
    new = sold_market(t, [22, 23, 21], ages=[3, 6, 9], **kw)
    r = analyse(17, t, old + new, **kw)
    assert abs(float(r.market.fair_market_value) - 22) / 22 < 0.15
    assert r.market.mixed and r.market.n_outliers == 0
    assert r.decision.verdict not in BUYS
    assert {c.exclusion_reason for c in r.market.comparables if not c.included} == {"other_price_level"}
    # the same market with only the recent regime is the control
    control = analyse(
        17, t, sold_market(t, [22, 23, 21, 22, 24, 22, 23, 21], ages=[2, 4, 6, 8, 10, 12, 14, 16], **kw), **kw
    )
    assert control.decision.verdict not in BUYS and abs(float(control.market.fair_market_value) - 22) < 2


def test_d4_the_real_comparables_of_a_rare_line_are_not_outvoted_by_a_popular_one() -> None:
    """Polo Big Pony at 45 whose 3 true comparables (@70) were excluded by 36 Classic Fit (@27): FMV 27, a real deal missed."""
    kw = {"brand": "ralph-lauren", "category": "polo-shirts", "parent": "tops", "size": "M"}
    classic = sold_market(
        "Polo Ralph Lauren Classic Fit blu M", [27 + i % 3 for i in range(36)], model="Classic Fit", **kw
    )
    big = sold_market("Polo Ralph Lauren Big Pony blu M", [70, 72, 68], model="Big Pony", **kw)
    r = analyse(45, "Polo Ralph Lauren Big Pony blu M", classic + big, model="Big Pony", **kw)
    assert r.market.tier == TIER_MODEL and r.market.n_used == 3
    assert abs(float(r.market.fair_market_value) - 70) / 70 < 0.1
    assert r.decision.verdict in (
        V.NEGOTIATE,
        V.WATCHLIST,
    )  # a real discount, on three comparables only: not a BUY


def test_d5_a_generic_polo_is_not_priced_by_the_36_of_another_line() -> None:
    kw = {"brand": "ralph-lauren", "category": "polo-shirts", "parent": "tops", "size": "M"}
    other = sold_market(
        "Polo Ralph Lauren Classic Fit blu M", [41 + i % 3 for i in range(36)], model="Classic Fit", **kw
    )
    own = sold_market("Polo Ralph Lauren blu M", [15, 14, 16], **kw)
    r = analyse(22, "Polo Ralph Lauren blu M", other + own, **kw)
    assert r.decision.verdict not in BUYS and r.market.tier == TIER_FAMILY


# ---------------------------------------------------------------------------------- PX-03: the evidence floor
def test_d2_three_sold_comparables_are_not_enough_to_buy_on() -> None:
    t = "Polo Ralph Lauren Custom Slim Fit blu navy M"
    kw = {
        "brand": "ralph-lauren",
        "category": "polo-shirts",
        "parent": "tops",
        "model": "Custom Slim Fit",
        "size": "M",
    }
    r = analyse(17, t, sold_market(t, [40, 41, 39], **kw), **kw)
    assert r.market.n_used == 3 and r.expected_profit > 10  # the margin looks great
    assert r.decision.verdict in (V.NEGOTIATE, V.WATCHLIST)
    assert any(v.code == "thin_evidence" and v.binding for v in r.decision.vetoes)
    enough = analyse(17, t, sold_market(t, [40, 41, 39, 40, 42, 41], **kw), **kw)
    assert enough.decision.verdict in BUYS


def test_d6_three_asking_prices_nobody_paid_are_not_a_market() -> None:
    t = "Polo Ralph Lauren Custom Slim Fit blu navy M"
    kw = {
        "brand": "ralph-lauren",
        "category": "polo-shirts",
        "parent": "tops",
        "model": "Custom Slim Fit",
        "size": "M",
    }
    asks = [prof(t, p, status="active", days=3 + i, **kw) for i, p in enumerate([55, 58, 60])]
    r = analyse(25, t, asks, **kw)
    assert r.market.n_sold == 0 and r.decision.verdict not in BUYS


# ---------------------------------------------------------------------------------- PX-04: old and falling markets
def test_c1_fresh_asks_far_below_old_sales_move_the_estimate() -> None:
    """6 sold at ~40 aged 95-115 days and 15 asks at ~23.5 posted in the last 10 days: FMV 40, BUY at 17, the 15 asks ignored."""
    t = "Polo Ralph Lauren Custom Slim Fit blu navy M"
    kw = {
        "brand": "ralph-lauren",
        "category": "polo-shirts",
        "parent": "tops",
        "model": "Custom Slim Fit",
        "size": "M",
    }
    sold = sold_market(t, [40, 41, 39, 42, 40, 38], ages=[95, 100, 104, 108, 112, 115], **kw)
    asks = [
        prof(t, p, status="active", days=d, **kw)
        for p, d in zip(
            [22, 23, 24, 25, 22, 26, 21, 24, 23, 25, 22, 24, 23, 26, 25], range(1, 16), strict=False
        )
    ]
    r = analyse(17, t, sold + asks, **kw)
    assert r.market.trend == "falling" and float(r.market.fair_market_value) < 32
    assert r.decision.verdict not in BUYS
    assert any(v.code == "unstable_market" for v in r.decision.vetoes)


def test_c4_a_market_with_nothing_newer_than_a_quarter_of_a_year_is_not_trusted() -> None:
    t = "Polo Ralph Lauren Custom Slim Fit blu navy M"
    kw = {
        "brand": "ralph-lauren",
        "category": "polo-shirts",
        "parent": "tops",
        "model": "Custom Slim Fit",
        "size": "M",
    }
    old = sold_market(
        t, [40, 41, 39, 42, 40, 38, 41, 40, 39, 42], ages=[100 + 2 * i for i in range(10)], **kw
    )
    r = analyse(17, t, old, **kw)
    assert r.market.confidence < 50 and r.decision.verdict not in BUYS
    fresh = analyse(17, t, sold_market(t, [40, 41, 39, 42, 40, 38, 41, 40, 39, 42], **kw), **kw)
    assert (
        fresh.market.confidence >= 75 and fresh.decision.verdict in BUYS
    )  # same prices, recent: nothing else differs


def test_h1_asks_nobody_has_seen_for_months_set_no_price() -> None:
    t = "Polo Ralph Lauren Custom Slim Fit blu navy M"
    kw = {
        "brand": "ralph-lauren",
        "category": "polo-shirts",
        "parent": "tops",
        "model": "Custom Slim Fit",
        "size": "M",
    }
    stale = [prof(t, 40 + i % 3, status="active", days=100, seen_days=100, **kw) for i in range(8)]
    r = analyse(30, t, stale, **kw)
    assert r.market.fair_market_value is None and r.decision.verdict == V.INSUFFICIENT_EVIDENCE
    assert all(c.exclusion_reason == "stale_ask" for c in r.market.comparables)


def test_a_steady_decline_is_followed_not_averaged_away() -> None:
    """Prices falling about 45% in three months, no gap to see: the weighted median of the last month's sales sets the price."""
    t = "Polo Ralph Lauren Custom Slim Fit blu navy M"
    kw = {
        "brand": "ralph-lauren",
        "category": "polo-shirts",
        "parent": "tops",
        "model": "Custom Slim Fit",
        "size": "M",
    }
    ages = [2 + 4 * i for i in range(28)]
    prices = [22 + 18 * a / 112 for a in ages]  # 22 today, 40 at 112 days
    r = analyse(
        17, t, sold_market(t, [round(p, 1) for p in prices], ages=[float(a) for a in ages], **kw), **kw
    )
    assert r.market.trend == "falling" and float(r.market.fair_market_value) <= 26
    assert r.decision.verdict not in BUYS


# ---------------------------------------------------------------------------------- MODEL-007: variants
def test_the_variant_and_audience_come_from_what_the_title_says() -> None:
    assert variant_of("Nike Dunk High Panda", "sneakers", "footwear") == "high"
    assert variant_of("Air Jordan 1 Low Chicago", "sneakers", "footwear") == "low"
    assert variant_of("Air Jordan 1 mid e high", "sneakers", "footwear") is None  # two heights: ambiguous
    assert variant_of("Giacca low cost", "jackets", "outerwear") is None  # only footwear has a height
    assert (
        audience_of("Piumino Moncler bambino", None) == "kids"
        and audience_of("Piumino Moncler", "kids") == "kids"
    )
    assert audience_of("Piumino Moncler", "men") == "adult" and audience_of("Piumino Moncler", None) is None


def test_a_jordan_1_low_is_not_priced_with_the_highs_of_the_same_model_string() -> None:
    """10 Lows sold ~100 and 10 Highs ~200, both read as the model "Air Jordan 1": a Low asking 90 was worth 136, a 34% discount."""
    kw = {"model": "Air Jordan 1"}
    lows = sold_market("Nike Air Jordan 1 Low Chicago uomo 42", [98 + i % 5 for i in range(10)], **kw)
    highs = sold_market("Nike Air Jordan 1 High Chicago uomo 42", [198 + i % 5 for i in range(10)], **kw)
    low = prof("Nike Air Jordan 1 Low Chicago uomo 42", 90, status="active", days=0.2, **kw)
    sim = select_comparables(low, lows + highs, NOW, max_count=10_000)
    est = estimate_market_value(sim, low.condition, NOW, subject=low)
    assert est.tier == TIER_MODEL and est.n_used == 10 and 95 <= float(est.fair_market_value) <= 110
    assert not est.mixed
    high = prof("Nike Air Jordan 1 High Chicago uomo 42", 150, status="active", days=0.2, **kw)
    est_high = estimate_market_value(
        select_comparables(high, lows + highs, NOW, max_count=10_000), high.condition, NOW, subject=high
    )
    assert 190 <= float(est_high.fair_market_value) <= 210


# ---------------------------------------------------------------------------------- KIDS-002: audience
@pytest.mark.parametrize("kids_share", [4, 8, 12])
def test_a_childs_jacket_is_not_priced_with_the_adults_of_the_same_model(kids_share: int) -> None:
    """Moncler puffer: 10 adult ~300 + k kids ~110. A kid's jacket got 309 for k<=8 (kids up to 44% of the pool)."""
    kw = {
        "brand": "moncler",
        "category": "puffer-jackets",
        "parent": "outerwear",
        "model": "Maya",
        "size": "M",
    }
    adults = sold_market("Moncler Maya piumino uomo M", [290 + 3 * i for i in range(10)], **kw)
    kids = sold_market("Moncler Maya piumino bambino 12 anni", [105 + 2 * i for i in range(kids_share)], **kw)
    kid = prof("Moncler Maya piumino bambino 12 anni", 90, status="active", days=0.2, gender="kids", **kw)
    est = estimate_market_value(
        select_comparables(kid, adults + kids, NOW, max_count=10_000), kid.condition, NOW, subject=kid
    )
    if kids_share < 3:
        assert est.fair_market_value is None
    assert 100 <= float(est.fair_market_value) <= 125
    adult = prof("Moncler Maya piumino uomo M", 250, status="active", days=0.2, **kw)
    est_adult = estimate_market_value(
        select_comparables(adult, adults + kids, NOW, max_count=10_000), adult.condition, NOW, subject=adult
    )
    assert 285 <= float(est_adult.fair_market_value) <= 320


def test_kids_are_marked_and_adults_mostly_are_not() -> None:
    """Only kids against kids: an unmarked listing counts as an adult one (a children's item says so)."""
    kid = prof("Nike scarpe bambino EU 33", 20, status="active", days=0.2, size="EU 33")
    assert kid.audience == "kids"
    adult = prof("Nike scarpe EU 42", 50, gender=None)
    marked = prof("Nike scarpe bambino EU 33", 18, size="EU 33", gender=None)
    got = select_comparables(kid, [adult, marked], NOW)
    assert [c.item for c in got] == [marked]
    # and the other way round
    assert [c.item for c in select_comparables(replace(adult, id=None), [adult, marked], NOW)] == [adult]
