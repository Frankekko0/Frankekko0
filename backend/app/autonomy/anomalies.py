"""Automatic suspension: when the numbers say the system is not behaving, it stops and asks."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.autonomy.limits import Limits

MIN_ACTIONS_FOR_RATE = 5
MIN_SALES_FOR_DIVERGENCE = 3


@dataclass(frozen=True)
class RecentStats:
    actions: int  # decided or executed in the window
    failed: int  # channel failures
    realized_loss: Decimal  # sum of the losses of sales in the window (positive number)
    forecast_error: float | None  # mean |price error| as a share, over the sales in the window
    sales: int


@dataclass(frozen=True)
class Anomaly:
    code: str
    label: str


def detect(stats: RecentStats, limits: Limits) -> list[Anomaly]:
    out: list[Anomaly] = []
    if stats.actions >= MIN_ACTIONS_FOR_RATE and stats.failed / stats.actions > limits.max_error_rate:
        out.append(
            Anomaly(
                "error_rate",
                f"{stats.failed} azioni su {stats.actions} non riuscite (limite {limits.max_error_rate:.0%})",
            )
        )
    if stats.realized_loss > limits.max_loss:
        out.append(
            Anomaly(
                "loss",
                f"Perdite realizzate di {stats.realized_loss} € nel periodo (limite {limits.max_loss} €)",
            )
        )
    if (
        stats.sales >= MIN_SALES_FOR_DIVERGENCE
        and stats.forecast_error is not None
        and stats.forecast_error > limits.max_forecast_error
    ):
        out.append(
            Anomaly(
                "divergence",
                f"Le previsioni di prezzo sbagliano in media del {stats.forecast_error:.0%} (limite {limits.max_forecast_error:.0%})",
            )
        )
    return out
