"""Alert rules (pure functions): watchlist matching, user thresholds, Ultra Deal, price drops."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.enums import AlertPriority, AlertType
from app.identification.taxonomy import fold

# A watchlist without any quality criterion still needs a floor, otherwise "Nike under 20 EUR"
# would notify for every overpriced Nike item.
WATCHLIST_DEFAULT_MIN_FLIP = 60
PRICE_DROP_MIN_RATIO = Decimal("0.05")


@dataclass(frozen=True)
class AlertCandidate:
    opportunity_id: uuid.UUID
    listing_id: uuid.UUID
    root_listing_id: uuid.UUID
    title: str
    brand: str | None
    category: str | None
    parent_category: str | None
    size: str | None
    condition: str
    country: str | None
    is_vintage: bool
    price: Decimal
    previous_price: Decimal | None
    expected_profit: Decimal | None  # computed with the target user's cost profile
    expected_roi: Decimal | None
    flip_score: int
    confidence: int
    risk: int
    is_ultra: bool
    is_new: bool


@dataclass(frozen=True)
class Thresholds:
    min_flip: int
    min_roi: Decimal
    min_profit: Decimal
    min_confidence: int
    max_risk: int | None


@dataclass(frozen=True)
class WatchlistRule:
    id: uuid.UUID
    name: str
    query: str | None = None
    brands: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    sizes: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    vintage_only: bool = False
    max_buy_price: Decimal | None = None
    min_profit: Decimal | None = None
    min_roi: Decimal | None = None
    min_flip: int | None = None
    min_confidence: int | None = None
    max_risk: int | None = None


@dataclass
class AlertDecision:
    type: AlertType
    priority: AlertPriority
    dedupe_key: str
    title: str
    body: str
    watchlist_id: uuid.UUID | None = None
    reasons: list[str] = field(default_factory=list)


def _eur(v: Decimal | None) -> str:
    return "n/d" if v is None else f"€{v:.0f}" if v == v.to_integral_value() else f"€{v:.2f}"


def _pct(v: Decimal | None) -> str:
    return "n/d" if v is None else f"{float(v) * 100:.0f}%"


def passes_thresholds(c: AlertCandidate, t: Thresholds) -> bool:
    return (
        c.flip_score >= t.min_flip
        and c.confidence >= t.min_confidence
        and c.expected_roi is not None
        and c.expected_roi >= t.min_roi
        and c.expected_profit is not None
        and c.expected_profit >= t.min_profit
        and (t.max_risk is None or c.risk <= t.max_risk)
    )


def matches_watchlist(c: AlertCandidate, w: WatchlistRule) -> bool:
    if w.brands and c.brand not in w.brands:
        return False
    if w.categories and c.category not in w.categories and c.parent_category not in w.categories:
        return False
    if w.sizes and (c.size or "").upper() not in {s.upper() for s in w.sizes}:
        return False
    if w.conditions and c.condition not in w.conditions:
        return False
    if w.countries and (c.country or "") not in w.countries:
        return False
    if w.vintage_only and not c.is_vintage:
        return False
    if w.query:
        title = fold(c.title)
        if not all(word in title for word in fold(w.query).split()):
            return False
    if w.max_buy_price is not None and c.price > w.max_buy_price:
        return False
    if w.min_profit is not None and (c.expected_profit is None or c.expected_profit < w.min_profit):
        return False
    if w.min_roi is not None and (c.expected_roi is None or c.expected_roi < w.min_roi):
        return False
    if w.min_confidence is not None and c.confidence < w.min_confidence:
        return False
    if w.max_risk is not None and c.risk > w.max_risk:
        return False
    has_quality_rule = any(v is not None for v in (w.min_flip, w.min_profit, w.min_roi))
    min_flip = (
        w.min_flip if w.min_flip is not None else (0 if has_quality_rule else WATCHLIST_DEFAULT_MIN_FLIP)
    )
    return c.flip_score >= min_flip


def summary_line(c: AlertCandidate) -> str:
    return (
        f"{_eur(c.price)} → profitto {_eur(c.expected_profit)} · ROI {_pct(c.expected_roi)} · "
        f"Flip {c.flip_score} · Confidence {c.confidence} · Rischio {c.risk}"
    )


def decide_alerts(
    c: AlertCandidate,
    thresholds: Thresholds,
    watchlists: list[WatchlistRule],
    *,
    ultra_enabled: bool = True,
    price_drop_enabled: bool = True,
    watchlist_enabled: bool = True,
    new_opportunity_enabled: bool = True,
) -> list[AlertDecision]:
    """Which alerts a user should get for this analysis (at most one per type/watchlist)."""
    decisions: list[AlertDecision] = []
    root = c.root_listing_id
    line = summary_line(c)

    if ultra_enabled and c.is_ultra:
        decisions.append(
            AlertDecision(
                AlertType.ULTRA_DEAL,
                AlertPriority.HIGH,
                f"ultra:{root}",
                f"🔥 ULTRA DEAL · {c.title}"[:200],
                line,
            )
        )

    dropped = (
        c.previous_price is not None
        and c.previous_price > 0
        and (c.previous_price - c.price) / c.previous_price >= PRICE_DROP_MIN_RATIO
    )
    if price_drop_enabled and dropped and (passes_thresholds(c, thresholds) or c.flip_score >= 70):
        decisions.append(
            AlertDecision(
                AlertType.PRICE_DROP,
                AlertPriority.HIGH if c.flip_score >= 80 else AlertPriority.NORMAL,
                f"drop:{root}:{c.price}",
                f"📉 PRICE DROP · {c.title}"[:200],
                f"Da {_eur(c.previous_price)} a {_eur(c.price)}. {line}",
            )
        )

    already_ultra = any(d.type == AlertType.ULTRA_DEAL for d in decisions)
    if watchlist_enabled:
        for w in watchlists:
            if matches_watchlist(c, w):
                decisions.append(
                    AlertDecision(
                        AlertType.WATCHLIST_MATCH,
                        AlertPriority.HIGH if already_ultra else AlertPriority.NORMAL,
                        f"watch:{w.id}:{root}",
                        f"👀 {w.name} · {c.title}"[:200],
                        line,
                        watchlist_id=w.id,
                    )
                )

    if new_opportunity_enabled and not already_ultra and c.is_new and passes_thresholds(c, thresholds):
        decisions.append(
            AlertDecision(
                AlertType.NEW_OPPORTUNITY,
                AlertPriority.NORMAL,
                f"new:{root}",
                f"✨ Nuova opportunità · {c.title}"[:200],
                line,
            )
        )
    return decisions
