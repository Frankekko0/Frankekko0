"""Pre-mortem: before a purchase, the three most likely ways it loses money.

For every candidate failure the expected loss is ``probability * loss``; the three largest are kept and
each is checked against what is actually known (verified, contradicted, or "cannot be verified from
what we have"). Required above a cost threshold; nothing here invents evidence: a failure mode whose
evidence is missing says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PremortemFacts:
    cost: float
    resale_low: float
    resale_mid: float
    p_authentic: float  # 0..1
    p_sale_30d: float  # 0..1 at the planned price
    seller_risk: int  # 0..100
    comparables: int
    sold_comparables: int
    photos_analysed: bool
    label_seen: bool
    unobserved_parts: tuple[str, ...] = ()
    declared_defects: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()  # consistency-matrix discrepancies, already worded
    positive_auth_signals: tuple[str, ...] = ()
    shipping_out: float = 0.0
    return_cost: float = 0.0


@dataclass
class FailureMode:
    code: str
    title: str
    probability: float
    loss: float
    evidence: list[str] = field(default_factory=list)
    check: str = "unverifiable"  # verified_ok | contradicted | unverifiable
    how: str = ""

    @property
    def expected_loss(self) -> float:
        return self.probability * self.loss

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "probability": round(self.probability, 3),
            "loss": round(self.loss, 2),
            "expected_loss": round(self.expected_loss, 2),
            "evidence": self.evidence,
            "check": self.check,
            "how": self.how,
        }


def required(cost: float, threshold: float = 25.0) -> bool:
    return cost >= threshold


def premortem(f: PremortemFacts, top: int = 3) -> list[FailureMode]:
    modes: list[FailureMode] = []

    fake = FailureMode("not_authentic", "L'articolo non è autentico", 1 - f.p_authentic, f.cost)
    if f.positive_auth_signals and f.label_seen:
        fake.evidence = list(f.positive_auth_signals)[:3]
        fake.check = "verified_ok"
        fake.how = "etichetta vista e segnali favorevoli (non una prova di autenticità)"
    elif not f.label_seen:
        fake.evidence = ["etichetta non vista nelle foto"]
        fake.how = "serve la foto dell'etichetta interna"
    modes.append(fake)

    scam = FailureMode(
        "seller_does_not_deliver",
        "Il venditore non consegna o la merce non corrisponde",
        f.seller_risk / 100 * 0.6,
        f.cost,
    )
    scam.evidence = [f"rischio venditore {f.seller_risk}/100"]
    scam.check = "verified_ok" if f.seller_risk < 25 else "unverifiable"
    scam.how = "storico e recensioni del venditore; pagare solo dentro la piattaforma"
    modes.append(scam)

    hidden = FailureMode(
        "hidden_defect",
        "Un difetto non visibile nelle foto",
        min(0.6, 0.08 + 0.07 * len(f.unobserved_parts) + (0.0 if f.photos_analysed else 0.12)),
        0.3 * f.resale_mid,
    )
    hidden.evidence = [f"parti non osservate: {', '.join(f.unobserved_parts)}"] if f.unobserved_parts else []
    if f.declared_defects:
        hidden.evidence.append("difetti già dichiarati: " + ", ".join(f.declared_defects))
    hidden.check = "verified_ok" if f.photos_analysed and not f.unobserved_parts else "unverifiable"
    hidden.how = "chiedere le foto delle parti non viste prima di comprare"
    modes.append(hidden)

    slow = FailureMode(
        "slow_or_low_sale",
        "Si vende tardi o sotto la stima",
        max(0.05, 1 - f.p_sale_30d),
        max(0.0, f.resale_mid - f.resale_low),
    )
    slow.evidence = [f"probabilità di vendita entro 30 giorni {f.p_sale_30d:.0%}"]
    slow.check = "verified_ok" if f.p_sale_30d >= 0.6 else "unverifiable"
    slow.how = "tempi di vendita dei comparabili"
    modes.append(slow)

    weak = FailureMode(
        "market_value_overestimated",
        "Il valore di mercato è sovrastimato",
        0.45 if f.sold_comparables < 3 else 0.2 if f.sold_comparables < 8 else 0.08,
        max(0.0, f.resale_mid - f.resale_low),
    )
    weak.evidence = [f"{f.sold_comparables} venduti su {f.comparables} comparabili"]
    weak.check = "verified_ok" if f.sold_comparables >= 8 else "unverifiable"
    weak.how = "più vendite reali dello stesso modello"
    modes.append(weak)

    if f.contradictions:
        c = FailureMode(
            "listing_inconsistent",
            "L'annuncio è incoerente (taglia, materiale, etichetta)",
            0.5,
            0.25 * f.resale_mid,
        )
        c.evidence = list(f.contradictions)[:3]
        c.check = "contradicted"
        c.how = "risolvere le contraddizioni con il venditore"
        modes.append(c)

    if f.return_cost > 0:
        ret = FailureMode("return_or_dispute", "Reso o disputa", 0.07, f.return_cost + f.shipping_out)
        ret.how = "descrizione accurata e foto del pacco come prova"
        modes.append(ret)

    modes.sort(key=lambda m: (-m.expected_loss, m.code))
    return modes[:top]
