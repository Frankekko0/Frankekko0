"""Score curves shared by the scoring engines.

``concave(x, full)`` maps a metric to 0-100 with diminishing returns, reaching 100 at ``full``:

    100 * (1 - (1 - min(1, x / full)) ** k)

e.g. with full = 0.5 (a 50% discount is a top-tier undervaluation): 10% -> 30, 20% -> 56,
30% -> 77, 50%+ -> 100. Calibrated so the reference case "price 52% below market, ROI 73%,
strong demand, ~4 days to sell, reliable seller" scores about 91/100.
"""

from __future__ import annotations

import math

CURVE_K = 1.6

# Value at which each component reaches 100.
UNDERVALUATION_FULL = 0.5  # 50% below fair market value
ROI_FULL = 0.9  # 90% expected ROI
PROFIT_FULL = 25.0  # EUR 25 expected net profit
SELL_THROUGH_FULL = 0.65  # windowed sell-through rate


def concave(x: float, full: float, k: float = CURVE_K) -> float:
    if x <= 0 or full <= 0:
        return 0.0
    ratio = min(1.0, x / full)
    return 100.0 * (1.0 - (1.0 - ratio) ** k)


def sell_through_score(str_value: float) -> float:
    return concave(str_value, SELL_THROUGH_FULL)


def days_score(days: float) -> float:
    """100 for a sale within ~1.5 days, ~87 at 4 days, ~74 at 7, ~50 at 14, ~21 at 30."""
    return 100.0 * math.exp(-max(0.0, days - 1.5) / 18.0)
