"""Detailed analysis: market comparison, time online, risk signals, data quality, headline."""

from datetime import timedelta
from decimal import Decimal as D

from app.demand.time_online import time_online
from app.opportunities.engine import EconomicTargets, SubjectContext, run_analysis
from app.pricing.comparables import ItemProfile, select_comparables
from app.pricing.comparison import market_comparison, price_stats
from app.pricing.market_value import estimate_market_value
from app.profit.calculator import CostProfile
from app.scoring.seller import SellerProfile
from app.scoring.signals import SignalInput, build_signals, description_quality
from tests.unit.test_pricing import NOW, SUBJECT, item


def removed(price: float, days_online: float) -> ItemProfile:
    base = item(price, status="active", days=days_online)
    return ItemProfile(
        **{
            **base.__dict__,
            "status": "removed",
            "removed_at": base.published_at + timedelta(days=days_online),
        }
    )


# ------------------------------------------------------------------ market comparison
def test_price_stats_quantiles() -> None:
    assert price_stats([10, 20, 30, 40, 50]) == {
        "n": 5,
        "min": 10,
        "p25": 20,
        "median": 30,
        "p75": 40,
        "max": 50,
    }
    assert price_stats([]) is None


def test_market_comparison_counts_and_matches() -> None:
    cands = [
        item(30),
        item(32),
        item(28, size="L"),
        item(31, condition="good"),
        item(34, status="active"),
        removed(29, 9),
    ]
    similar = select_comparables(SUBJECT, cands, NOW)
    priced = [c for c in similar if c.item.status in ("sold", "active")]
    market = estimate_market_value(priced, SUBJECT.condition, NOW)
    mc = market_comparison(SUBJECT, similar, market)
    assert (mc["found"], mc["found_sold"], mc["found_active"], mc["found_removed"]) == (6, 4, 1, 1)
    assert mc["used"] == 5 and mc["used_sold"] == 4 and mc["used_active"] == 1
    assert mc["matches"] == {"same_model": 5, "same_size": 4, "same_condition": 4}
    assert mc["prices"]["n"] == 5 and mc["prices"]["min"] <= mc["prices"]["median"] <= mc["prices"]["max"]
    assert mc["sold_prices"]["n"] == 4


def test_time_online_counts_removed_as_not_sold() -> None:
    similar = select_comparables(
        SUBJECT, [item(30, days=4), item(31, days=8), item(33, status="active", days=2), removed(29, 10)], NOW
    )
    t = time_online(similar, NOW)
    assert (t["sold"], t["active"], t["removed"]) == (2, 1, 1)
    assert t["sold_share"] == 0.5
    assert t["days_to_sell"] == {"n": 2, "mean": 5.0, "median": 5.0}  # published 5 days before the sale
    assert t["days_online_removed"]["mean"] == 10.0


# ------------------------------------------------------------------ risk signals
def signals(**kw):
    base = dict(
        title="Polo Ralph Lauren Custom Slim Fit",
        description="Polo originale taglia M, 100% cotone, indossata due volte, nessun difetto. Misure: spalle 45 cm.",
        brand_name="Ralph Lauren",
        brand_slug="ralph-lauren",
        brand_counterfeit_risk=0.05,
        category="polo-shirts",
        color="navy",
        photo_count=6,
        price=D("25"),
        fair_market_value=D("30"),
        seller_known=True,
        seller_rating=4.9,
        seller_review_count=80,
    )
    return {s.code: s for s in build_signals(SignalInput(**{**base, **kw}))}


def test_description_quality_flags_generic_text_only() -> None:
    assert description_quality("Come da foto, per info scrivetemi").generic
    assert description_quality("").generic
    detailed = description_quality(
        "Felpa in cotone, taglia L, misure petto 56 cm, piccolo segno sulla manica (in foto)."
    )
    assert not detailed.generic and detailed.score >= 70


def test_clean_listing_has_no_warnings_but_never_claims_authenticity() -> None:
    s = signals()
    assert s["possible_fake"].level == "ok"
    assert "non è una garanzia" in s["possible_fake"].label
    assert s["generic_description"].level == "ok"
    assert s["seller_reviews"].level == "ok"
    # Without image analysis these cannot be checked: said explicitly, not guessed.
    assert s["label_photos"].level == "info" and not s["label_photos"].verifiable
    assert s["title_photo_mismatch"].level == "info" and not s["title_photo_mismatch"].verifiable


def test_possible_fake_combines_price_brand_text_and_photos() -> None:
    s = signals(
        brand_counterfeit_risk=0.4,
        price=D("9"),
        suspicious_terms=("replica",),
        photos_reused_by_other_seller=True,
    )
    fake = s["possible_fake"]
    assert fake.level == "high"
    assert any("sotto il mercato" in e for e in fake.evidence) and any("replica" in e for e in fake.evidence)


