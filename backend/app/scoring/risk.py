"""Risk Score (0-100, higher = riskier), always with explicit reasons."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.enums import Condition, RiskLevel
from app.scoring.seller import SellerProfile


@dataclass(frozen=True)
class RiskInput:
    price: Decimal
    fair_market_value: Decimal | None
    brand_counterfeit_risk: float
    seller: SellerProfile | None
    seller_account_age_days: int | None
    photo_count: int
    description_length: int
    suspicious_terms: tuple[str, ...] = ()
    defect_terms: tuple[str, ...] = ()
    vision_defects: tuple[str, ...] = ()
    vision_concerns: tuple[str, ...] = ()
    identification_confidence: int = 0
    comparables_used: int = 0
    market_confidence: int = 0
    condition: str = Condition.UNKNOWN
    photos_reused_by_other_seller: bool = False
    is_repost: bool = False
    brand_known: bool = True


@dataclass(frozen=True)
class RiskFactor:
    code: str
    label: str
    points: int
    severity: str  # info | low | medium | high


@dataclass
class RiskResult:
    score: int
    level: RiskLevel
    factors: list[RiskFactor] = field(default_factory=list)

    def as_list(self) -> list[dict[str, object]]:
        return [f.__dict__ for f in self.factors]


def _severity(points: int) -> str:
    if points >= 20:
        return "high"
    if points >= 10:
        return "medium"
    if points >= 4:
        return "low"
    return "info"


def risk_level(score: int) -> RiskLevel:
    if score < 25:
        return RiskLevel.LOW
    if score < 50:
        return RiskLevel.MODERATE
    if score < 75:
        return RiskLevel.HIGH
    return RiskLevel.VERY_HIGH


def assess_risk(inp: RiskInput) -> RiskResult:
    factors: list[RiskFactor] = []

    def add(code: str, label: str, points: int) -> None:
        if points > 0:
            factors.append(RiskFactor(code, label, points, _severity(points)))

    high_fake_brand = inp.brand_counterfeit_risk >= 0.3
    if inp.fair_market_value and inp.fair_market_value > 0:
        ratio = float(inp.price / inp.fair_market_value)
        discount = round((1 - ratio) * 100)
        if ratio < 0.35:
            add(
                "price_too_low",
                f"Prezzo sospettosamente basso ({discount}% sotto il mercato)",
                35 if high_fake_brand else 25,
            )
        elif ratio < 0.5 and high_fake_brand:
            add(
                "price_low_fake_brand",
                f"Prezzo molto basso ({discount}% sotto il mercato) per un brand spesso contraffatto",
                15,
            )

    if inp.brand_counterfeit_risk >= 0.1:
        add(
            "counterfeit_brand",
            "Brand con molte contraffazioni sul mercato dell'usato",
            round(inp.brand_counterfeit_risk * 20),
        )
    if not inp.brand_known:
        add("brand_unknown", "Brand non identificato con certezza", 8)

    s = inp.seller
    if s is None:
        add("seller_unknown", "Informazioni sul venditore non disponibili", 5)
    else:
        if s.review_count == 0:
            add(
                "seller_no_reviews",
                "Venditore senza recensioni (nessuno storico, non necessariamente un problema)",
                10,
            )
        elif s.review_count < 5:
            add("seller_few_reviews", f"Venditore con poche recensioni ({s.review_count})", 5)
        if s.rating is not None and s.review_count >= 5:
            rating = float(s.rating)
            if rating < 4.0:
                add("seller_bad_rating", f"Valutazione venditore bassa ({rating:.1f}★)", 15)
            elif rating < 4.5:
                add("seller_mixed_rating", f"Valutazione venditore nella media bassa ({rating:.1f}★)", 6)
        if inp.seller_account_age_days is not None and inp.seller_account_age_days < 30:
            add("seller_new_account", f"Account creato da {inp.seller_account_age_days} giorni", 4)
        for anomaly in s.anomalies:
            add("seller_anomaly", anomaly, 10)

    if inp.photo_count == 0:
        add("no_photos", "Nessuna foto", 15)
    elif inp.photo_count == 1:
        add("one_photo", "Una sola foto: impossibile verificare dettagli ed etichette", 12)
    elif inp.photo_count == 2:
        add("few_photos", "Solo 2 foto", 6)

    if inp.suspicious_terms:
        add("suspicious_text", "Descrizione sospetta: " + ", ".join(inp.suspicious_terms), 30)
    if inp.description_length < 20:
        add("short_description", "Descrizione molto breve", 4)
    if inp.defect_terms:
        add(
            "declared_defects",
            "Difetti dichiarati: " + ", ".join(inp.defect_terms),
            min(12, 6 + 3 * len(inp.defect_terms)),
        )
    if inp.vision_defects:
        add("visual_defects", "Possibili difetti visibili nelle foto: " + ", ".join(inp.vision_defects), 10)
    if inp.vision_concerns:
        add("visual_authenticity", "Elementi da verificare nelle foto: " + ", ".join(inp.vision_concerns), 15)
    if inp.photos_reused_by_other_seller:
        add("reused_photos", "Foto identiche presenti in un annuncio di un altro venditore", 20)
    if inp.is_repost:
        add("repost", "Articolo ripubblicato più volte", 3)

    if inp.identification_confidence < 50:
        add("hard_to_identify", "Prodotto difficile da identificare", 10)
    elif inp.identification_confidence < 65:
        add("partly_identified", "Identificazione del prodotto parziale", 5)
    if inp.comparables_used < 5:
        add("few_comparables", "Pochi comparabili per stimare il valore", 10)
    elif inp.market_confidence < 40:
        add("weak_market_data", "Dati di mercato poco affidabili", 6)
    if inp.condition == Condition.SATISFACTORY:
        add("poor_condition", "Condizioni discrete", 6)

    score = min(100, sum(f.points for f in factors))
    factors.sort(key=lambda f: f.points, reverse=True)
    return RiskResult(score, risk_level(score), factors)
