"""Drift detection: did prices, the brand mix or photo quality shift away from what the rules were tuned on?

* PSI (population stability index) over quantile bins of a baseline sample: < 0.10 stable, 0.10-0.25
  warning, > 0.25 alarm.
* Two-sample Kolmogorov-Smirnov distance with its 5% critical value.
* PSI on categorical mixes (brands, categories).

An alarm triggers a *controlled* recalibration proposal (champion/challenger), never an automatic change.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

PSI_WARN = 0.10
PSI_ALARM = 0.25
MIN_SAMPLE = 30


def _edges(baseline: Sequence[float], bins: int) -> list[float]:
    xs = sorted(baseline)
    cuts = [xs[min(len(xs) - 1, int(len(xs) * i / bins))] for i in range(1, bins)]
    out: list[float] = []
    for c in cuts:
        if not out or c > out[-1]:
            out.append(c)
    return out


def _share(xs: Sequence[float], edges: list[float]) -> list[float]:
    counts = [0] * (len(edges) + 1)
    for x in xs:
        i = 0
        while i < len(edges) and x > edges[i]:
            i += 1
        counts[i] += 1
    n = max(1, len(xs))
    return [max(c / n, 1e-4) for c in counts]


def psi(baseline: Sequence[float], recent: Sequence[float], bins: int = 10) -> float:
    edges = _edges(baseline, bins)
    e, a = _share(baseline, edges), _share(recent, edges)
    return sum((ai - ei) * math.log(ai / ei) for ei, ai in zip(e, a, strict=True))


def categorical_psi(baseline: Counter[str] | dict[str, int], recent: Counter[str] | dict[str, int]) -> float:
    keys = set(baseline) | set(recent)
    nb, nr = max(1, sum(baseline.values())), max(1, sum(recent.values()))
    total = 0.0
    for k in keys:
        e = max(baseline.get(k, 0) / nb, 1e-4)
        a = max(recent.get(k, 0) / nr, 1e-4)
        total += (a - e) * math.log(a / e)
    return total


def ks(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    """(statistic, 5% critical value)."""
    xs, ys = sorted(a), sorted(b)
    i = j = 0
    d = 0.0
    while i < len(xs) and j < len(ys):
        v = min(xs[i], ys[j])
        while i < len(xs) and xs[i] <= v:
            i += 1
        while j < len(ys) and ys[j] <= v:
            j += 1
        d = max(d, abs(i / len(xs) - j / len(ys)))
    crit = 1.358 * math.sqrt((len(xs) + len(ys)) / (len(xs) * len(ys)))
    return d, crit


@dataclass(frozen=True)
class DriftAlarm:
    metric: str
    value: float
    threshold: float
    level: str  # warning | alarm
    note: str


def detect(metric: str, baseline: Sequence[float], recent: Sequence[float], what: str) -> DriftAlarm | None:
    if len(baseline) < MIN_SAMPLE or len(recent) < MIN_SAMPLE:
        return None
    value = psi(baseline, recent)
    if value > PSI_ALARM:
        return DriftAlarm(
            metric, value, PSI_ALARM, "alarm", f"{what}: la distribuzione è cambiata molto (PSI {value:.2f})"
        )
    if value > PSI_WARN:
        return DriftAlarm(
            metric, value, PSI_WARN, "warning", f"{what}: la distribuzione si sta spostando (PSI {value:.2f})"
        )
    return None


def detect_mix(metric: str, baseline: dict[str, int], recent: dict[str, int], what: str) -> DriftAlarm | None:
    if sum(baseline.values()) < MIN_SAMPLE or sum(recent.values()) < MIN_SAMPLE:
        return None
    value = categorical_psi(baseline, recent)
    if value > PSI_ALARM:
        return DriftAlarm(
            metric, value, PSI_ALARM, "alarm", f"{what}: la composizione è cambiata molto (PSI {value:.2f})"
        )
    if value > PSI_WARN:
        return DriftAlarm(
            metric, value, PSI_WARN, "warning", f"{what}: la composizione si sta spostando (PSI {value:.2f})"
        )
    return None
