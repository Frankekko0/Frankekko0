"""The plain baseline the AI must beat, and what happens where it does not.

Baseline: buy when the *median of the comparables* leaves the minimum profit and ROI after all costs. No
model, no photos. Every claim that the system is better is a comparison with this on real outcomes, per
segment: where the baseline does at least as well (profit per euro per day, with a bootstrap bound), the
agent degrades to the baseline for that segment.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MIN_PER_SEGMENT = 12


@dataclass(frozen=True)
class Candidate:
    case_id: str
    segment: str
    cost: float
    median_resale_net: float | None  # median of the comparables after selling costs; None: unknown
    ai_buy: bool
    realized_profit: float | None  # real outcome of buying it; None: not bought, unknown
    days: float | None


def baseline_buys(c: Candidate, min_profit: float, min_roi: float) -> bool:
    if c.median_resale_net is None or c.cost <= 0:
        return False
    profit = c.median_resale_net - c.cost
    return profit >= min_profit and profit / c.cost >= min_roi


def _per_euro_day(xs: Sequence[Candidate]) -> float | None:
    done = [c for c in xs if c.realized_profit is not None and c.days]
    if not done:
        return None
    return sum(
        c.realized_profit / c.cost / max(c.days or 1.0, 1.0) for c in done if c.realized_profit is not None
    ) / len(done)


@dataclass(frozen=True)
class SegmentResult:
    segment: str
    n: int
    ai: float | None
    baseline: float | None
    degrade_to_baseline: bool
    reason: str


def compare_by_segment(
    cases: Sequence[Candidate],
    min_profit: float = 5.0,
    min_roi: float = 0.3,
    seed: int = 5,
    resamples: int = 400,
) -> list[SegmentResult]:
    by_seg: dict[str, list[Candidate]] = defaultdict(list)
    for c in cases:
        by_seg[c.segment].append(c)
    rng = random.Random(seed)
    out: list[SegmentResult] = []
    for seg, xs in sorted(by_seg.items()):
        ai = [c for c in xs if c.ai_buy]
        bl = [c for c in xs if baseline_buys(c, min_profit, min_roi)]
        a, b = _per_euro_day(ai), _per_euro_day(bl)
        known = [c for c in xs if c.realized_profit is not None]
        if len(known) < MIN_PER_SEGMENT or a is None:
            out.append(
                SegmentResult(
                    seg, len(xs), a, b, False, f"meno di {MIN_PER_SEGMENT} esiti reali: non misurabile"
                )
            )
            continue
        if b is None:
            out.append(
                SegmentResult(
                    seg, len(xs), a, b, False, "il baseline non avrebbe comprato nulla con esito noto"
                )
            )
            continue
        wins = 0
        for _ in range(resamples):
            s = [known[rng.randrange(len(known))] for _ in known]
            sa = _per_euro_day([c for c in s if c.ai_buy])
            sb = _per_euro_day([c for c in s if baseline_buys(c, min_profit, min_roi)])
            if sa is not None and sb is not None and sa > sb:
                wins += 1
        ai_wins = wins / resamples >= 0.8
        out.append(
            SegmentResult(
                seg,
                len(xs),
                a,
                b,
                not ai_wins,
                "l'AI batte il baseline con prove sufficienti"
                if ai_wins
                else "l'AI non batte il baseline: si usa il baseline",
            )
        )
    return out


def summary(results: list[SegmentResult]) -> dict[str, Any]:
    measurable = [r for r in results if r.ai is not None and r.baseline is not None]
    return {
        "segments": len(results),
        "measurable": len(measurable),
        "degraded": [r.segment for r in results if r.degrade_to_baseline],
        "note": "Nessuna affermazione di superiorità senza esiti reali per segmento."
        if not measurable
        else "Confronto su profitto per euro per giorno degli articoli comprati.",
    }
