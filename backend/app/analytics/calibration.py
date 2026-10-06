"""Calibration of price estimates on real outcomes (continuous learning).

Every sale observed after an estimate (sold listings re-estimated with only the data available
before them, and the user's own resales) yields a residual ``log(realized / estimated)``. From
the residuals:

* **shift** - systematic error, overall and per category, brand and condition, shrunk towards the
  overall value when a group has few cases (``n / (n + K)``), so a handful of sales never swings
  the estimates;
* **range** - the 10th and 90th percentile of the corrected residuals, per confidence level: the
  realistic minimum and maximum resale prices then contain about 80% of real sales (instead of a
  fixed +/- percentage).

The calibration is fitted daily by the worker and stored in ``system_state`` (key
``price_calibration``) together with the measured errors before and after.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SHRINK_K = 25.0
MIN_CASES = 30
MIN_RANGE_CASES = 20
RANGE_Q = (0.10, 0.90)
STATE_KEY = "price_calibration"


def confidence_bucket(confidence: int) -> str:
    return "high" if confidence >= 75 else "mid" if confidence >= 50 else "low"


@dataclass
class CalibrationCase:
    expected: float
    realized: float
    category: str | None
    brand: str | None
    condition: str | None
    confidence: int
    weight: float = 1.0  # the user's own resales count more

    @property
    def residual(self) -> float:
        return math.log(self.realized / self.expected)


def _wmedian(values: list[tuple[float, float]]) -> float:
    pairs = sorted(values)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


def _quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


@dataclass
class Calibration:
    n: int = 0
    shift: float = 0.0
    groups: dict[str, dict[str, float]] = field(default_factory=dict)  # "cat:x" -> {dev, n}
    ranges: dict[str, dict[str, float]] = field(default_factory=dict)  # bucket -> {lo, hi, n}
    fitted_at: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.n >= MIN_CASES

    # ---------------------------------------------------------------- fit
    @classmethod
    def fit(
        cls,
        cases: list[CalibrationCase],
        groups: tuple[str, ...] = ("cat", "brand", "cond"),
        global_shift: bool = True,
    ) -> Calibration:
        """Residuals are weighted by the estimated price (errors cost euros, not percent)."""
        cases = [c for c in cases if c.expected > 0 and c.realized > 0]
        cal = cls(n=len(cases), fitted_at=datetime.now(UTC).isoformat())
        if len(cases) < MIN_CASES:
            return cal
        cal.shift = _wmedian([(c.residual, c.weight * c.expected) for c in cases]) if global_shift else 0.0
        for key_fn, prefix in (
            (lambda c: c.category, "cat"),
            (lambda c: c.brand, "brand"),
            (lambda c: c.condition, "cond"),
        ):
            if prefix not in groups:
                continue
            buckets: dict[str, list[CalibrationCase]] = {}
            for c in cases:
                if (k := key_fn(c)) is not None:
                    buckets.setdefault(k, []).append(c)
            for k, group in buckets.items():
                n = sum(c.weight for c in group)
                dev = _wmedian([(c.residual - cal.shift, c.weight * c.expected) for c in group])
                cal.groups[f"{prefix}:{k}"] = {"dev": dev * n / (n + SHRINK_K), "n": n}
        corrected: dict[str, list[float]] = {}
        for c in cases:
            r = c.residual - cal._correction(c.category, c.brand, c.condition)
            corrected.setdefault(confidence_bucket(c.confidence), []).append(r)
        everything = [r for rs in corrected.values() for r in rs]
        for bucket in ("low", "mid", "high"):
            rs = corrected.get(bucket, [])
            src = rs if len(rs) >= MIN_RANGE_CASES else everything
            cal.ranges[bucket] = {
                "lo": _quantile(src, RANGE_Q[0]),
                "hi": _quantile(src, RANGE_Q[1]),
                "n": len(rs),
            }
        return cal

    def _correction(self, category: str | None, brand: str | None, condition: str | None) -> float:
        total = self.shift
        for prefix, key in (("cat", category), ("brand", brand), ("cond", condition)):
            g = self.groups.get(f"{prefix}:{key}") if key else None
            if g:
                total += g["dev"]
        return total

    # ---------------------------------------------------------------- apply
    def apply(
        self,
        expected: float,
        category: str | None,
        brand: str | None,
        condition: str | None,
        confidence: int,
    ) -> tuple[float, float, float]:
        """(low, expected, high) calibrated resale prices."""
        corr = math.exp(self._correction(category, brand, condition))
        exp_cal = expected * corr
        rng = self.ranges.get(confidence_bucket(confidence)) or {"lo": -0.15, "hi": 0.1}
        return exp_cal * math.exp(rng["lo"]), exp_cal, exp_cal * math.exp(rng["hi"])

    # ---------------------------------------------------------------- storage
    def to_state(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "shift": self.shift,
            "groups": self.groups,
            "ranges": self.ranges,
            "fitted_at": self.fitted_at,
            "metrics": self.metrics,
        }

    @classmethod
    def from_state(cls, value: dict[str, Any] | None) -> Calibration:
        if not value:
            return cls()
        return cls(
            n=int(value.get("n", 0)),
            shift=float(value.get("shift", 0.0)),
            groups=dict(value.get("groups") or {}),
            ranges=dict(value.get("ranges") or {}),
            fitted_at=value.get("fitted_at"),
            metrics=dict(value.get("metrics") or {}),
        )


def describe_errors(pairs: list[tuple[float, float, float | None, float | None]]) -> dict[str, float]:
    """MAE, MAPE, median APE, bias and range coverage of (expected, realized, low, high) tuples."""
    if not pairs:
        return {}
    ape = [abs(e - r) / r for e, r, _, _ in pairs]
    return {
        "n": len(pairs),
        "mae_eur": round(sum(abs(e - r) for e, r, _, _ in pairs) / len(pairs), 2),
        "mape": round(sum(ape) / len(ape), 4),
        "median_ape": round(statistics.median(ape), 4),
        "bias": round(sum((e - r) / r for e, r, _, _ in pairs) / len(pairs), 4),
        "in_range": round(
            sum(1 for _, r, lo, hi in pairs if lo is not None and hi is not None and lo <= r <= hi)
            / len(pairs),
            4,
        ),
    }