def test_new_seller_is_not_called_a_scammer() -> None:
    s = signals(seller_review_count=0, seller_rating=None)["seller_reviews"]
    assert s.level == "low" and "non è di per sé un problema" in s.label
    few = signals(seller_review_count=3)["seller_reviews"]
    assert few.level == "low" and "poche recensioni (3)" in few.label


def test_label_photos_and_title_photo_mismatch_with_image_analysis() -> None:
    vision = {
        "analyzer": "claude",
        "brand": {"value": "Lacoste", "certainty": "probable"},
        "color": {"value": "navy", "certainty": "certain"},
        "photo_quality": {"has_label_photo": False},
    }
    s = signals(vision=vision, brand_counterfeit_risk=0.2)
    assert s["label_photos"].level == "medium"  # brand with counterfeits, no label photo
    assert signals(vision=vision)["label_photos"].level == "low"
    mm = s["title_photo_mismatch"]
    assert mm.level == "high" and "lacoste" in mm.evidence[0].lower()
    ok = signals(
        vision={
            **vision,
            "brand": {"value": "Ralph Lauren", "certainty": "probable"},
            "photo_quality": {"has_label_photo": True},
        }
    )
    assert ok["label_photos"].level == "ok" and ok["title_photo_mismatch"].level == "ok"
    assert signals(brand_counterfeit_risk=0.2)["possible_fake"].level == "low"
    few_photos = signals(photo_count=1)["label_photos"]
    assert few_photos.level in ("low", "medium") and "poche foto" in few_photos.label.lower()


# ------------------------------------------------------------------ data quality and headline
def subject(**kw) -> SubjectContext:
    return SubjectContext(
        profile=SUBJECT,
        description_length=80,
        photo_count=5,
        favourite_count=3,
        listing_age_hours=2,
        shipping_fee=D("2.99"),
        brand_name="Ralph Lauren",
        brand_counterfeit_risk=0.2,
        category_baseline_days=10,
        identification={},
        identification_confidence=80,
        seller=SellerProfile(D("4.9"), 50),
        description="Polo originale taglia M, cotone, nessun difetto, misure spalle 45 cm.",
        **kw,
    )


def run(cands):
    return run_analysis(subject(), cands, NOW, CostProfile(), EconomicTargets())


def test_too_few_comparables_is_declared_not_estimated() -> None:
    r = run([item(30), item(31)])
    assert r.data_quality == "insufficient"
    assert r.market.fair_market_value is None and r.expected_profit is None
    assert "Solo 2 comparabili" in r.insufficient_reason
    assert r.headline.startswith("Dati insufficienti")


def test_limited_and_ok_data_quality_with_headline() -> None:
    limited = run([item(p) for p in (29, 30, 31, 30)])
    assert limited.data_quality == "limited" and "indicativa" in limited.insufficient_reason
    ok = run(
        [
            item(p, days=d)
            for p, d in zip([28, 29, 30, 30, 31, 32, 30, 29, 31, 30, 33, 28], range(2, 14), strict=True)
        ]
    )
    assert ok.data_quality == "ok" and ok.insufficient_reason is None
    assert "sotto il mercato" in ok.headline and "margine" in ok.headline
    assert ok.market_comparison["used"] == 12 and ok.time_online["sold"] == 12
    assert {s.code for s in ok.risk_signals} == {
        "possible_fake",
        "generic_description",
        "label_photos",
        "seller_reviews",
        "title_photo_mismatch",
    }


def test_removed_comparables_never_set_the_price() -> None:
    base = [item(p) for p in (29, 30, 31, 30, 29, 31, 30, 32)]
    with_removed = run([*base, removed(5, 3), removed(6, 2), removed(4, 5)])
    without = run(base)
    assert with_removed.market.fair_market_value == without.market.fair_market_value
    assert with_removed.time_online["removed"] == 3
    # ...but they lower the share of listings that actually sell.
    assert with_removed.demand.sell_through_rate < without.demand.sell_through_rate


def test_actual_buyer_protection_fee_is_used() -> None:
    cands = [item(p) for p in (29, 30, 31, 30, 29, 31, 30, 32, 30)]
    formula = run_analysis(subject(), cands, NOW, CostProfile(), EconomicTargets())
    shown = run_analysis(
        subject(buyer_protection_fee=D("0.50")), cands, NOW, CostProfile(), EconomicTargets()
    )
    assert formula.total_acquisition_cost - shown.total_acquisition_cost == D("0.80")  # 0.70+5%*12 - 0.50
