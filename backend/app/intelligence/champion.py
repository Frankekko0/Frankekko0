"""Champion / challenger: a new rule replaces the current one only if it wins on metrics fixed in advance.

Both policies are evaluated on the same historical cases that have a real outcome. The primary metric
(``profit per euro per day`` of the cases a policy would have bought) and the promotion rule are
pre-registered here, not chosen after seeing the numbers:

* the challenger must buy at least ``MIN_BOUGHT`` cases,
* its mean advantage over the champion must be positive with a one-sided 90% bootstrap bound above
  ``MARGIN``, and it must not have a worse loss rate.

Only cases with outcomes can be judged (a selection bias, said in the report). Nothing is switched on
automatically: the result is a recommendation that a person approves.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

MIN_BOUGHT = 20
MARGIN = 0.0005  # euros of profit per euro invested per day
MIN_CASES = 30


@dataclass(frozen=True)
class Outcome:
    case_id: str
    cost: float
    profit: float
    days: float  # time held until sale (>= 1)
    segment: str = ""

    @property
    def per_euro_day(self) -> float:
        return self.profit / self.cost / max(self.days, 1.0)


Policy = Callable[[Outcome], bool]


@dataclass(frozen=True)
class Evaluation:
    bought: int
    mean_per_euro_day: float | None
    loss_rate: float | None
    total_profit: float


def evaluate_policy(cases: Sequence[Outcome], policy: Policy) -> Evaluation:
    chosen = [c for c in cases if policy(c)]
    if not chosen:
        return Evaluation(0, None, None, 0.0)
    return Evaluation(
        len(chosen),
        sum(c.per_euro_day for c in chosen) / len(chosen),
        sum(1 for c in chosen if c.profit < 0) / len(chosen),
        sum(c.profit for c in chosen),
    )


@dataclass(frozen=True)
class Verdict:
    promote: bool
    reason: str
    champion: Evaluation
    challenger: Evaluation
    lower_bound: float | None
    note: str = "solo i casi con un esito reale sono valutabili: i rifiutati non hanno un risultato"

    def as_dict(self) -> dict[str, Any]:
        e = lambda x: {  # noqa: E731
            "bought": x.bought,
            "mean_per_euro_day": None if x.mean_per_euro_day is None else round(x.mean_per_euro_day, 5),
            "loss_rate": None if x.loss_rate is None else round(x.loss_rate, 3),
            "total_profit": round(x.total_profit, 2),
        }
        return {
            "promote": self.promote,
            "reason": self.reason,
            "champion": e(self.champion),
            "challenger": e(self.challenger),
            "advantage_lower_bound": None if self.lower_bound is None else round(self.lower_bound, 5),
            "note": self.note,
        }


def compare(
    cases: Sequence[Outcome], champion: Policy, challenger: Policy, seed: int = 11, resamples: int = 600
) -> Verdict:
    ec, ex = evaluate_policy(cases, champion), evaluate_policy(cases, challenger)
    if len(cases) < MIN_CASES:
        return Verdict(
            False, f"servono almeno {MIN_CASES} casi con esito reale (ce ne sono {len(cases)})", ec, ex, None
        )
    if ex.bought < MIN_BOUGHT:
        return Verdict(
            False, f"lo sfidante avrebbe comprato solo {ex.bought} casi (minimo {MIN_BOUGHT})", ec, ex, None
        )
    rng = random.Random(seed)
    diffs: list[float] = []
    n = len(cases)
    for _ in range(resamples):
        sample = [cases[rng.randrange(n)] for _ in range(n)]
        a, b = evaluate_policy(sample, champion), evaluate_policy(sample, challenger)
        diffs.append((b.mean_per_euro_day or 0.0) - (a.mean_per_euro_day or 0.0))
    diffs.sort()
    lower = diffs[int(0.10 * len(diffs))]
    worse_loss = (ex.loss_rate or 0.0) > (ec.loss_rate or 0.0) + 0.02
    if lower > MARGIN and not worse_loss:
        return Verdict(True, "lo sfidante vince sulla metrica fissata in anticipo", ec, ex, lower)
    why = "più perdite del campione" if worse_loss else "il vantaggio non è dimostrato con margine"
    return Verdict(False, f"il campione resta: {why}", ec, ex, lower)
