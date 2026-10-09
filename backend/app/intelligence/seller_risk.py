"""Seller risk (scam and non-delivery), kept apart from the authenticity of the item.

Starts from how little the profile proves (the reliability score is the opposite of this) and adds the
signals that make a scam likelier: a brand-new profile, photos reused from other sellers, a request to
pay or talk outside the platform (the strongest one), a price too good to be true and anomalies in the
seller's other listings. At or above ``HIGH`` the decision engine refuses to buy, whatever the margin.
"""

from __future__ import annotations

from dataclasses import dataclass, field

HIGH = 70


@dataclass(frozen=True)
class SellerRisk:
    score: int
    level: str  # low | medium | high
    factors: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"score": self.score, "level": self.level, "factors": self.factors}


def seller_risk_score(
    *,
    reliability: int,
    review_count: int,
    reused_photos: bool = False,
    off_platform: bool = False,
    price_too_good: bool = False,
    anomalies: int = 0,
) -> SellerRisk:
    factors: list[dict[str, object]] = []
    score = 100 - max(0, min(100, reliability))
    factors.append({"label": f"Affidabilità del profilo {reliability}/100", "impact": score})
    if review_count < 3:
        score += 10
        factors.append({"label": "Profilo nuovo o quasi senza recensioni", "impact": 10})
    if reused_photos:
        score += 25
        factors.append({"label": "Foto già usate da altri venditori", "impact": 25})
    if off_platform:
        score += 35
        factors.append({"label": "Chiede pagamento o contatti fuori dalla piattaforma", "impact": 35})
    if price_too_good:
        score += 10
        factors.append({"label": "Prezzo troppo basso rispetto al mercato", "impact": 10})
    if anomalies:
        add = min(15, 5 * anomalies)
        score += add
        factors.append({"label": f"{anomalies} anomalie negli altri annunci del venditore", "impact": add})
    score = max(0, min(100, score))
    level = "high" if score >= HIGH else "medium" if score >= 45 else "low"
    return SellerRisk(score, level, factors)
