"""Deal evaluation: the exact financial engine the decision layer (and later the AI agent) calls.

Everything numeric comes from ``calculator`` (one set of formulas, shared with the feed's SQL, the
alerts and the extension's port). This module adds what a decision needs on top:

* every cost line carries a **status**: ``confirmed`` (read on the listing, or a figure the user
  saved), ``estimated`` (a default or a formula, shown as such) or ``unknown`` (needed but never
  provided: it is *not* in the totals and the profit is declared gross of it);
* margin on the sale, break-even resale price, capital tied up;
* **no tax is computed** (no rate is invented): taxes are reported as excluded.

ROI = net profit / total acquisition cost.  Margin = net profit / resale price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from app.core.money import ZERO
from app.profit.calculator import (
    AcquisitionCost,
    CostProfile,
    ProfitResult,
    SaleRevenue,
    break_even_resale_price,
    max_buy_price,
    profit_for,
)

D = Decimal
RATIO_Q = D("0.0001")
TAX_NOTE = "Imposte escluse: nessuna aliquota è inserita, quindi non vengono stimate."


class CostStatus(StrEnum):
    CONFIRMED = "confirmed"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


_SEVERITY = {CostStatus.CONFIRMED: 0, CostStatus.ESTIMATED: 1, CostStatus.UNKNOWN: 2}
Side = Literal["acquisition", "sale"]


@dataclass(frozen=True)
class EvaluatedCost:
    key: str
    label: str
    amount: Decimal
    status: CostStatus
    # listing | user_setting | default | formula | caller | reserve | none
    source: str
    side: Side

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "amount": str(self.amount),
            "status": self.status.value,
            "source": self.source,
            "side": self.side,
        }


@dataclass(frozen=True)
class Restoration:
    """Cleaning/repair before resale. ``amount=None``: it is needed but nobody quantified it."""

    amount: Decimal | None
    status: CostStatus = CostStatus.ESTIMATED


@dataclass(frozen=True)
class DealEvaluation:
    purchase_price: Decimal
    resale_price: Decimal
    lines: tuple[EvaluatedCost, ...]
    total_acquisition_cost: Decimal  # also the capital tied up until the item is sold
    net_revenue: Decimal
    net_profit: Decimal
    roi: Decimal  # ratio (0.6923 = 69.23%)
    margin_on_sale: Decimal | None  # net profit / resale price; None when the resale price is 0
    break_even_price: Decimal | None
    cost_status: CostStatus  # the worst status among the lines
    unknown_costs: tuple[str, ...]
    result: ProfitResult

    @property
    def capital_tied_up(self) -> Decimal:
        return self.total_acquisition_cost

    @property
    def gross_of_unknown_costs(self) -> bool:
        """True when some needed cost is unknown: the profit shown is before those costs."""
        return bool(self.unknown_costs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "purchase_price": str(self.purchase_price),
            "resale_price": str(self.resale_price),
            "total_acquisition_cost": str(self.total_acquisition_cost),
            "capital_tied_up": str(self.capital_tied_up),
            "net_revenue": str(self.net_revenue),
            "net_profit": str(self.net_profit),
            "roi": str(self.roi),
            "margin_on_sale": None if self.margin_on_sale is None else str(self.margin_on_sale),
            "break_even_price": None if self.break_even_price is None else str(self.break_even_price),
            "cost_status": self.cost_status.value,
            "unknown_costs": list(self.unknown_costs),
            "gross_of_unknown_costs": self.gross_of_unknown_costs,
            "lines": [line.as_dict() for line in self.lines],
            "taxes_included": False,
            "taxes_note": TAX_NOTE,
        }


def _user_cost(
    key: str, label: str, amount: Decimal, side: Side, *, saved: bool, unknown_if_unset: bool
) -> EvaluatedCost | None:
    """A cost that comes from the profile: the user's own figure when they saved one, else a default."""
    if amount > 0:
        status, source = (
            (CostStatus.CONFIRMED, "user_setting") if saved else (CostStatus.ESTIMATED, "default")
        )
        return EvaluatedCost(key, label, amount, status, source, side)
    if saved or not unknown_if_unset:
        return None  # nothing to pay (their choice, or a cost that only exists when set)
    return EvaluatedCost(key, label, ZERO, CostStatus.UNKNOWN, "none", side)


