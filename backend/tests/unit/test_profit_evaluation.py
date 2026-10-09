"""The financial engine as an exact tool: reference numbers, cost statuses, break-even, cases E and H."""

from decimal import Decimal as D
from itertools import product

import pytest

from app.profit.calculator import (
    CostProfile,
    break_even_resale_price,
    max_buy_price,
    profit_for,
)
from app.profit.evaluation import (
    TAX_NOTE,
    CostStatus,
    Restoration,
    evaluate_deal,
    evaluate_scenarios,
    profit_per_euro_per_day,
    recommended_max_buy_price,
)

# 20 (price) + 4 (shipping) + 2 (protection) = 26; resale 45 - 1 (packaging) = 44; profit 18; ROI 69.23%
BRIEF = CostProfile(
    buyer_protection_fixed=D("2"),
    buyer_protection_pct=D("0"),
    shipping_in=D("4"),
    use_listing_shipping=False,
    packaging=D("1"),
)


def by_key(ev, key):
    return next(line for line in ev.lines if line.key == key)


# ------------------------------------------------------------------ the reference case
def test_reference_numbers_26_44_18_and_69_23_percent() -> None:
    ev = evaluate_deal(D("20"), D("45"), BRIEF, profile_saved=True)
    assert ev.total_acquisition_cost == D("26.00")
    assert ev.net_revenue == D("44.00")
    assert ev.net_profit == D("18.00")
    assert ev.roi == D("0.6923")  # 69.23 %
    assert ev.capital_tied_up == D("26.00")
    assert ev.margin_on_sale == D("0.4000")  # 18 / 45
    assert ev.break_even_price == D("27.00")  # 26 + 1 packaging
    assert ev.result.roi * 100 == D("69.2300")


def test_dynamic_recalculation_when_price_or_costs_change() -> None:
    assert evaluate_deal(D("15"), D("45"), BRIEF, profile_saved=True).net_profit == D("23.00")
    pricier = BRIEF.model_copy(update={"shipping_in": D("6")})
    assert evaluate_deal(D("20"), D("45"), pricier, profile_saved=True).net_profit == D("16.00")


def test_taxes_are_never_estimated() -> None:
    d = evaluate_deal(D("20"), D("45"), BRIEF, profile_saved=True).as_dict()
    assert d["taxes_included"] is False and d["taxes_note"] == TAX_NOTE
    assert D(d["net_profit"]) == D("18.00")  # nothing is subtracted for tax


def test_the_dict_form_is_json_safe_and_exact() -> None:
    import json

    d = evaluate_deal(D("20"), D("45"), BRIEF, profile_saved=True).as_dict()
    assert json.loads(json.dumps(d))["roi"] == "0.6923"


# ------------------------------------------------------------------ cost statuses
def test_values_read_on_the_listing_are_confirmed_formulas_are_estimates() -> None:
    profile = CostProfile()
    ev = evaluate_deal(
        D("20"), D("45"), profile, listing_shipping=D("2.99"), listing_buyer_protection=D("1.60")
    )
    assert (by_key(ev, "buyer_protection").status, by_key(ev, "buyer_protection").source) == (
        CostStatus.CONFIRMED,
        "listing",
    )
    assert by_key(ev, "shipping_in").status == CostStatus.CONFIRMED
    no_listing = evaluate_deal(D("20"), D("45"), profile)
    assert by_key(no_listing, "buyer_protection").status == CostStatus.ESTIMATED
    assert by_key(no_listing, "buyer_protection").source.startswith("formula")
    assert by_key(no_listing, "shipping_in").status == CostStatus.ESTIMATED


def test_without_a_saved_profile_user_dependent_costs_are_unknown_not_invented() -> None:
    ev = evaluate_deal(D("20"), D("45"), CostProfile())
    assert ev.cost_status == CostStatus.UNKNOWN and ev.gross_of_unknown_costs
    assert set(ev.unknown_costs) == {"Promozione / advertising", "Spedizione a tuo carico"}
    # unknown costs are not in the totals: nothing was made up for them
    assert by_key(ev, "advertising").amount == 0 and by_key(ev, "shipping_out").amount == 0
    # the default packaging is a visible assumption, not a fact
    assert (by_key(ev, "packaging").status, by_key(ev, "packaging").source) == (
        CostStatus.ESTIMATED,
        "default",
    )


def test_a_saved_profile_makes_the_users_figures_confirmed_and_clears_the_unknowns() -> None:
    reads_listing = BRIEF.model_copy(update={"use_listing_shipping": True})
    ev = evaluate_deal(
        D("20"),
        D("45"),
        reads_listing,
        listing_shipping=D("4"),
        listing_buyer_protection=D("2"),
        profile_saved=True,
    )
    assert ev.unknown_costs == () and not ev.gross_of_unknown_costs
    assert by_key(ev, "packaging").status == CostStatus.CONFIRMED
    assert ev.cost_status == CostStatus.CONFIRMED  # every line is read on the listing or set by the user


