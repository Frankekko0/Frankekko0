"""Capital allocation: the best set of purchases inside a budget (exact 0/1 knapsack).

The question is not "which are the ten best deals" but "which combination earns the most inside
this budget, this per-item cap and this number of items". The objective is the *risk-adjusted*
profit, never the raw one. The search is exact (dynamic programming over whole euros, costs
rounded up so the budget can never be exceeded), not greedy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from app.decision.engine import DecisionVerdict

MAX_CELLS = 40_000_000  # items x slots x budget units: beyond this the unit grows


@dataclass(frozen=True)
class Candidate:
    id: str
    cost: Decimal  # what it takes out of the budget (price at the agreed offer + fees)
    value: float  # risk-adjusted expected profit, EUR
    verdict: DecisionVerdict
    risk: int = 0


@dataclass(frozen=True)
class CapitalRules:
    budget: Decimal
    max_per_item: Decimal | None = None
    max_items: int | None = None
    max_risk: int | None = None
    min_value: float = 0.0  # a candidate must be worth at least this to be considered
    include_negotiate: bool = False


@dataclass
class Allocation:
    selected: list[Candidate] = field(default_factory=list)
    total_cost: Decimal = Decimal("0")
    total_value: float = 0.0
    left: Decimal = Decimal("0")
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (id, reason)

    @property
    def ids(self) -> list[str]:
        return [c.id for c in self.selected]


def _eligible(c: Candidate, rules: CapitalRules) -> str | None:
    """None when the candidate may compete for the budget, else why not."""
    ok = (DecisionVerdict.STRONG_BUY, DecisionVerdict.BUY)
    if c.verdict not in ok and not (rules.include_negotiate and c.verdict == DecisionVerdict.NEGOTIATE):
        return f"verdetto {c.verdict.label}: non è un acquisto"
    if c.cost <= 0:
        return "costo non valido"
    if rules.max_per_item is not None and c.cost > rules.max_per_item:
        return "oltre il massimo per articolo"
    if rules.max_risk is not None and c.risk > rules.max_risk:
        return "rischio sopra il limite"
    if c.cost > rules.budget:
        return "oltre il budget"
    if c.value <= rules.min_value:
        return "valore atteso troppo basso"
    return None


def allocate_capital(candidates: list[Candidate], rules: CapitalRules) -> Allocation:
    out = Allocation(left=rules.budget)
    pool: list[Candidate] = []
    for c in sorted(candidates, key=lambda c: c.id):
        why = _eligible(c, rules)
        if why:
            out.rejected.append((c.id, why))
        else:
            pool.append(c)
    if not pool or rules.budget <= 0:
        return out

    slots = min(len(pool), rules.max_items) if rules.max_items is not None else len(pool)
    if slots <= 0:
        out.rejected.extend((c.id, "limite di articoli raggiunto") for c in pool)
        return out
    unit = 1
    while len(pool) * (slots + 1) * (math.floor(rules.budget / unit) + 1) > MAX_CELLS:
        unit *= 2
    capacity = math.floor(rules.budget / unit)
    weights = [max(1, math.ceil(c.cost / unit)) for c in pool]

    # best[k][b]: best value using at most k items and at most b units; choice for the walk back.
    best = [[0.0] * (capacity + 1) for _ in range(slots + 1)]
    taken: list[list[list[bool]]] = []
    for i, c in enumerate(pool):
        w = weights[i]
        row = [[False] * (capacity + 1) for _ in range(slots + 1)]
        for k in range(slots, 0, -1):
            prev, cur = best[k - 1], best[k]
            for b in range(capacity, w - 1, -1):
                candidate_value = prev[b - w] + c.value
                if candidate_value > cur[b] + 1e-12:
                    cur[b] = candidate_value
                    row[k][b] = True
        taken.append(row)

    k, b = slots, capacity
    chosen: list[int] = []
    for i in range(len(pool) - 1, -1, -1):
        if k > 0 and taken[i][k][b]:
            chosen.append(i)
            k -= 1
            b -= weights[i]
    chosen.reverse()
    out.selected = [pool[i] for i in chosen]
    out.total_cost = sum((c.cost for c in out.selected), Decimal("0"))
    out.total_value = round(sum(c.value for c in out.selected), 2)
    out.left = rules.budget - out.total_cost
    picked = {c.id for c in out.selected}
    out.rejected.extend(
        (c.id, "esclusa: la combinazione scelta rende di più col budget") for c in pool if c.id not in picked
    )
    return out
