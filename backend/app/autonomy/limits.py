"""The limits the user sets. Nothing is allowed that these do not allow: no budget, no purchases."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.intelligence.exposure import ExposureLimits


class LimitsError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    daily_budget: Decimal | None = None
    weekly_budget: Decimal | None = None
    max_per_item: Decimal | None = None
    max_items: int | None = None
    min_flip: int = 70
    min_confidence: int = 60
    max_risk: int = 40
    allowed_brands: tuple[str, ...] = ()  # empty: any brand
    allowed_categories: tuple[str, ...] = ()
    max_messages_per_day: int = 5
    max_reprices_per_day: int = 10  # markdowns proposed per day
    premortem_above: Decimal = Decimal("25")  # purchases from this cost up need a pre-mortem on record
    # Automatic suspension.
    max_error_rate: float = 0.30
    max_loss: Decimal = Decimal("50")
    max_forecast_error: float = 0.35
    window_days: int = 14
    dry_run_days: int = 7
    exposure: ExposureLimits = field(default_factory=ExposureLimits)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if k == "exposure":
                out[k] = dict(v.__dict__)
            elif isinstance(v, Decimal):
                out[k] = float(v)
            elif isinstance(v, tuple):
                out[k] = list(v)
            else:
                out[k] = v
        return out


MONEY_KEYS = ("daily_budget", "weekly_budget", "max_per_item", "premortem_above", "max_loss")
INT_KEYS = {
    "max_items": (1, 500), "min_flip": (0, 100), "min_confidence": (0, 100), "max_risk": (0, 100),
    "max_messages_per_day": (0, 100), "max_reprices_per_day": (0, 100), "window_days": (1, 90),
    "dry_run_days": (0, 60),
}  # fmt: skip
FLOAT_KEYS = {"max_error_rate": (0.0, 1.0), "max_forecast_error": (0.0, 5.0)}
LIST_KEYS = ("allowed_brands", "allowed_categories")


def parse_limits(raw: dict[str, Any] | None) -> Limits:
    """Validate what the user sent; unknown keys are refused, not ignored."""
    raw = dict(raw or {})
    known = set(Limits.__dataclass_fields__)
    unknown = set(raw) - known
    if unknown:
        raise LimitsError(f"limiti sconosciuti: {', '.join(sorted(unknown))}")
    kw: dict[str, Any] = {}
    for k in MONEY_KEYS:
        if raw.get(k) is not None:
            try:
                v = Decimal(str(raw[k]))
            except InvalidOperation as exc:
                raise LimitsError(f"{k}: importo non valido") from exc
            if v < 0 or v > Decimal("1000000"):
                raise LimitsError(f"{k}: fuori intervallo")
            kw[k] = v
    for k, (lo, hi) in INT_KEYS.items():
        if raw.get(k) is not None:
            if isinstance(raw[k], bool) or not isinstance(raw[k], int) or not lo <= raw[k] <= hi:
                raise LimitsError(f"{k}: deve essere un intero tra {lo} e {hi}")
            kw[k] = raw[k]
    for fk, (flo, fhi) in FLOAT_KEYS.items():
        if raw.get(fk) is not None:
            if (
                not isinstance(raw[fk], (int, float))
                or isinstance(raw[fk], bool)
                or not flo <= raw[fk] <= fhi
            ):
                raise LimitsError(f"{fk}: deve essere tra {flo} e {fhi}")
            kw[fk] = float(raw[fk])
    for k in LIST_KEYS:
        if raw.get(k) is not None:
            if (
                not isinstance(raw[k], list)
                or not all(isinstance(x, str) and x for x in raw[k])
                or len(raw[k]) > 100
            ):
                raise LimitsError(f"{k}: elenco di nomi")
            kw[k] = tuple(x.strip().lower() for x in raw[k])
    if isinstance(raw.get("exposure"), dict):
        ex = raw["exposure"]
        bad = set(ex) - set(ExposureLimits.__dataclass_fields__)
        if bad:
            raise LimitsError(f"limiti di esposizione sconosciuti: {', '.join(sorted(bad))}")
        floats = {k: float(v) for k, v in ex.items() if k != "min_items"}
        kw["exposure"] = ExposureLimits(
            **floats, min_items=int(ex.get("min_items", ExposureLimits.min_items))
        )  # type: ignore[arg-type]
    lim = Limits(**kw)
    if (
        lim.weekly_budget is not None
        and lim.daily_budget is not None
        and lim.daily_budget > lim.weekly_budget
    ):
        raise LimitsError("il budget giornaliero supera quello settimanale")
    return lim
