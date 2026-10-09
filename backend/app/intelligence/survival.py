"""Time-to-sell model: how price changes how fast an item sells (hazard / survival analysis).

Observations are ``(days, sold, ratio)`` for comparable listings: how long each was online
(``days``), whether it sold (``sold``) or was still on sale when last seen (right-censored), and its
asking price relative to the market reference (``ratio``, 1.0 = at the market median).

Model: a constant hazard that falls with price, ``h(ratio) = h0 * exp(-beta * (ratio - 1))``.
For a given ``beta`` the maximum-likelihood ``h0`` is ``events / sum(days * exp(-beta*(ratio-1)))``;
``beta`` is found by maximising that profile likelihood (golden-section search). With too few sales
the fit is *not reliable* and says so: the caller then uses an explicit prior (documented assumption)
instead of pretending to have measured something.

From the fit: P(sold within d days), expected days, and the price that maximises profit per day
(not the highest price) subject to a minimum profit, plus a markdown plan whose waiting times
come from the curve itself.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

MIN_EVENTS = 8
BETA_MAX = 20.0
PRIOR_BETA = 4.0  # assumption when the data cannot say: +10% price -> ~33% slower
PRIOR_DAYS = 21.0  # assumption: median-priced item sells in ~3 weeks
MAX_DAYS = 365.0


@dataclass(frozen=True)
class Obs:
    days: float  # time online, > 0
    sold: bool  # False: still on sale at last sight (censored)
    ratio: float  # asking price / market reference, > 0


@dataclass(frozen=True)
class SurvivalFit:
    h0: float  # hazard per day at ratio 1.0
    beta: float  # price sensitivity: how fast the hazard falls as the price rises
    n: int
    events: int
    reliable: bool
    source: str  # "measured" or "prior"

    @property
    def median_days_at_market(self) -> float:
        return math.log(2) / self.h0


def _clean(obs: Sequence[Obs]) -> list[Obs]:
    return [
        o
        for o in obs
        if math.isfinite(o.days) and o.days > 0 and math.isfinite(o.ratio) and 0.05 <= o.ratio <= 5.0
    ]


def _profile(obs: list[Obs], beta: float) -> tuple[float, float]:
    """(log-likelihood, h0) at ``beta``."""
    exposure = sum(o.days * math.exp(-beta * (o.ratio - 1.0)) for o in obs)
    events = sum(1 for o in obs if o.sold)
    if exposure <= 0 or events == 0:
        return -math.inf, 0.0
    h0 = events / exposure
    ll = sum(math.log(h0) - beta * (o.ratio - 1.0) for o in obs if o.sold) - events  # sum(h_i t_i) = events
    return ll, h0


def prior_fit(n: int = 0, events: int = 0) -> SurvivalFit:
    return SurvivalFit(math.log(2) / PRIOR_DAYS, PRIOR_BETA, n, events, False, "prior")


def fit_hazard(obs: Sequence[Obs], min_events: int = MIN_EVENTS) -> SurvivalFit:
    data = _clean(obs)
    events = sum(1 for o in data if o.sold)
    if events < min_events or len({round(o.ratio, 2) for o in data}) < 3:
        return prior_fit(len(data), events)
    lo, hi = 0.0, BETA_MAX
    g = (math.sqrt(5) - 1) / 2
    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    fa, fb = _profile(data, a)[0], _profile(data, b)[0]
    for _ in range(60):
        if fa > fb:
            hi, b, fb = b, a, fa
            a = hi - g * (hi - lo)
            fa = _profile(data, a)[0]
        else:
            lo, a, fa = a, b, fb
            b = lo + g * (hi - lo)
            fb = _profile(data, b)[0]
    beta = (lo + hi) / 2
    ll, h0 = _profile(data, beta)
    if not math.isfinite(ll) or h0 <= 0:
        return prior_fit(len(data), events)
    return SurvivalFit(h0, beta, len(data), events, True, "measured")


def hazard(fit: SurvivalFit, ratio: float) -> float:
    return fit.h0 * math.exp(-fit.beta * (ratio - 1.0))


def p_sold_by(fit: SurvivalFit, ratio: float, days: float) -> float:
    return 1.0 - math.exp(-hazard(fit, ratio) * max(0.0, days))


def expected_days(fit: SurvivalFit, ratio: float) -> float:
    return min(MAX_DAYS, 1.0 / hazard(fit, ratio))


def median_days(fit: SurvivalFit, ratio: float) -> float:
    return min(MAX_DAYS, math.log(2) / hazard(fit, ratio))


@dataclass(frozen=True)
class PricePoint:
    price: float
    ratio: float
    expected_days: float
    p_sold_30d: float
    profit: float
    profit_per_day: float


def price_curve(
    fit: SurvivalFit,
    reference: float,
    profit_at: Callable[[float], float],
    ratios: Sequence[float] | None = None,
) -> list[PricePoint]:
    """Profit and speed along a grid of prices around the market reference."""
    grid = ratios or [round(0.5 + 0.02 * i, 2) for i in range(0, 76)]  # 0.50 .. 2.00
    out = []
    for r in grid:
        price = reference * r
        days = expected_days(fit, r)
        profit = profit_at(price)
        out.append(PricePoint(round(price, 2), r, days, p_sold_30d(fit, r), profit, profit / max(days, 1.0)))
    return out


def p_sold_30d(fit: SurvivalFit, ratio: float) -> float:
    return p_sold_by(fit, ratio, 30.0)


def best_price(
    fit: SurvivalFit,
    reference: float,
    profit_at: Callable[[float], float],
    min_profit: float = 0.0,
    max_days: float | None = None,
) -> PricePoint | None:
    """The price that maximises profit per day among those that clear ``min_profit``."""
    pts = [
        p
        for p in price_curve(fit, reference, profit_at)
        if p.profit >= min_profit and (max_days is None or p.expected_days <= max_days)
    ]
    return max(pts, key=lambda p: p.profit_per_day) if pts else None


@dataclass(frozen=True)
class MarkdownStep:
    day: int  # days after listing
    price: float
    why: str


def markdown_plan(
    fit: SurvivalFit,
    reference: float,
    start_price: float,
    floor: float,
    steps: int = 4,
) -> list[MarkdownStep]:
    """Lower the price in steps from ``start_price`` to ``floor``.

    How long to wait at each price comes from the curve: the day by which half of the items at that
    price would have sold (bounded to 3..14 days). If it has not sold by then, the price is probably
    too high and the next step is taken."""
    if start_price <= floor or steps < 1:
        return [MarkdownStep(0, round(start_price, 2), "prezzo di partenza = minimo accettabile")]
    plan: list[MarkdownStep] = []
    day = 0
    ratio_step = (math.log(start_price) - math.log(floor)) / steps
    for k in range(steps + 1):
        price = math.exp(math.log(start_price) - ratio_step * k)
        price = max(floor, price)
        wait = max(3, min(14, round(median_days(fit, price / reference))))
        if k == 0:
            why = "prezzo di partenza"
        elif k == steps:
            why = "prezzo minimo: sotto non resta il margine richiesto"
        else:
            why = f"nessuna vendita dopo {day} giorni: il prezzo è probabilmente alto"
        plan.append(MarkdownStep(day, round(price, 2), why))
        day += wait
    return plan
