"""Profit, ROI and Smart Buy Price calculations.

All money is ``Decimal``. Formulas (configurable cost profile):

    Total Acquisition Cost = purchase price + buyer protection + shipping + other acquisition costs
                             + restoration (cleaning, repair; optional)
    Net Sale Revenue       = sale price - selling fees - advertising - packaging - payment fees
                             - outbound shipping - other sale costs - contingency reserve (optional)
    Net Profit             = Net Sale Revenue - Total Acquisition Cost
    ROI                    = Net Profit / Total Acquisition Cost

Buyer protection is ``fixed + pct x purchase price`` (Vinted style), or the fee actually shown on
the listing when it was captured (more precise: it reflects the buyer's market). The maximum buy price is
the closed-form inverse of these formulas under both the minimum-profit and minimum-ROI
constraints, rounded *down* so the targets are always met.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.core.money import CENT, ZERO, floor_money, money
from app.schemas.common import Money

D = Decimal


class CostProfile(BaseModel):
    """User-configurable cost model. Defaults reflect buying and reselling on Vinted Italy."""

    buyer_protection_fixed: Money = Field(default=D("0.70"), ge=0, le=100)
    buyer_protection_pct: Money = Field(default=D("0.05"), ge=0, le=1)
    shipping_in: Money = Field(default=D("3.49"), ge=0, le=200, description="Default inbound shipping")
    use_listing_shipping: bool = Field(
        default=True, description="Prefer the shipping fee shown on the listing"
    )
    other_acquisition: Money = Field(default=ZERO, ge=0, le=1000)
    selling_fee_fixed: Money = Field(default=ZERO, ge=0, le=100)
    selling_fee_pct: Money = Field(default=ZERO, ge=0, le=1)
    shipping_out: Money = Field(default=ZERO, ge=0, le=200, description="Shipping paid by you when selling")
    packaging: Money = Field(default=D("0.50"), ge=0, le=100)
    advertising: Money = Field(default=ZERO, ge=0, le=500, description="Bumps / promoted listings")
    payment_fee_fixed: Money = Field(default=ZERO, ge=0, le=100)
    payment_fee_pct: Money = Field(default=ZERO, ge=0, le=1)
    other_sale: Money = Field(default=ZERO, ge=0, le=1000)

    def acquisition_fixed(self, listing_shipping: Decimal | None = None) -> Decimal:
        return self.buyer_protection_fixed + self.shipping(listing_shipping) + self.other_acquisition

    def shipping(self, listing_shipping: Decimal | None) -> Decimal:
        if self.use_listing_shipping and listing_shipping is not None:
            return listing_shipping
        return self.shipping_in


@dataclass(frozen=True)
class AcquisitionCost:
    purchase_price: Decimal
    buyer_protection: Decimal
    shipping: Decimal
    other: Decimal
    total: Decimal
    # Added later and last: defaults keep every existing construction valid.
    restoration: Decimal = ZERO


@dataclass(frozen=True)
class SaleRevenue:
    sale_price: Decimal
    selling_fees: Decimal
    advertising: Decimal
    packaging: Decimal
    payment_fees: Decimal
    shipping: Decimal
    other: Decimal
    net: Decimal
    contingency: Decimal = ZERO

    @property
    def total_costs(self) -> Decimal:
        return self.sale_price - self.net


@dataclass(frozen=True)
class ProfitResult:
    acquisition: AcquisitionCost
    sale: SaleRevenue
    net_profit: Decimal
    roi: Decimal  # ratio, e.g. 0.6400 = 64%

    def as_dict(self) -> dict[str, Any]:
        return {
            "acquisition": {k: str(v) for k, v in asdict(self.acquisition).items()},
            "sale": {k: str(v) for k, v in asdict(self.sale).items()},
            "net_profit": str(self.net_profit),
            "roi": str(self.roi),
        }


def acquisition_cost(
    purchase_price: Decimal,
    profile: CostProfile,
    listing_shipping: Decimal | None = None,
    listing_buyer_protection: Decimal | None = None,
    restoration: Decimal = ZERO,
) -> AcquisitionCost:
    bp = money(
        listing_buyer_protection
        if listing_buyer_protection is not None
        else profile.buyer_protection_fixed + profile.buyer_protection_pct * purchase_price
    )
    shipping = money(profile.shipping(listing_shipping))
    other = money(profile.other_acquisition)
    price = money(purchase_price)
    fix = money(restoration)
    return AcquisitionCost(price, bp, shipping, other, price + bp + shipping + other + fix, fix)


def sale_revenue(sale_price: Decimal, profile: CostProfile, contingency_pct: Decimal = ZERO) -> SaleRevenue:
    price = money(sale_price)
    selling = money(profile.selling_fee_fixed + profile.selling_fee_pct * price)
    payment = money(profile.payment_fee_fixed + profile.payment_fee_pct * price)
    advertising = money(profile.advertising)
    packaging = money(profile.packaging)
    shipping = money(profile.shipping_out)
    other = money(profile.other_sale)
    reserve = money(contingency_pct * price)
    net = price - selling - payment - advertising - packaging - shipping - other - reserve
    return SaleRevenue(price, selling, advertising, packaging, payment, shipping, other, net, reserve)


def compute_profit(acquisition: AcquisitionCost, sale: SaleRevenue) -> ProfitResult:
    profit = sale.net - acquisition.total
    roi = (profit / acquisition.total).quantize(D("0.0001")) if acquisition.total > 0 else ZERO
    return ProfitResult(acquisition, sale, profit, roi)


def profit_for(
    purchase_price: Decimal,
    sale_price: Decimal,
    profile: CostProfile,
    listing_shipping: Decimal | None = None,
    listing_buyer_protection: Decimal | None = None,
    restoration: Decimal = ZERO,
    contingency_pct: Decimal = ZERO,
) -> ProfitResult:
    return compute_profit(
        acquisition_cost(purchase_price, profile, listing_shipping, listing_buyer_protection, restoration),
        sale_revenue(sale_price, profile, contingency_pct),
    )


@dataclass(frozen=True)
class Scenario:
    name: str
    sale_price: Decimal
    result: ProfitResult
    estimated_days: float | None = None


def profit_scenarios(
    purchase_price: Decimal,
    quick: Decimal | None,
    expected: Decimal | None,
    optimistic: Decimal | None,
    profile: CostProfile,
    listing_shipping: Decimal | None = None,
    days: tuple[float | None, float | None, float | None] = (None, None, None),
    listing_buyer_protection: Decimal | None = None,
) -> list[Scenario]:
    out: list[Scenario] = []
    for name, price, d in (
        ("conservative", quick, days[0]),
        ("expected", expected, days[1]),
        ("optimistic", optimistic, days[2]),
    ):
        if price is not None:
            out.append(
                Scenario(
                    name,
                    price,
                    profit_for(purchase_price, price, profile, listing_shipping, listing_buyer_protection),
                    d,
                )
            )
    return out


def max_buy_price(
    sale_price: Decimal | None,
    profile: CostProfile,
    min_profit: Decimal,
    min_roi: Decimal,
    listing_shipping: Decimal | None = None,
    restoration: Decimal = ZERO,
    contingency_pct: Decimal = ZERO,
) -> Decimal | None:
    """Highest purchase price that still meets BOTH min profit and min ROI at ``sale_price``.

    TAC(p) = p * (1 + bp_pct) + F,  F = bp_fixed + shipping + other_acquisition + restoration
    profit >= min_profit  <=>  TAC <= NSR - min_profit
    ROI >= min_roi        <=>  TAC <= NSR / (1 + min_roi)
    """
    if sale_price is None:
        return None
    nsr = sale_revenue(sale_price, profile, contingency_pct).net
    tac_max = min(nsr - min_profit, nsr / (D(1) + min_roi))
    fixed = money(profile.acquisition_fixed(listing_shipping)) + money(restoration)
    p = (tac_max - fixed) / (D(1) + profile.buyer_protection_pct)
    if p <= 0:
        return None
    candidate = floor_money(p)
    # Guard against cent rounding of the buyer-protection fee pushing us over the limits.
    while candidate > 0:
        r = profit_for(
            candidate,
            sale_price,
            profile,
            listing_shipping,
            restoration=restoration,
            contingency_pct=contingency_pct,
        )
        if r.net_profit >= min_profit and r.roi >= min_roi:
            return candidate
        candidate -= CENT
    return None


def break_even_resale_price(
    purchase_price: Decimal,
    profile: CostProfile,
    listing_shipping: Decimal | None = None,
    listing_buyer_protection: Decimal | None = None,
    restoration: Decimal = ZERO,
    contingency_pct: Decimal = ZERO,
) -> Decimal | None:
    """Lowest resale price at which net profit is not negative (0.00 profit or a few cents more).

    NSR(p) = p * (1 - pct) - F_sale  with pct = fee% + payment% + contingency%, so
    p = (TAC + F_sale) / (1 - pct), rounded up to the cent and then fixed by checking the
    cent-rounded real formula. ``None`` when the percentage costs eat the whole price.
    """
    pct = profile.selling_fee_pct + profile.payment_fee_pct + contingency_pct
    if pct >= 1:
        return None
    tac = acquisition_cost(
        purchase_price, profile, listing_shipping, listing_buyer_protection, restoration
    ).total
    fixed_sale = (
        money(profile.selling_fee_fixed)
        + money(profile.payment_fee_fixed)
        + money(profile.advertising)
        + money(profile.packaging)
        + money(profile.shipping_out)
        + money(profile.other_sale)
    )
    price = ((tac + fixed_sale) / (D(1) - pct) / CENT).to_integral_value(rounding=ROUND_CEILING) * CENT

    def profit_at(p: Decimal) -> Decimal:
        return sale_revenue(p, profile, contingency_pct).net - tac

    for _ in range(200):  # per-line cent rounding can leave us a few cents short
        if profit_at(price) >= 0:
            break
        price += CENT
    else:  # pragma: no cover - cannot happen for pct < 1
        return None
    while price > 0 and profit_at(price - CENT) >= 0:
        price -= CENT
    return price
