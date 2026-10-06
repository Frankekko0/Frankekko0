"""Market price engine: robust statistics, comparables, FMV and resale scenarios."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.pricing.comparables import ItemProfile, adjust_for_condition, select_comparables, similarity
from app.pricing.market_value import SegmentPrior, estimate_market_value
from app.pricing.stats import WeightedValue, describe, detect_outliers, histogram, weighted_percentile

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def test_spec_outlier_example_excludes_150() -> None:
    prices = [38, 40, 42, 43, 45, 46, 49, 150]
    res = detect_outliers(prices)
    assert [prices[i] for i in res.removed] == [150]
    stats = describe([WeightedValue(prices[i]) for i in res.kept])
    assert 42 <= stats.median <= 44
    assert stats.max_reasonable == 49


def test_tight_cluster_keeps_close_values() -> None:
    assert detect_outliers([40, 40, 40, 41, 44]).removed == []


def test_small_sample_uses_ratio_filter() -> None:
    res = detect_outliers([20, 25, 200])
    assert res.method == "ratio"
    assert 2 in res.removed


def test_non_positive_prices_are_outliers() -> None:
    assert 0 in detect_outliers([0, 30, 31, 32, 33]).removed


def test_weighted_percentile_equal_weights_matches_median() -> None:
    items = [WeightedValue(v) for v in (1, 2, 3, 4, 5)]
    assert weighted_percentile(items, 0.5) == 3
    heavy = [WeightedValue(10, 10), WeightedValue(100, 1)]
    assert weighted_percentile(heavy, 0.5) < 50


def test_histogram_counts_all_values() -> None:
    h = histogram([10, 12, 14, 30, 31], bins=4)
    assert sum(b["count"] for b in h) == 5


def item(
    price: float,
    *,
    model: str | None = "Custom Slim Fit",
    status: str = "sold",
    condition: str = "very_good",
    size: str = "M",
    days: float = 10,
    category: str = "polo-shirts",
) -> ItemProfile:
    published = NOW - timedelta(days=days + 5)
    return ItemProfile(
        id=None,
        title=f"Polo Ralph Lauren {model or ''} blu",
        price=D(str(price)),
        brand="ralph-lauren",
        category=category,
        parent_category="tops",
        model=model,
        condition=condition,
        size=size,
        gender="men",
        color="navy",
        country="IT",
        status=status,
        published_at=published,
        sold_at=NOW - timedelta(days=days) if status == "sold" else None,
        last_seen_at=NOW - timedelta(days=days),
    )


SUBJECT = ItemProfile(
    id=None,
    title="Polo Ralph Lauren Custom Slim Fit blu",
    price=D("12"),
    brand="ralph-lauren",
    category="polo-shirts",
    parent_category="tops",
    model="Custom Slim Fit",
    condition="very_good",
    size="M",
    gender="men",
    color="navy",
    country="IT",
)


def test_same_model_is_more_similar_than_other_model() -> None:
    same, _ = similarity(SUBJECT, item(30))
    other, _ = similarity(SUBJECT, item(30, model="Big Pony"))
    other_cat, _ = similarity(SUBJECT, item(30, category="knitwear"))
    assert same > other > 0
    assert same > other_cat


def test_condition_adjustment_normalizes_prices() -> None:
    assert adjust_for_condition(100, "very_good", "new_with_tags") < 100
    assert adjust_for_condition(100, "very_good", "satisfactory") > 100


def test_other_brands_are_never_comparables() -> None:
    foreign = ItemProfile(**{**item(30).__dict__, "brand": "nike"})
    assert select_comparables(SUBJECT, [foreign, item(30)], NOW).__len__() == 1


def market(sold: list[float], active: list[float]) -> list[ItemProfile]:
    return [item(p, status="sold", days=3 + i) for i, p in enumerate(sold)] + [
        item(p, status="active", days=1 + i) for i, p in enumerate(active)
    ]


def test_fmv_prefers_sold_prices_and_orders_scenarios() -> None:
    comps = select_comparables(SUBJECT, market([26, 27, 28, 28, 29, 30, 31, 32], [33, 35, 36, 38, 40]), NOW)
    est = estimate_market_value(comps, "very_good", NOW)
    assert est.fair_market_value is not None
    assert D("26") <= est.fair_market_value <= D("31")
    assert est.quick_sale_price <= est.expected_sale_price <= est.optimistic_sale_price
    assert est.n_sold == 8
    assert est.ask_to_sale_ratio < 1  # asks above realized prices
    assert 40 <= est.confidence <= 100


def test_outliers_do_not_move_fmv() -> None:
    base = [38, 40, 42, 43, 45, 46, 49]
    a = estimate_market_value(select_comparables(SUBJECT, market(base, []), NOW), "very_good", NOW)
    b = estimate_market_value(select_comparables(SUBJECT, market([*base, 150], []), NOW), "very_good", NOW)
    assert b.n_outliers == 1
    assert abs(a.fair_market_value - b.fair_market_value) <= D("1")


def test_insufficient_data_returns_clear_message() -> None:
    est = estimate_market_value(select_comparables(SUBJECT, market([30], []), NOW), "very_good", NOW)
    assert est.fair_market_value is None
    assert any("Non ci sono abbastanza dati" in n for n in est.notes)


def test_segment_prior_supports_thin_markets() -> None:
    prior = SegmentPrior(median_price=40, p25_price=34, p75_price=46, sample_size=50, sell_through_rate=0.5)
    est = estimate_market_value(select_comparables(SUBJECT, market([30], []), NOW), "very_good", NOW, prior)
    assert est.fair_market_value is not None
    assert est.used_prior
    assert est.confidence <= 30


@pytest.mark.parametrize("dispersed", [False, True])
def test_dispersion_lowers_confidence(dispersed: bool) -> None:
    prices = [15, 22, 30, 41, 55, 70, 25, 48] if dispersed else [29, 30, 30, 31, 31, 32, 30, 29]
    est = estimate_market_value(select_comparables(SUBJECT, market(prices, []), NOW), "very_good", NOW)
    if dispersed:
        assert est.confidence_breakdown["dispersion"] < 0.6
    else:
        assert est.confidence_breakdown["dispersion"] > 0.85


def test_fast_similarity_score_equals_breakdown_total() -> None:
    from app.pricing.comparables import similarity_score

    for cand in (
        item(30),
        item(30, model="Big Pony"),
        item(30, category="knitwear"),
        item(45, condition="good"),
    ):
        assert similarity_score(SUBJECT, cand) == similarity(SUBJECT, cand)[0]
