"""What happened to the items we turned down: measuring false negatives.

A *false negative* is an item the system rejected (PASS, WATCHLIST or insufficient evidence) that then
sold quickly at no more than the price analysed, although the economics we computed were already
profitable. That points at a veto or a threshold that was too cautious (it was not the money). The report
names the vetoes responsible; it only proposes - the thresholds are never changed on their own.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

REJECTED = ("PASS", "WATCHLIST", "INSUFFICIENT_EVIDENCE", "NEGOTIATE")


@dataclass(frozen=True)
class Discarded:
    opportunity_id: str
    verdict: str
    analysed_price: float
    expected_profit: float | None  # our own estimate at the analysed price
    sold_after_days: float | None  # None: still on sale or unknown
    sold_price: float | None
    vetoes: tuple[str, ...] = ()
    never_bought: bool = True


@dataclass
class CounterfactualReport:
    rejected: int
    sold_quickly: int
    false_negatives: list[Discarded] = field(default_factory=list)
    by_veto: dict[str, int] = field(default_factory=dict)
    rate: float | None = None  # false negatives / rejected that sold
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "rejected": self.rejected,
            "sold_quickly": self.sold_quickly,
            "false_negatives": [d.opportunity_id for d in self.false_negatives],
            "n_false_negatives": len(self.false_negatives),
            "by_veto": self.by_veto,
            "rate": None if self.rate is None else round(self.rate, 3),
            "note": self.note,
        }


def evaluate(items: list[Discarded], min_profit: float, window_days: float = 7.0) -> CounterfactualReport:
    rejected = [d for d in items if d.verdict in REJECTED and d.never_bought]
    sold = [
        d
        for d in rejected
        if d.sold_after_days is not None
        and d.sold_after_days <= window_days
        and (d.sold_price is None or d.sold_price <= d.analysed_price * 1.02)
    ]
    fns = [d for d in sold if d.expected_profit is not None and d.expected_profit >= min_profit]
    veto = Counter(v for d in fns for v in d.vetoes) if fns else Counter()
    note = (
        "Nessun articolo scartato è stato osservato come venduto: i falsi negativi non sono misurabili."
        if not sold
        else "Falsi negativi misurati sugli scartati venduti entro la finestra; le soglie vanno riviste solo con prove ripetute."
    )
    return CounterfactualReport(
        rejected=len(rejected),
        sold_quickly=len(sold),
        false_negatives=fns,
        by_veto=dict(veto),
        rate=(len(fns) / len(sold)) if sold else None,
        note=note,
    )
