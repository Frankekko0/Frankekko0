"""Seller Reliability Score (0-100).

A new seller is *not* treated as a scammer: with no reviews the rating is shrunk towards a
neutral prior and the score lands around the middle. Only evidence (low ratings over many
reviews, anomalies) pushes it down.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

PRIOR_RATING = 4.5
PRIOR_WEIGHT = 5


@dataclass(frozen=True)
class SellerProfile:
    rating: Decimal | float | None
    review_count: int
    account_created_at: datetime | None = None
    item_count: int | None = None
    sold_count: int | None = None
    anomalies: tuple[str, ...] = ()


@dataclass
class SellerScore:
    score: int
    level: str  # unknown | new | low | medium | high
    smoothed_rating: float | None
    factors: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "level": self.level,
            "smoothed_rating": round(self.smoothed_rating, 2) if self.smoothed_rating is not None else None,
            "factors": self.factors,
        }


def seller_reliability(seller: SellerProfile | None, now: datetime) -> SellerScore:
    if seller is None:
        return SellerScore(50, "unknown", None, [{"label": "Venditore non disponibile", "impact": 0}])
    n = max(0, seller.review_count)
    rating = float(seller.rating) if seller.rating is not None and n > 0 else PRIOR_RATING
    smoothed = (rating * n + PRIOR_RATING * PRIOR_WEIGHT) / (n + PRIOR_WEIGHT)
    rating_c = min(1.0, max(0.0, (smoothed - 3.5) / 1.5))
    volume_c = min(1.0, math.log10(n + 1) / math.log10(500))
    age_days = (now - seller.account_created_at).days if seller.account_created_at else None
    age_c = min(1.0, age_days / 365) if age_days is not None else 0.3
    sold_c = min(1.0, math.log10((seller.sold_count or 0) + 1) / math.log10(300))

    factors: list[dict[str, object]] = []
    score = 15 + 55 * rating_c + 15 * volume_c + 10 * age_c + 5 * sold_c
    if n == 0:
        factors.append(
            {"label": "Nessuna recensione: poco storico, non necessariamente un problema", "impact": 0}
        )
    else:
        factors.append(
            {"label": f"Valutazione {rating:.1f}★ su {n} recensioni", "impact": round(55 * rating_c - 30)}
        )
    if n >= 10 and smoothed < 4.2:
        score -= 15
        factors.append({"label": "Molte recensioni negative", "impact": -15})
    if age_days is not None and age_days < 30:
        factors.append({"label": f"Account creato {age_days} giorni fa", "impact": round(10 * age_c - 10)})
    anomaly_penalty = min(25, 10 * len(seller.anomalies))
    if anomaly_penalty:
        score -= anomaly_penalty
        for a in seller.anomalies:
            factors.append({"label": a, "impact": -10})
    score_i = max(0, min(100, round(score)))
    if n < 3 and (age_days is None or age_days < 60):
        level = "new"
    elif score_i >= 75:
        level = "high"
    elif score_i >= 50:
        level = "medium"
    else:
        level = "low"
    return SellerScore(score_i, level, smoothed if n else None, factors)
