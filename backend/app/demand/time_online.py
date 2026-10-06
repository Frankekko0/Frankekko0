"""How long comparable listings stay online and how many actually sell.

* sold: days from publication to the (estimated) sale;
* still on sale: days online so far (a lower bound: they have not sold yet);
* removed: days until the seller removed them - they count as **not sold**.

``sold_share`` = sold / (sold + on sale + removed): only sales seen as such are counted.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from app.pricing.comparables import ScoredComparable


def _days(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None or b < a:
        return None
    return (b - a).total_seconds() / 86400


def _summary(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 1),
        "median": round(statistics.median(values), 1),
    }


def time_online(similar: list[ScoredComparable], now: datetime) -> dict[str, Any]:
    sold = [
        d
        for c in similar
        if c.item.status == "sold"
        if (d := _days(c.item.published_at, c.item.sold_at)) is not None
    ]
    active = [
        d for c in similar if c.item.status == "active" if (d := _days(c.item.published_at, now)) is not None
    ]
    removed = [
        d
        for c in similar
        if c.item.status == "removed"
        if (d := _days(c.item.published_at, c.item.removed_at)) is not None
    ]
    n_sold = sum(1 for c in similar if c.item.status == "sold")
    n_active = sum(1 for c in similar if c.item.status == "active")
    n_removed = sum(1 for c in similar if c.item.status == "removed")
    total = n_sold + n_active + n_removed
    everything = sold + active + removed
    return {
        "comparables": total,
        "sold": n_sold,
        "active": n_active,
        "removed": n_removed,
        "sold_share": round(n_sold / total, 4) if total else None,
        "days_to_sell": _summary(sold),
        "days_online_active": _summary(active),
        "days_online_removed": _summary(removed),
        "avg_days_online": round(statistics.fmean(everything), 1) if everything else None,
    }
