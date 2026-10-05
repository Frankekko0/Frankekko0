"""Financial calculations: profit, ROI, maximum buy price, scenarios."""

from decimal import Decimal as D

import pytest

from app.profit.calculator import (
    CostProfile,
    acquisition_cost,
    max_buy_price,
    profit_for,
    profit_scenarios,
    sale_revenue,
)

ZERO_COSTS = CostProfile(
    buyer_protection_fixed=D("0"),
    buyer_protection_pct=D("0"),
    shipping_in=D("0"),
    packaging=D("0"),
)


def test_spec_example_profit_and_roi() -> None:
    """purchase 20, shipping 3, fees 2, resale 45, sale costs 4 -> profit 16, ROI 64%."""
    costs = CostProfile(
        buyer_protection_fixed=D("2"),
        buyer_protection_pct=D("0"),
        shipping_in=D("3"),
        use_listing_shipping=False,
        selling_fee_fixed=D("4"),
        packaging=D("0"),
    )
    r = profit_for(D("20"), D("45"), costs)
    assert r.acquisition.total == D("25.00")
    assert r.sale.net == D("41.00")
    assert r.net_profit == D("16.00")
    assert r.roi == D("0.6400")


def test_roi_example_from_spec() -> None:
    """Total cost 20, net revenue 40 -> profit 20, ROI 100%."""
    costs = ZERO_COSTS.model_copy(update={"other_acquisition": D("0")})
    r = profit_for(D("20"), D("40"), costs)
    assert r.net_profit == D("20.00")
    assert r.roi == D("1.0000")


def test_vinted_buyer_protection_is_fixed_plus_percentage() -> None:
    costs = CostProfile()  # 0.70 + 5%
    acq = acquisition_cost(D("18"), costs, listing_shipping=D("3.49"))
    assert acq.buyer_protection == D("1.60")  # 0.70 + 0.90
    assert acq.shipping == D("3.49")
    assert acq.total == D("23.09")


def test_listing_shipping_can_be_ignored() -> None:
    costs = CostProfile(use_listing_shipping=False, shipping_in=D("4.00"))
    assert acquisition_cost(D("10"), costs, listing_shipping=D("2.00")).shipping == D("4.00")


def test_all_sale_costs_are_deducted() -> None:
    costs = CostProfile(
        selling_fee_fixed=D("1"),
        selling_fee_pct=D("0.10"),
        advertising=D("2"),
        packaging=D("0.5"),
        payment_fee_fixed=D("0.25"),
        payment_fee_pct=D("0.02"),
        shipping_out=D("3"),
        other_sale=D("1"),
    )
    s = sale_revenue(D("50"), costs)
    assert s.selling_fees == D("6.00")
    assert s.payment_fees == D("1.25")
    assert s.net == D("50") - D("6") - D("1.25") - D("2") - D("0.5") - D("3") - D("1")


def test_negative_profit_has_negative_roi() -> None:
    r = profit_for(D("40"), D("30"), CostProfile())
    assert r.net_profit < 0
    assert r.roi < 0


@pytest.mark.parametrize("sale_price", ["25", "39", "45", "120", "300"])
@pytest.mark.parametrize("min_profit,min_roi", [("10", "0.40"), ("15", "0.50"), ("5", "1.0"), ("0", "0")])
def test_max_buy_price_meets_targets_and_is_maximal(sale_price: str, min_profit: str, min_roi: str) -> None:
    costs = CostProfile()
    mbp = max_buy_price(D(sale_price), costs, D(min_profit), D(min_roi), D("3.49"))
    if mbp is None:
        cheapest = profit_for(D("0.01"), D(sale_price), costs, D("3.49"))
        assert cheapest.net_profit < D(min_profit) or cheapest.roi < D(min_roi)
        return
    r = profit_for(mbp, D(sale_price), costs, D("3.49"))
    assert r.net_profit >= D(min_profit)
    assert r.roi >= D(min_roi)
    above = profit_for(mbp + D("0.05"), D(sale_price), costs, D("3.49"))
    assert above.net_profit < D(min_profit) or above.roi < D(min_roi)


def test_max_buy_price_none_when_targets_unreachable() -> None:
    assert max_buy_price(D("8"), CostProfile(), D("10"), D("0.4")) is None
    assert max_buy_price(None, CostProfile(), D("10"), D("0.4")) is None


def test_scenarios_use_all_costs() -> None:
    sc = profit_scenarios(D("18"), D("32"), D("39"), D("45"), CostProfile(), D("3.49"), (2, 5, 9))
    assert [s.name for s in sc] == ["conservative", "expected", "optimistic"]
    profits = [s.result.net_profit for s in sc]
    assert profits == sorted(profits)
    total_cost = sc[0].result.acquisition.total
    assert all(s.result.acquisition.total == total_cost for s in sc)
    # expected: 39 - 0.50 packaging - (18 + 0.70 + 0.90 + 3.49)
    assert sc[1].result.net_profit == D("39") - D("0.50") - D("23.09")
