"""Forecast against reality: every closed sale leaves a record, and the errors are measured per segment.

Profit forecast is not profit earned: this module is where the two are put side by side.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

MIN_PER_SEGMENT = 3


@dataclass(frozen=True)
class OutcomeRow:
    brand: str | None
    category: str | None
    predicted_price: float | None
    predicted_days: float | None
    predicted_profit: float | None
    actual_price: float
    actual_days: float
    actual_profit: float


def price_error_pct(predicted: float | None, actual: float) -> float | None:
    """(actual - predicted) / predicted: negative when we expected more than we got."""
    if predicted is None or predicted <= 0:
        return None
    return (actual - predicted) / predicted


def days_error(predicted: float | None, actual: float) -> float | None:
    return None if predicted is None else actual - predicted


def to_decimal(v: float | None, places: str = "0.0001") -> Decimal | None:
    return None if v is None else Decimal(str(v)).quantize(Decimal(places))


def _stats(rows: list[OutcomeRow]) -> dict[str, Any]:
    pe = [e for r in rows if (e := price_error_pct(r.predicted_price, r.actual_price)) is not None]
    de = [e for r in rows if (e := days_error(r.predicted_days, r.actual_days)) is not None]
    pf = [r for r in rows if r.predicted_profit is not None]
    return {
        "n": len(rows),
        "price_error_mae_pct": round(statistics.mean(abs(e) for e in pe) * 100, 1) if pe else None,
        "price_bias_pct": round(statistics.mean(pe) * 100, 1) if pe else None,
        "days_error_mean": round(statistics.mean(de), 1) if de else None,
        "predicted_profit": round(sum(r.predicted_profit or 0 for r in pf), 2) if pf else None,
        "actual_profit": round(sum(r.actual_profit for r in rows), 2),
        "profit_gap": round(sum(r.actual_profit - (r.predicted_profit or 0) for r in pf), 2) if pf else None,
    }


def accuracy(rows: list[OutcomeRow]) -> dict[str, Any]:
    overall = _stats(rows) if rows else {"n": 0}
    by: dict[str, list[OutcomeRow]] = defaultdict(list)
    for r in rows:
        by[r.brand or "—"].append(r)
    segments = {k: _stats(v) for k, v in sorted(by.items()) if len(v) >= MIN_PER_SEGMENT}
    note = (
        "Nessuna vendita chiusa: non c'è ancora nulla da confrontare con le previsioni."
        if not rows
        else "Errore % = (venduto − previsto) / previsto: negativo se si è venduto sotto la previsione."
    )
    return {"overall": overall, "by_brand": segments, "note": note}
