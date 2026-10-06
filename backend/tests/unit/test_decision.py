"""Decision layer: sale probability, risk-adjusted profit, authenticity verdicts, calibration,
real-sales-first pricing and misspelled brands."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.analytics.accuracy import evaluate
from app.analytics.backtest import Case
from app.analytics.calibration import Calibration, CalibrationCase
from app.authenticity.assess import AuthInput, PhotoFinding, PhotoQuality, assess, photo_evidence
from app.identification.engine import IdentificationEngine, ListingText
from app.opportunities import insights as ins
from app.pricing.comparables import ItemProfile, ScoredComparable, select_comparables
from app.pricing.market_value import estimate_market_value

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def profile(price: float, status: str, published_days_ago: float, sold_after_days: float | None = None, **kw):
    pub = NOW - timedelta(days=published_days_ago)
    return ItemProfile(
        id=None,
        title="Polo Ralph Lauren Custom Slim Fit blu",
        price=D(str(price)),
        brand="ralph-lauren",
        category="polo-shirts",
        parent_category="tops",
        model="Custom Slim Fit",
        condition=kw.get("condition", "very_good"),
        size=kw.get("size", "M"),
        gender="men",
        color=kw.get("color", "navy"),
        status=status,
        published_at=pub,
        sold_at=pub + timedelta(days=sold_after_days) if sold_after_days is not None else None,
        last_seen_at=NOW,
        removed_at=NOW - timedelta(days=1) if status == "removed" else None,
        tracked=kw.get("tracked", False),
    )


def comp(p: ItemProfile, weight: float = 1.0) -> ScoredComparable:
    return ScoredComparable(item=p, similarity=0.9, weight=weight, adjusted_price=float(p.price))


# ---------------------------------------------------------------- probability of sale
def test_sale_probability_counts_only_known_outcomes() -> None:
    similar = [comp(profile(30, "sold", 40, 5)) for _ in range(6)]  # sold within 30 days
    similar += [comp(profile(30, "sold", 80, 45)) for _ in range(2)]  # sold, but too late
    similar += [comp(profile(30, "removed", 50))]  # withdrawn: not sold
    similar += [comp(profile(30, "active", 60))]  # on sale for 60 days: not sold in 30
    similar += [comp(profile(30, "active", 3)) for _ in range(20)]  # too early to tell: ignored
    p = ins.probability_of_sale(similar, NOW)
    assert p["n"] == 10
    assert p["p"] == pytest.approx((0.6 * 10 + 1) / 12, abs=1e-3)


def test_sale_probability_is_insufficient_with_few_outcomes() -> None:
    p = ins.probability_of_sale([comp(profile(30, "sold", 40, 5)) for _ in range(3)], NOW)
    assert p["p"] is None and "3" in p["reason"]


def test_risk_adjusted_profit() -> None:
    assert ins.risk_adjusted_profit(20.0, 0.5, 0.9) == 9.0
    assert ins.risk_adjusted_profit(-5.0, 0.9, 0.9) == -5.0  # a loss stays a loss
    assert ins.risk_adjusted_profit(20.0, None, 0.9) is None  # no made-up probability


# ---------------------------------------------------------------- condition from photos
def test_photos_worse_than_declared_lower_the_condition() -> None:
    vision = {
        "analyzer": "claude_vision",
        "defects": [{"kind": "stain", "severity": "moderate", "certainty": "certain"}],
    }
    assert ins.effective_condition("new_without_tags", vision) == "good"
    assert ins.effective_condition("very_good", {"defects": [{"severity": "severe", "certainty": "probable"}]}) == "satisfactory"
    # Unverifiable defects and better-looking photos never change the declared condition.
    assert ins.effective_condition("good", {"defects": [{"severity": "severe", "certainty": "unverifiable"}]}) == "good"
    assert ins.effective_condition("good", {"condition_estimate": {"value": "new_with_tags", "certainty": "certain"}}) == "good"


# ---------------------------------------------------------------- demand detail
def test_demand_detail_uses_size_color_and_tracked_sales() -> None:
    similar = [comp(profile(30, "sold", 40, 4, size="M", tracked=True)) for _ in range(6)]
    similar += [comp(profile(30, "removed", 40, size="XL")) for _ in range(6)]
    inp = ins.InsightInput(
        now=NOW,
        price=D("20"),
        favourites=6,
        listing_age_hours=48,
        size="M",
        color="navy",
        condition_declared="very_good",
        condition_effective="very_good",
        price_history=[(NOW - timedelta(days=5), D("30")), (NOW - timedelta(days=2), D("25")), (NOW, D("20"))],
    )
    d = ins.demand_detail(inp, similar, {None})
    assert d["favourites_per_day"] == 3.0
    assert d["price_drops"]["count"] == 2 and d["price_drops"]["total_pct"] == pytest.approx(1 / 3, abs=1e-3)
    assert d["size"]["sell_share"] == 1.0 and d["sell_share"]["overall"] == 0.5
    assert d["tracked_similar"]["median_days_to_sell"] == 4.0
    assert d["seasonality"]["available"] is False  # never invented without a year of sales


# ---------------------------------------------------------------- pricing on real sales
def test_with_enough_sales_asking_prices_do_not_set_the_price() -> None:
    subject = profile(12, "active", 0.1)
    sold = [profile(p, "sold", 20 + i, 3) for i, p in enumerate([26, 27, 28, 29, 30, 31])]
    asks = [profile(p, "active", 2 + i) for i, p in enumerate([45, 48, 50, 52])]
    comps = select_comparables(subject, [*sold, *asks], NOW)
    est = estimate_market_value(comps, "very_good", NOW)
    assert est.n_active == 0 and est.n_sold == 6
    assert D("26") <= est.expected_sale_price <= D("31")
    assert all(c.exclusion_reason == "asking_price" for c in comps if not c.is_sold)
    old = estimate_market_value(select_comparables(subject, [*sold, *asks], NOW), "very_good", NOW, sold_only_min=None)
    assert old.expected_sale_price > est.expected_sale_price  # asks pulled the old estimate up


# ---------------------------------------------------------------- calibration
def _cases(n: int, start: datetime, ratio: float) -> list[Case]:
    out = []
    for i in range(n):
        realized = 30.0 * (ratio + 0.4 * ((i * 7919) % 100 / 100 - 0.5))
        out.append(Case(None, ("ralph-lauren", "polo-shirts"), "very_good", start + timedelta(hours=i), realized, 30.0, 28.0, 32.0, 20, 80))  # type: ignore[arg-type]
    return out


def test_calibration_ranges_cover_most_sales_and_shift_only_if_it_helps() -> None:
    old = _cases(120, NOW - timedelta(days=60), 1.0)
    new = _cases(120, NOW - timedelta(days=20), 1.0)
    split = NOW - timedelta(days=30)
    cal, m = evaluate(old + new, old + new, [], split)
    assert m["before"]["in_range"] < 0.5 <= 0.7 <= m["after"]["in_range"]
    assert m["shift_applied"] is False and cal.shift == 0  # no systematic error to correct

    biased = _cases(120, NOW - timedelta(days=60), 0.7) + _cases(120, NOW - timedelta(days=20), 0.7)
    cal, m = evaluate(biased, biased, [], split)
    assert m["shift_applied"] is True and m["after"]["mae_eur"] < m["before"]["mae_eur"]


def test_calibration_needs_enough_sales_and_roundtrips() -> None:
    few = [CalibrationCase(30, 25, "c", "b", "good", 80) for _ in range(10)]
    assert not Calibration.fit(few).active
    cal = Calibration.fit([CalibrationCase(30, 25 + i % 10, "c", "b", "good", 80) for i in range(60)])
    again = Calibration.from_state(cal.to_state())
    assert again.apply(30, "c", "b", "good", 80) == pytest.approx(cal.apply(30, "c", "b", "good", 80))


# ---------------------------------------------------------------- authenticity
def base_input(**kw) -> AuthInput:
    return AuthInput(
        **{
            "brand_slug": "stone-island",
            "brand_name": "Stone Island",
            "brand_counterfeit_risk": 0.35,
            "price": 120.0,
            "market_value": 150.0,
            "photo_count": 6,
            **kw,
        }
    )


def test_no_photo_check_means_not_verifiable_never_authentic() -> None:
    a = assess(base_input(seller_reviews=200, seller_rating=4.9))
    assert a.verdict == "not_verifiable"
    assert a.missing_photos and "Stone Island" in (a.seller_message or "")


def test_new_seller_alone_is_not_a_counterfeit() -> None:
    a = assess(base_input(seller_reviews=0, seller_account_age_days=3))
    assert a.verdict != "counterfeit_risk"


def test_strong_red_flags_give_counterfeit_risk() -> None:
    a = assess(
        base_input(
            price=40.0,
            suspicious_terms=["replica"],
            photos_reused_by_other_seller=True,
            photos_analyzed=True,
            findings=[PhotoFinding(2, "label", "concern", "certain", "Font dell'etichetta diverso", [0.1, 0.2, 0.3, 0.2])],
        )
    )
    assert a.verdict == "counterfeit_risk"
    ev = next(e for e in a.evidence if e.photo == 2)
    assert ev.box == [0.1, 0.2, 0.3, 0.2]


def test_consistent_key_photos_are_probably_authentic_but_never_certain() -> None:
    findings = [
        PhotoFinding(i, kind, "consistent", "certain", f"{kind} coerente")
        for i, kind in enumerate(["label", "care_tag", "logo", "code", "stitching", "button", "zip", "material"])
    ]
    a = assess(base_input(photos_analyzed=True, findings=findings, seller_reviews=80, seller_rating=4.9))
    assert a.verdict == "probably_authentic"
    assert a.p_authentic <= 0.97


def test_blurry_key_photos_are_not_verifiable() -> None:
    findings = [PhotoFinding(0, "label", "consistent", "certain"), PhotoFinding(1, "care_tag", "consistent", "certain")]
    quality = [PhotoQuality(0, False, "sfocata"), PhotoQuality(1, False, "troppo lontana")]
    a = assess(base_input(photos_analyzed=True, findings=findings, quality=quality))
    assert a.verdict == "not_verifiable"
    assert any(e.direction == "?" and "sfocata" in e.label for e in a.evidence)


def test_photo_evidence_reads_old_and_new_analyses() -> None:
    old = photo_evidence({"analyzer": "claude_vision", "authenticity_concerns": ["Logo storto"]})
    assert old["photos_analyzed"] and old["findings"][0].photo == -1
    assert photo_evidence({"analyzer": "heuristic"})["photos_analyzed"] is False


# ---------------------------------------------------------------- hidden opportunities
@pytest.mark.parametrize(
    ("title", "brand"),
    [
        ("Polo Ralf Loren blu M", "ralph-lauren"),
        ("Giacca Carhart detroit", "carhartt"),
        ("Felpa Stone Islnd", "stone-island"),
        ("Piumino North Fase 700", "the-north-face"),
        ("Pile Patagonya", "patagonia"),
    ],
)
def test_misspelled_brand_is_found_and_flagged(title: str, brand: str) -> None:
    r = IdentificationEngine().identify(ListingText(title=title))
    assert r.brand.value == brand
    assert r.flags["misspelled_brand"]["brand"]


@pytest.mark.parametrize("title", ["Maglione Guess rosso", "Borsa Bella", "Giacca North Pole"])
def test_similar_words_are_not_taken_for_brands(title: str) -> None:
    r = IdentificationEngine().identify(ListingText(title=title))
    assert "misspelled_brand" not in r.flags
