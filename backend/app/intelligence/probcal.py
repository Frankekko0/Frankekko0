"""Calibration of probabilities (sale within N days, authenticity, a real defect).

A probability is *calibrated* when events predicted at 70% happen about 70% of the time. Measured with the
Brier score and the expected calibration error (ECE) on outcomes. When the raw probabilities are off, an
isotonic fit (pool-adjacent-violators) or a Platt sigmoid is learned on a *training* part and kept only
if it lowers the Brier score on a held-out part, so the reported improvement is the one to expect later.
There are no real outcomes at the start: this is a harness that reports "not enough data" until there are.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

MIN_OUTCOMES = 30


def brier(p: Sequence[float], y: Sequence[int]) -> float:
    return sum((a - b) ** 2 for a, b in zip(p, y, strict=True)) / len(p)


@dataclass(frozen=True)
class Bin:
    low: float
    high: float
    n: int
    mean_predicted: float
    observed_rate: float


def reliability(p: Sequence[float], y: Sequence[int], bins: int = 10) -> list[Bin]:
    out: list[Bin] = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        idx = [j for j, v in enumerate(p) if (lo <= v < hi) or (i == bins - 1 and v == 1.0)]
        if not idx:
            continue
        out.append(
            Bin(lo, hi, len(idx), sum(p[j] for j in idx) / len(idx), sum(y[j] for j in idx) / len(idx))
        )
    return out


def ece(p: Sequence[float], y: Sequence[int], bins: int = 10) -> float:
    n = len(p)
    return sum(b.n / n * abs(b.mean_predicted - b.observed_rate) for b in reliability(p, y, bins))


class Isotonic:
    """Non-decreasing step function fitted with pool-adjacent-violators."""

    def __init__(self, xs: list[float], ys: list[float]) -> None:
        self.xs, self.ys = xs, ys

    @classmethod
    def fit(cls, p: Sequence[float], y: Sequence[int]) -> Isotonic:
        pairs = sorted(zip(p, y, strict=True))
        blocks: list[list[float]] = []  # [sum_y, count, min_x, max_x]
        for x, t in pairs:
            blocks.append([float(t), 1.0, x, x])
            while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
                b = blocks.pop()
                blocks[-1][0] += b[0]
                blocks[-1][1] += b[1]
                blocks[-1][3] = b[3]
        return cls([(b[2] + b[3]) / 2 for b in blocks], [b[0] / b[1] for b in blocks])

    def __call__(self, x: float) -> float:
        xs, ys = self.xs, self.ys
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= x:
                lo = mid
            else:
                hi = mid
        t = (x - xs[lo]) / (xs[hi] - xs[lo]) if xs[hi] > xs[lo] else 0.0
        return ys[lo] + t * (ys[hi] - ys[lo])


class Platt:
    """Logistic recalibration ``sigmoid(a * logit(p) + b)`` by Newton's method."""

    def __init__(self, a: float, b: float) -> None:
        self.a, self.b = a, b

    @staticmethod
    def _logit(p: float) -> float:
        p = min(1 - 1e-6, max(1e-6, p))
        return math.log(p / (1 - p))

    @classmethod
    def fit(cls, p: Sequence[float], y: Sequence[int], iters: int = 50) -> Platt:
        a, b = 1.0, 0.0
        xs = [cls._logit(v) for v in p]
        for _ in range(iters):
            ga = gb = haa = hab = hbb = 0.0
            for x, t in zip(xs, y, strict=True):
                q = 1 / (1 + math.exp(-(a * x + b)))
                w = q * (1 - q) + 1e-9
                ga += (q - t) * x
                gb += q - t
                haa += w * x * x
                hab += w * x
                hbb += w
            det = haa * hbb - hab * hab
            if abs(det) < 1e-12:
                break
            da = (hbb * ga - hab * gb) / det
            db = (haa * gb - hab * ga) / det
            a, b = a - da, b - db
            if abs(da) + abs(db) < 1e-8:
                break
        return cls(a, b)

    def __call__(self, p: float) -> float:
        return 1 / (1 + math.exp(-(self.a * self._logit(p) + self.b)))


@dataclass(frozen=True)
class CalibrationReport:
    status: str  # "not_enough_data" | "calibrated_ok" | "recalibrated" | "kept_raw"
    n: int
    method: str | None
    brier_before: float | None
    brier_after: float | None
    ece_before: float | None
    ece_after: float | None
    note: str


def evaluate_and_calibrate(
    p: Sequence[float], y: Sequence[int], train_fraction: float = 0.6
) -> tuple[CalibrationReport, Isotonic | Platt | None]:
    """The first ``train_fraction`` (outcomes in time order) trains, the rest judges."""
    n = len(p)
    if n < MIN_OUTCOMES:
        return (
            CalibrationReport(
                "not_enough_data",
                n,
                None,
                None,
                None,
                None,
                None,
                f"servono almeno {MIN_OUTCOMES} esiti reali",
            ),
            None,
        )
    cut = int(n * train_fraction)
    ptr, ytr, pte, yte = p[:cut], y[:cut], p[cut:], y[cut:]
    if len(set(ytr)) < 2 or not pte:
        return CalibrationReport(
            "not_enough_data", n, None, None, None, None, None, "esiti di una sola classe"
        ), None
    b0, e0 = brier(pte, yte), ece(pte, yte)
    best: tuple[str, float, Isotonic | Platt] | None = None
    for name, model in (("isotonic", Isotonic.fit(ptr, ytr)), ("platt", Platt.fit(ptr, ytr))):
        score = brier([model(v) for v in pte], yte)
        if best is None or score < best[1]:
            best = (name, score, model)
    assert best is not None
    name, b1, model = best
    if b1 < b0 - 1e-4:
        e1 = ece([model(v) for v in pte], yte)
        return CalibrationReport(
            "recalibrated",
            n,
            name,
            b0,
            b1,
            e0,
            e1,
            "calibrazione appresa e provata su esiti non usati per impararla",
        ), model
    status = "calibrated_ok" if e0 <= 0.05 else "kept_raw"
    return CalibrationReport(
        status, n, None, b0, b0, e0, e0, "nessuna correzione migliora i dati non visti"
    ), None
