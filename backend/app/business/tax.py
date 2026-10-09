"""Fiscal status thresholds: the user enters them from an official source, with the date; the system watches them.

No rate and no threshold is built in. A threshold without a source and a date is refused. The system measures
the year's revenue, profit or number of sales against each threshold and warns when one is near or crossed;
what that means (private seller, habitual, professional, VAT, platform reporting duties) is for the accountant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

METRICS = ("revenue", "profit", "sales_count")
WARN_AT = 0.80
PROFESSIONAL_CHECKLIST = [
    "Status del titolare (privato, occasionale, abituale, attività) confermato con il commercialista",
    "Partita IVA e regime fiscale, se dovuti",
    "Fatturazione e conservazione dei documenti",
    "Informative al consumatore (identità del venditore, condizioni di vendita)",
    "Diritto di recesso per le vendite a distanza ai consumatori",
    "Garanzia legale di conformità sull'usato (durata e modalità)",
    "Comunicazioni dei dati delle vendite che la piattaforma è tenuta a fare",
]


class ThresholdError(ValueError):
    pass


@dataclass(frozen=True)
class Threshold:
    name: str
    amount: float
    metric: str
    source: str
    as_of: str


def parse(raw: list[dict[str, Any]]) -> list[Threshold]:
    out = []
    for i, t in enumerate(raw or []):
        try:
            amount = float(t["amount"])
            name, source, as_of = (
                str(t["name"]).strip(),
                str(t.get("source") or "").strip(),
                str(t.get("as_of") or "").strip(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ThresholdError(f"soglia {i + 1}: nome e importo obbligatori") from exc
        metric = t.get("metric", "revenue")
        if amount <= 0 or not name or metric not in METRICS:
            raise ThresholdError(
                f"soglia {i + 1}: nome, importo positivo e metrica ({', '.join(METRICS)}) validi"
            )
        if not source or not as_of:
            raise ThresholdError(
                f"soglia «{name}»: serve la fonte ufficiale e la data a cui si riferisce (il sistema non inventa soglie)"
            )
        try:
            date.fromisoformat(as_of)
        except ValueError as exc:
            raise ThresholdError(f"soglia «{name}»: la data deve essere AAAA-MM-GG") from exc
        out.append(Threshold(name, amount, metric, source, as_of))
    return out


@dataclass(frozen=True)
class TaxNotice:
    name: str
    metric: str
    used: float
    amount: float
    share: float
    level: str  # ok | approaching | exceeded
    projected: float | None
    source: str
    as_of: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "share": round(self.share, 3),
            "projected": None if self.projected is None else round(self.projected, 2),
        }


def check(
    thresholds: list[Threshold], revenue: float, profit: float, sales: int, today: date
) -> list[TaxNotice]:
    elapsed = max(1, (today - date(today.year, 1, 1)).days + 1)
    out = []
    for t in thresholds:
        used = {"revenue": revenue, "profit": profit, "sales_count": float(sales)}[t.metric]
        share = used / t.amount
        projected = used * 365.0 / elapsed if elapsed >= 30 else None
        if used >= t.amount:
            level, msg = "exceeded", f"Superata la soglia «{t.name}» ({used:.0f} su {t.amount:.0f})."
        elif share >= WARN_AT:
            level, msg = "approaching", f"Hai raggiunto il {share:.0%} della soglia «{t.name}»."
        elif projected is not None and projected >= t.amount:
            level, msg = (
                "approaching",
                f"A questo ritmo la soglia «{t.name}» verrebbe superata entro l'anno (proiezione {projected:.0f}).",
            )
        else:
            level, msg = "ok", f"Soglia «{t.name}»: {share:.0%} usato."
        if level != "ok":
            msg += (
                f" Fonte: {t.source}, aggiornata al {t.as_of}. Verifica con il commercialista cosa comporta."
            )
        out.append(
            TaxNotice(t.name, t.metric, used, t.amount, share, level, projected, t.source, t.as_of, msg)
        )
    return out
