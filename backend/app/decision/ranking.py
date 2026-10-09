"""Ordering opportunities: by verdict first, then by what they are worth once risk is counted."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.decision.engine import Decision


@dataclass(frozen=True)
class Ranked:
    id: str
    decision: Decision


def rank_opportunities(items: Iterable[Ranked]) -> list[Ranked]:
    """Best first. A BUY always sits above a WATCHLIST; inside a verdict the risk-adjusted value
    (discounted by confidence) decides, so a sure 15 EUR can beat an uncertain 20 EUR (case H).
    Ties break on the id, so the order is stable."""
    return sorted(items, key=lambda r: (-r.decision.verdict.rank, -r.decision.rank_value, r.id))