def _lines(
    acq: AcquisitionCost,
    sale: SaleRevenue,
    profile: CostProfile,
    *,
    listing_shipping: Decimal | None,
    listing_buyer_protection: Decimal | None,
    restoration: Restoration | None,
    contingency_pct: Decimal,
    saved: bool,
) -> list[EvaluatedCost]:
    out = [
        EvaluatedCost(
            "purchase_price",
            "Prezzo d'acquisto",
            acq.purchase_price,
            CostStatus.CONFIRMED,
            "listing",
            "acquisition",
        )
    ]
    profile_source = "user_setting" if saved else "default"
    if listing_buyer_protection is not None:
        out.append(
            EvaluatedCost(
                "buyer_protection",
                "Protezione acquisti",
                acq.buyer_protection,
                CostStatus.CONFIRMED,
                "listing",
                "acquisition",
            )
        )
    else:
        out.append(
            EvaluatedCost(
                "buyer_protection",
                "Protezione acquisti",
                acq.buyer_protection,
                CostStatus.ESTIMATED,
                f"formula:{profile_source}",
                "acquisition",
            )
        )
    if profile.use_listing_shipping and listing_shipping is not None:
        out.append(
            EvaluatedCost(
                "shipping_in", "Spedizione", acq.shipping, CostStatus.CONFIRMED, "listing", "acquisition"
            )
        )
    else:
        out.append(
            EvaluatedCost(
                "shipping_in", "Spedizione", acq.shipping, CostStatus.ESTIMATED, profile_source, "acquisition"
            )
        )
    if line := _user_cost(
        "other_acquisition",
        "Altri costi d'acquisto",
        acq.other,
        "acquisition",
        saved=saved,
        unknown_if_unset=False,
    ):
        out.append(line)
    if restoration is not None:
        if restoration.amount is None:
            out.append(
                EvaluatedCost("restoration", "Ripristino", ZERO, CostStatus.UNKNOWN, "none", "acquisition")
            )
        else:
            out.append(
                EvaluatedCost(
                    "restoration", "Ripristino", acq.restoration, restoration.status, "caller", "acquisition"
                )
            )

    for key, label, amount, unknown_if_unset in (
        ("selling_fees", "Commissioni di vendita", sale.selling_fees, False),
        ("payment_fees", "Costi di pagamento", sale.payment_fees, False),
        ("advertising", "Promozione / advertising", sale.advertising, True),
        ("packaging", "Imballaggio", sale.packaging, True),
        ("shipping_out", "Spedizione a tuo carico", sale.shipping, True),
        ("other_sale", "Altri costi di vendita", sale.other, False),
    ):
        if line := _user_cost(key, label, amount, "sale", saved=saved, unknown_if_unset=unknown_if_unset):
            out.append(line)
    if contingency_pct > 0:
        out.append(
            EvaluatedCost(
                "contingency",
                "Riserva per imprevisti",
                sale.contingency,
                CostStatus.ESTIMATED,
                "reserve",
                "sale",
            )
        )
    return out


def evaluate_deal(
    purchase_price: Decimal,
    resale_price: Decimal,
    profile: CostProfile,
    *,
    listing_shipping: Decimal | None = None,
    listing_buyer_protection: Decimal | None = None,
    restoration: Restoration | None = None,
    contingency_pct: Decimal = ZERO,
    profile_saved: bool = False,
) -> DealEvaluation:
    """Exact profit, ROI, margin and break-even for one purchase/resale pair, with cost statuses.

    ``profile_saved``: the user saved their own cost profile (their figures are then ``confirmed``);
    otherwise defaults are used and shown as ``estimated``, and the user-dependent costs that no
    default covers (promotion, outbound shipping, packaging if zero) are ``unknown``.
    """
    restoration_amount = restoration.amount if restoration and restoration.amount is not None else ZERO
    result = profit_for(
        purchase_price,
        resale_price,
        profile,
        listing_shipping,
        listing_buyer_protection,
        restoration=restoration_amount,
        contingency_pct=contingency_pct,
    )
    lines = _lines(
        result.acquisition,
        result.sale,
        profile,
        listing_shipping=listing_shipping,
        listing_buyer_protection=listing_buyer_protection,
        restoration=restoration,
        contingency_pct=contingency_pct,
        saved=profile_saved,
    )
    unknown = tuple(line.label for line in lines if line.status == CostStatus.UNKNOWN)
    worst = max((line.status for line in lines), key=_SEVERITY.__getitem__)
    margin = (
        (result.net_profit / result.sale.sale_price).quantize(RATIO_Q) if result.sale.sale_price > 0 else None
    )
    return DealEvaluation(
        purchase_price=result.acquisition.purchase_price,
        resale_price=result.sale.sale_price,
        lines=tuple(lines),
        total_acquisition_cost=result.acquisition.total,
        net_revenue=result.sale.net,
        net_profit=result.net_profit,
        roi=result.roi,
        margin_on_sale=margin,
        break_even_price=break_even_resale_price(
            purchase_price,
            profile,
            listing_shipping,
            listing_buyer_protection,
            restoration_amount,
            contingency_pct,
        ),
        cost_status=worst,
        unknown_costs=unknown,
        result=result,
    )


def evaluate_scenarios(
    purchase_price: Decimal,
    prices: dict[str, Decimal | None],
    profile: CostProfile,
    **kwargs: Any,
) -> dict[str, DealEvaluation]:
    """Conservative / base / optimistic (or any named prices); prices that are ``None`` are skipped."""
    return {
        name: evaluate_deal(purchase_price, price, profile, **kwargs)
        for name, price in prices.items()
        if price is not None
    }


def recommended_max_buy_price(
    resale_price: Decimal | None,
    profile: CostProfile,
    min_profit: Decimal,
    min_roi: Decimal,
    *,
    listing_shipping: Decimal | None = None,
    restoration: Decimal = ZERO,
    contingency_pct: Decimal = ZERO,
) -> Decimal | None:
    """Highest purchase price that still meets both targets (None when no price can).

    Rounded down on purpose so the targets are always met: it can sit up to one cent below the
    exact maximum. Restoration and the contingency reserve are part of the cost it protects.
    """
    return max_buy_price(
        resale_price, profile, min_profit, min_roi, listing_shipping, restoration, contingency_pct
    )


def profit_per_euro_per_day(
    net_profit: Decimal, capital: Decimal, days: Decimal | float | None
) -> Decimal | None:
    """The guiding metric: profit per euro of capital per day. ``None`` if days or capital are unknown/zero."""
    if days is None or capital <= 0:
        return None
    d = D(str(days))
    if d <= 0:
        return None
    return (net_profit / capital / d).quantize(D("0.000001"))


__all__ = [
    "TAX_NOTE",
    "CostStatus",
    "DealEvaluation",
    "EvaluatedCost",
    "Restoration",
    "evaluate_deal",
    "evaluate_scenarios",
    "profit_per_euro_per_day",
    "recommended_max_buy_price",
]
