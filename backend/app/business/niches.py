"""Niches (brand x category x price band): which to scale, which to close, how much capital goes where.

A niche is judged on what it *realised*: profit per euro per day, share of winning sales, and whether the
recent results are better or worse than the earlier ones (its trend). Capital moves from declining niches to
growing ones, a fixed share (default 10%) is kept for exploring new niches, and a niche that has lost more than
its stop-loss share of the capital given to it is stopped.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

MIN_SALES = 3
EXPLORE_SHARE = 0.10
STOP_LOSS = 0.25  # of the capital allocated to the niche


@dataclass(frozen=True)
class NicheSale:
    niche: str
    cost: float
    profit: float
    days: float
    sold_on: date
    price: float


@dataclass(frozen=True)
class NicheStats:
    niche: str
    n: int
    profit: float
    per_euro_day: float
    win_rate: float
    trend: float  # recent minus earlier profit per euro per day
    loss: float  # realised loss in the niche (positive)

    @property
    def declining(self) -> bool:
        return self.n >= 2 * MIN_SALES and self.trend < -abs(self.per_euro_day) * 0.3

    @property
    def growing(self) -> bool:
        return self.n >= 2 * MIN_SALES and self.trend > abs(self.per_euro_day) * 0.3


def _ped(rows: list[NicheSale]) -> float:
    return sum(r.profit / r.cost / max(r.days, 1.0) for r in rows) / len(rows) if rows else 0.0


def stats(sales: list[NicheSale]) -> list[NicheStats]:
    by: dict[str, list[NicheSale]] = defaultdict(list)
    for s in sales:
        by[s.niche].append(s)
    out = []
    for niche, rows in by.items():
        rows = sorted(rows, key=lambda r: r.sold_on)
        half = len(rows) // 2
        trend = _ped(rows[half:]) - _ped(rows[:half]) if len(rows) >= 2 * MIN_SALES else 0.0
        out.append(
            NicheStats(
                niche,
                len(rows),
                sum(r.profit for r in rows),
                _ped(rows),
                sum(1 for r in rows if r.profit > 0) / len(rows),
                trend,
                sum(-r.profit for r in rows if r.profit < 0),
            )
        )
    return sorted(out, key=lambda s: -s.per_euro_day)


@dataclass(frozen=True)
class Allocation:
    niche: str
    action: str  # scale | hold | reduce | stop | explore
    share: float
    amount: float
    reason: str


def allocate(
    niches: list[NicheStats],
    capital: float,
    given: dict[str, float] | None = None,
    explore_share: float = EXPLORE_SHARE,
    stop_loss: float = STOP_LOSS,
) -> list[Allocation]:
    """``given``: capital each niche had so far (for the stop-loss); defaults to an equal split."""
    if capital <= 0:
        return []
    given = given or {n.niche: capital * (1 - explore_share) / max(1, len(niches)) for n in niches}
    weights: dict[str, float] = {}
    notes: dict[str, tuple[str, str]] = {}
    for n in niches:
        base = max(n.per_euro_day, 0.0)
        if n.n < MIN_SALES:
            weights[n.niche], notes[n.niche] = 0.0, ("hold", f"solo {n.n} vendite: troppo poche per decidere")
            continue
        if n.loss > stop_loss * given.get(n.niche, 0.0) and n.profit < 0:
            weights[n.niche], notes[n.niche] = (
                0.0,
                (
                    "stop",
                    f"perdite di {n.loss:.0f} € oltre lo stop-loss del {stop_loss:.0%} del capitale assegnato",
                ),
            )
        elif n.declining:
            weights[n.niche], notes[n.niche] = (
                base * 0.5,
                ("reduce", "il margine per euro al giorno sta calando"),
            )
        elif n.growing:
            weights[n.niche], notes[n.niche] = (
                base * 1.5,
                ("scale", "il margine per euro al giorno sta crescendo"),
            )
        else:
            weights[n.niche], notes[n.niche] = base, ("hold", "risultati stabili")
    total = sum(weights.values())
    pool = capital * (1 - explore_share)
    out = []
    for n in niches:
        share = (weights[n.niche] / total) * (1 - explore_share) if total > 0 else 0.0
        action, reason = notes[n.niche]
        out.append(
            Allocation(
                n.niche,
                action,
                round(share, 4),
                round(pool * (weights[n.niche] / total) if total > 0 else 0.0, 2),
                reason,
            )
        )
    out.append(
        Allocation(
            "(nuove nicchie)",
            "explore",
            explore_share,
            round(capital * explore_share, 2),
            "quota fissa per esplorare, con stop-loss",
        )
    )
    return out


def playbook(sales: list[NicheSale], search: str) -> dict[str, object]:
    """What the realised sales of a niche say about buying and reselling; what they cannot say is listed."""
    if len(sales) < MIN_SALES:
        return {
            "niche_search": search,
            "n": len(sales),
            "note": f"servono almeno {MIN_SALES} vendite per un playbook",
            "not_known": [],
        }
    wins = [s for s in sales if s.profit > 0]
    top = sorted(sales, key=lambda s: -s.profit / s.cost / max(s.days, 1.0))[: max(1, len(sales) // 2)]
    return {
        "niche_search": search,
        "n": len(sales),
        "max_buy_price": round(statistics.median(s.cost for s in top), 2),
        "resale_price_median": round(statistics.median(s.price for s in sales), 2),
        "days_to_sell_median": round(statistics.median(s.days for s in sales), 1),
        "win_rate": round(len(wins) / len(sales), 3),
        "not_known": [
            "difetti tipici e riparabili",
            "foto che convertono",
            "orari migliori",
            "stagionalità (servono 12 mesi)",
        ],
        "note": "Generato dalle tue vendite reali; ciò che non si può ricavare dai dati è elencato.",
    }
