"""Concentration limits: no more than a share of capital in one brand, category, size, price band or seller.

Shares are measured on the cost of what is held plus the candidate. With fewer than ``min_items`` items
nothing is blocked (the first purchases cannot be "concentrated" by definition).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Holding:
    cost: float
    brand: str | None = None
    category: str | None = None
    size: str | None = None
    price_band: str | None = None
    seller: str | None = None


@dataclass(frozen=True)
class ExposureLimits:
    brand: float = 0.40
    category: float = 0.50
    size: float = 0.50
    price_band: float = 0.60
    seller: float = 0.30
    min_items: int = 4


@dataclass(frozen=True)
class Violation:
    dimension: str
    key: str
    share_after: float
    limit: float

    def label(self) -> str:
        return f"{self.dimension} «{self.key}»: {self.share_after:.0%} del capitale investito (limite {self.limit:.0%})"


DIMENSIONS = ("brand", "category", "size", "price_band", "seller")


def check_exposure(
    held: Sequence[Holding], candidate: Holding, limits: ExposureLimits | None = None
) -> list[Violation]:
    limits = limits or ExposureLimits()
    items = [*held, candidate]
    if len(items) < limits.min_items:
        return []
    total = sum(h.cost for h in items)
    if total <= 0:
        return []
    out: list[Violation] = []
    for dim in DIMENSIONS:
        key = getattr(candidate, dim)
        if not key:
            continue
        share = sum(h.cost for h in items if getattr(h, dim) == key) / total
        limit = getattr(limits, dim)
        if share > limit:
            out.append(Violation(dim, str(key), share, limit))
    return out


def shares(held: Sequence[Holding], dimension: str) -> dict[str, float]:
    total = sum(h.cost for h in held)
    acc: dict[str, float] = defaultdict(float)
    for h in held:
        key = getattr(h, dimension)
        if key:
            acc[str(key)] += h.cost
    return {k: v / total for k, v in acc.items()} if total > 0 else {}