def test_a_fixed_shipping_figure_in_the_profile_is_an_assumption_even_when_saved() -> None:
    ev = evaluate_deal(
        D("20"), D("45"), BRIEF, listing_shipping=D("4"), listing_buyer_protection=D("2"), profile_saved=True
    )
    assert by_key(ev, "shipping_in").status == CostStatus.ESTIMATED  # BRIEF ignores the listing's shipping
    assert by_key(ev, "shipping_in").source == "user_setting"
    assert ev.cost_status == CostStatus.ESTIMATED


def test_one_estimated_line_makes_the_whole_evaluation_estimated() -> None:
    ev = evaluate_deal(D("20"), D("45"), BRIEF, profile_saved=True)  # protection/shipping are formulas
    assert ev.cost_status == CostStatus.ESTIMATED and ev.unknown_costs == ()


def test_zero_valued_optional_costs_do_not_clutter_the_lines() -> None:
    keys = {line.key for line in evaluate_deal(D("20"), D("45"), BRIEF, profile_saved=True).lines}
    assert keys == {"purchase_price", "buyer_protection", "shipping_in", "packaging"}


# ------------------------------------------------------------------ restoration and contingency
def test_restoration_counts_in_the_cost_and_the_capital() -> None:
    ev = evaluate_deal(D("20"), D("45"), BRIEF, restoration=Restoration(D("3")), profile_saved=True)
    assert ev.total_acquisition_cost == D("29.00") and ev.capital_tied_up == D("29.00")
    assert ev.net_profit == D("15.00")
    assert (by_key(ev, "restoration").amount, by_key(ev, "restoration").status) == (
        D("3.00"),
        CostStatus.ESTIMATED,
    )
    assert ev.break_even_price == D("30.00")


def test_restoration_needed_but_not_quantified_is_unknown_and_excluded() -> None:
    ev = evaluate_deal(D("20"), D("45"), BRIEF, restoration=Restoration(None), profile_saved=True)
    assert ev.total_acquisition_cost == D("26.00") and "Ripristino" in ev.unknown_costs
    assert ev.cost_status == CostStatus.UNKNOWN and ev.gross_of_unknown_costs


def test_contingency_reserve_lowers_revenue_and_raises_break_even() -> None:
    ev = evaluate_deal(D("20"), D("45"), BRIEF, contingency_pct=D("0.05"), profile_saved=True)
    assert ev.net_revenue == D("41.75")  # 45 - 1 - 2.25
    assert ev.net_profit == D("15.75")
    assert (
        by_key(ev, "contingency").amount == D("2.25")
        and by_key(ev, "contingency").status == CostStatus.ESTIMATED
    )
    assert ev.break_even_price == D("28.42")


# ------------------------------------------------------------------ one set of formulas
PROFILES = [
    CostProfile(),
    BRIEF,
    CostProfile(
        selling_fee_pct=D("0.10"),
        selling_fee_fixed=D("0.35"),
        payment_fee_pct=D("0.029"),
        shipping_out=D("4.5"),
    ),
    CostProfile(
        buyer_protection_pct=D("0.08"), advertising=D("1.2"), other_sale=D("0.4"), other_acquisition=D("0.5")
    ),
]


@pytest.mark.parametrize(
    ("purchase", "resale"), product(["0.01", "7.5", "18", "99.99"], ["5", "23", "45.50", "300"])
)
def test_the_evaluation_matches_the_calculator_on_a_grid(purchase: str, resale: str) -> None:
    for profile in PROFILES:
        ev = evaluate_deal(D(purchase), D(resale), profile, listing_shipping=D("3.49"))
        ref = profit_for(D(purchase), D(resale), profile, D("3.49"))
        assert (ev.total_acquisition_cost, ev.net_revenue, ev.net_profit, ev.roi) == (
            ref.acquisition.total,
            ref.sale.net,
            ref.net_profit,
            ref.roi,
        )


@pytest.mark.parametrize("purchase", ["1", "12.34", "20", "55"])
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("contingency", ["0", "0.05"])
@pytest.mark.parametrize("restoration", ["0", "2.5"])
def test_break_even_is_exact_to_the_cent(
    purchase: str, profile: CostProfile, contingency: str, restoration: str
) -> None:
    be = break_even_resale_price(D(purchase), profile, None, None, D(restoration), D(contingency))
    assert be is not None
    at = profit_for(
        D(purchase), be, profile, None, restoration=D(restoration), contingency_pct=D(contingency)
    )
    below = profit_for(
        D(purchase), be - D("0.01"), profile, None, restoration=D(restoration), contingency_pct=D(contingency)
    )
    assert at.net_profit >= 0 and below.net_profit < 0


