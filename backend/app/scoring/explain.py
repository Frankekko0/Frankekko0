"""Human-readable explanation of a score ("Why 91/100?").

Every opportunity carries a list of factors such as ``+ Prezzo 52% sotto il mercato`` or
``- Dati comparabili limitati`` with their point impact, so nothing is a black box.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.demand.analysis import DemandResult, VelocityResult
from app.domain.enums import DemandLevel
from app.scoring.flip import FlipResult
from app.scoring.seller import SellerScore

DEMAND_LABELS = {
    DemandLevel.VERY_HIGH: "Domanda molto alta",
    DemandLevel.HIGH: "Domanda alta",
    DemandLevel.MEDIUM: "Domanda media",
    DemandLevel.LOW: "Domanda bassa",
    DemandLevel.VERY_LOW: "Domanda molto bassa",
}


@dataclass(frozen=True)
class ExplanationContext:
    flip: FlipResult
    discount_vs_market: float | None
    expected_roi: Decimal | None
    expected_profit: Decimal | None
    demand: DemandResult
    velocity: VelocityResult
    seller: SellerScore
    listing_age_hours: float | None
    comparables_used: int
    sold_comparables: int


def _fmt_eur(value: Decimal) -> str:
    return f"€{value:.0f}" if value == value.to_integral_value() else f"€{value:.2f}"


def _age_label(hours: float) -> str:
    if hours < 1:
        minutes = max(1, round(hours * 60))
        return f"Pubblicato da {minutes} {'minuto' if minutes == 1 else 'minuti'}"
    if hours < 48:
        n = round(hours)
        return f"Pubblicato da {n} {'ora' if n == 1 else 'ore'}"
    days = round(hours / 24)
    return f"Pubblicato da {days} {'giorno' if days == 1 else 'giorni'}"


def build_explanation(ctx: ExplanationContext) -> list[dict[str, Any]]:
    c = ctx.flip.components
    out: list[dict[str, Any]] = []

    def factor(kind: str, code: str, label: str, impact: float | None) -> None:
        out.append(
            {
                "type": kind,
                "code": code,
                "label": label,
                "impact": None if impact is None else round(impact, 1),
            }
        )

    d = ctx.discount_vs_market
    if d is not None:
        pct = round(abs(d) * 100)
        if d >= 0.1:
            factor(
                "positive",
                "undervaluation",
                f"Prezzo {pct}% sotto il mercato",
                c["undervaluation"]["contribution"],
            )
        elif d > -0.05:
            factor(
                "neutral",
                "undervaluation",
                "Prezzo in linea con il mercato",
                c["undervaluation"]["contribution"],
            )
        else:
            factor("negative", "overpriced", f"Prezzo {pct}% sopra il mercato", 0)

    if ctx.expected_roi is not None:
        roi_pct = round(float(ctx.expected_roi) * 100)
        kind = "positive" if roi_pct >= 30 else "neutral" if roi_pct > 0 else "negative"
        factor(kind, "roi", f"ROI atteso {roi_pct}%", c["roi"]["contribution"])
    if ctx.expected_profit is not None:
        kind = (
            "positive" if ctx.expected_profit >= 8 else "neutral" if ctx.expected_profit > 0 else "negative"
        )
        factor(
            kind,
            "profit",
            f"Profitto netto atteso {_fmt_eur(ctx.expected_profit)}",
            c["profit"]["contribution"],
        )

    level = ctx.demand.level
    str_pct = round(ctx.demand.sell_through_rate * 100)
    kind = (
        "positive"
        if level in (DemandLevel.HIGH, DemandLevel.VERY_HIGH)
        else "neutral"
        if level == DemandLevel.MEDIUM
        else "negative"
    )
    factor(kind, "demand", f"{DEMAND_LABELS[level]} (sell-through {str_pct}%)", c["demand"]["contribution"])

    days = ctx.velocity.estimated_days
    kind = "positive" if days <= 7 else "neutral" if days <= 14 else "negative"
    unit = "giorno" if round(days) == 1 else "giorni"
    factor(kind, "velocity", f"Vendita stimata in ~{days:.0f} {unit}", c["velocity"]["contribution"])

    s = ctx.seller
    if s.level == "high":
        factor("positive", "seller", "Venditore affidabile", c["seller"]["contribution"])
    elif s.level in ("low",):
        factor("negative", "seller", "Venditore poco affidabile", c["seller"]["contribution"])
    elif s.level == "new":
        factor("neutral", "seller", "Venditore nuovo (poco storico)", c["seller"]["contribution"])

    if ctx.listing_age_hours is not None:
        kind = "positive" if ctx.listing_age_hours <= 6 else "neutral"
        factor(kind, "freshness", _age_label(ctx.listing_age_hours), c["freshness"]["contribution"])

    if ctx.comparables_used >= 10:
        factor(
            "info",
            "comparables",
            f"Basato su {ctx.comparables_used} comparabili ({ctx.sold_comparables} venduti)",
            None,
        )

    for p in ctx.flip.penalties:
        factor("negative", p["code"], p["label"], -p["points"])
    if ctx.flip.cap:
        factor(
            "negative", "cap", f"Punteggio limitato a {ctx.flip.cap['max']}: {ctx.flip.cap['reason']}", None
        )

    order = {"positive": 0, "neutral": 1, "info": 2, "negative": 3}
    out.sort(key=lambda f: (order[f["type"]], -(abs(f["impact"]) if f["impact"] is not None else 0)))
    return out
