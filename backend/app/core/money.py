"""Decimal helpers for monetary arithmetic.

All money is handled as ``Decimal`` and rounded half-up to cents only at boundaries,
so financial calculations are exact and reproducible (no float drift in profit/ROI).
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0")


def to_decimal(value: object, default: Decimal | None = None) -> Decimal:
    """Convert numbers/strings to Decimal without going through binary floats where possible."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        if default is None:
            raise ValueError("value is None")
        return default
    try:
        if isinstance(value, float):
            return Decimal(repr(value))
        if isinstance(value, str):
            return Decimal(value.replace(",", ".").replace("€", "").strip())
        return Decimal(value)  # type: ignore[arg-type]
    except (InvalidOperation, ValueError) as exc:
        if default is not None:
            return default
        raise ValueError(f"not a monetary value: {value!r}") from exc


def money(value: object) -> Decimal:
    """Round to cents, half-up (the way prices are displayed and charged)."""
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def floor_money(value: object, step: Decimal = CENT) -> Decimal:
    """Round *down* to ``step`` (used for maximum buy prices: never overshoot the target)."""
    d = to_decimal(value)
    return ((d / step).to_integral_value(rounding=ROUND_DOWN) * step).quantize(CENT)


def pct(value: Decimal, digits: int = 1) -> Decimal:
    """Ratio -> percentage rounded (0.6375 -> 63.8)."""
    q = Decimal(1).scaleb(-digits)
    return (value * 100).quantize(q, rounding=ROUND_HALF_UP)