def test_break_even_is_none_when_percentage_costs_eat_the_price() -> None:
    greedy = CostProfile(selling_fee_pct=D("0.6"), payment_fee_pct=D("0.4"))
    assert break_even_resale_price(D("10"), greedy) is None


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize(
    ("restoration", "contingency"), [("0", "0"), ("3", "0"), ("0", "0.05"), ("2.5", "0.08")]
)
def test_max_buy_price_meets_the_targets_and_is_within_a_cent_of_exact(
    profile: CostProfile, restoration: str, contingency: str
) -> None:
    resale, mp, mr = D("60"), D("10"), D("0.4")
    best = recommended_max_buy_price(
        resale,
        profile,
        mp,
        mr,
        listing_shipping=D("3.49"),
        restoration=D(restoration),
        contingency_pct=D(contingency),
    )
    assert best is not None
    ok = profit_for(
        best, resale, profile, D("3.49"), restoration=D(restoration), contingency_pct=D(contingency)
    )
    over = profit_for(
        best + D("0.01"),
        resale,
        profile,
        D("3.49"),
        restoration=D(restoration),
        contingency_pct=D(contingency),
    )
    assert ok.net_profit >= mp and ok.roi >= mr
    # rounded down on purpose (targets always met): never more than a cent below the exact maximum
    two_over = profit_for(
        best + D("0.02"),
        resale,
        profile,
        D("3.49"),
        restoration=D(restoration),
        contingency_pct=D(contingency),
    )
    assert two_over.net_profit < mp or two_over.roi < mr
    assert over.net_profit >= 0  # sanity: the probe above is a real deal, not noise


def test_defaults_leave_the_legacy_max_buy_price_unchanged() -> None:
    profile = CostProfile()
    assert recommended_max_buy_price(
        D("45"), profile, D("10"), D("0.4"), listing_shipping=D("3.49")
    ) == max_buy_price(D("45"), profile, D("10"), D("0.4"), D("3.49"))


# ------------------------------------------------------------------ scenarios and the guiding metric
def test_scenarios_skip_missing_prices() -> None:
    out = evaluate_scenarios(
        D("20"), {"conservative": D("35"), "base": D("45"), "optimistic": None}, BRIEF, profile_saved=True
    )
    assert list(out) == ["conservative", "base"]
    assert out["base"].net_profit == D("18.00") and out["conservative"].net_profit == D("8.00")


def test_profit_per_euro_per_day() -> None:
    assert profit_per_euro_per_day(D("18"), D("26"), 10) == D("0.069231")
    assert profit_per_euro_per_day(D("18"), D("26"), None) is None
    assert profit_per_euro_per_day(D("18"), D("26"), 0) is None
    assert profit_per_euro_per_day(D("18"), D("0"), 10) is None


# ------------------------------------------------------------------ case E: costs eat the margin -> PASS
def test_case_e_15_euro_item_resold_at_23_leaves_no_real_margin() -> None:
    profile = CostProfile()  # protection 0.70 + 5%, shipping 3.49, packaging 0.50
    ev = evaluate_deal(D("15"), D("23"), profile, listing_shipping=D("3.49"))
    assert ev.total_acquisition_cost == D("19.94") and ev.net_profit == D("2.56")
    assert ev.net_profit < D("10")  # far below any sensible minimum profit
    assert ev.roi < D("0.40")
    best = recommended_max_buy_price(D("23"), profile, D("10"), D("0.4"), listing_shipping=D("3.49"))
    assert best is not None and best < D("15")  # at 15 EUR this is not worth buying -> PASS


# ------------------------------------------------------------------ case H: speed beats a bigger, slower profit
def test_case_h_b_wins_per_euro_per_day_even_with_the_smaller_profit() -> None:
    a = evaluate_deal(D("40"), D("66.50"), BRIEF, profile_saved=True)  # bigger profit, slow
    b = evaluate_deal(D("40"), D("61.50"), BRIEF, profile_saved=True)  # 5 EUR less profit, fast
    assert a.net_profit - b.net_profit == D("5.00") and a.net_profit > b.net_profit
    slow = profit_per_euro_per_day(a.net_profit, a.capital_tied_up, 60)
    fast = profit_per_euro_per_day(b.net_profit, b.capital_tied_up, 10)
    assert slow is not None and fast is not None and fast > slow
