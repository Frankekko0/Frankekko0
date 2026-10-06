"""Risk signals shown as a checklist: possible fake, generic description, label photos, seller
reviews, title/photo consistency.

Each signal has a level (``ok``, ``info``, ``low``, ``medium``, ``high``), a plain-language label,
the evidence behind it and whether it could be verified with the available data. Nothing here
ever declares an item authentic: at best there are "no warning signs found". A new seller is
reported as such, never as a scammer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.identification.taxonomy import fold

# Phrases that carry no information on their own (Italian, plus common EN/FR/ES equivalents).
GENERIC_PHRASES = (
    "come da foto",
    "come in foto",
    "vedi foto",
    "vedere foto",
    "guarda le foto",
    "per info",
    "per qualsiasi info",
    "scrivetemi",
    "scrivimi",
    "contattatemi",
    "chiedete pure",
    "info in privato",
    "spedisco subito",
    "spedizione veloce",
    "see photos",
    "as pictured",
    "message me",
    "voir photos",
    "ver fotos",
)
# Words that make a description specific: measurements, materials, defects, fit, provenance.
SPECIFIC = re.compile(
    r"\d|\b(cm|misur|lunghezz|spall|petto|vita|manic|cotone|lana|cashmere|seta|lino|pelle|poliester|"
    r"nylon|denim|difett|macchi|buc|usur|strapp|scolor|pilling|indossat|lavat|nuov|cartellin|"
    r"etichett|scontrino|ricevuta|acquistat|originale|vestibilit|slim|regular|oversize|taglia)",
    re.IGNORECASE,
)
_WORD = re.compile(r"\w+", re.UNICODE)

LEVEL_ORDER = {"ok": 0, "info": 1, "low": 2, "medium": 3, "high": 4}


@dataclass(frozen=True)
class SignalInput:
    title: str
    description: str
    brand_name: str | None
    brand_slug: str | None
    brand_counterfeit_risk: float
    category: str | None
    color: str | None
    photo_count: int
    price: Decimal
    fair_market_value: Decimal | None
    suspicious_terms: tuple[str, ...] = ()
    photos_reused_by_other_seller: bool = False
    seller_known: bool = False
    seller_rating: float | None = None
    seller_review_count: int = 0
    seller_anomalies: tuple[str, ...] = ()
    vision: dict[str, Any] | None = None


@dataclass
class Signal:
    code: str
    title: str
    level: str
    label: str
    evidence: list[str] = field(default_factory=list)
    verifiable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# ------------------------------------------------------------------ description
@dataclass(frozen=True)
class DescriptionQuality:
    score: int  # 0-100, higher = more informative
    generic: bool
    reasons: tuple[str, ...]


def description_quality(description: str) -> DescriptionQuality:
    text = (description or "").strip()
    folded = fold(text)
    words = _WORD.findall(folded)
    unique = {w for w in words if len(w) > 2}
    reasons: list[str] = []
    score = 100
    if len(text) < 20:
        score -= 60
        reasons.append("molto breve" if text else "assente")
    elif len(unique) < 8:
        score -= 35
        reasons.append("poche parole diverse")
    specifics = len(SPECIFIC.findall(text))
    if specifics == 0:
        score -= 30
        reasons.append("nessun dettaglio concreto (misure, materiale, difetti, stato)")
    elif specifics == 1:
        score -= 10
    generic_hits = [p for p in GENERIC_PHRASES if p in folded]
    if generic_hits:
        # A stock phrase is fine inside a detailed text; it is a warning when it is most of it.
        weight = sum(len(p) for p in generic_hits) / max(len(folded), 1)
        if weight > 0.25:
            score -= 25
            reasons.append("frasi generiche: " + ", ".join(f"“{p}”" for p in generic_hits[:3]))
    score = max(0, min(100, score))
    return DescriptionQuality(score, score < 45, tuple(reasons))


# ------------------------------------------------------------------ signals
def possible_fake(inp: SignalInput) -> Signal:
    evidence: list[str] = []
    points = 0
    if inp.brand_counterfeit_risk >= 0.3:
        evidence.append(f"{inp.brand_name or 'Il brand'} è spesso contraffatto sull'usato")
        points += 2
    elif inp.brand_counterfeit_risk >= 0.15:
        evidence.append("Brand con qualche contraffazione in circolazione")
        points += 1
    if inp.fair_market_value and inp.fair_market_value > 0:
        ratio = float(inp.price / inp.fair_market_value)
        if ratio < 0.35:
            evidence.append(f"Prezzo {round((1 - ratio) * 100)}% sotto il mercato")
            points += 3 if inp.brand_counterfeit_risk >= 0.15 else 2
        elif ratio < 0.5 and inp.brand_counterfeit_risk >= 0.3:
            evidence.append(f"Prezzo {round((1 - ratio) * 100)}% sotto il mercato per un brand a rischio")
            points += 1
    if inp.suspicious_terms:
        evidence.append("Termini sospetti nel testo: " + ", ".join(inp.suspicious_terms))
        points += 4
    if inp.photos_reused_by_other_seller:
        evidence.append("Le stesse foto compaiono nell'annuncio di un altro venditore")
        points += 3
    concerns = (inp.vision or {}).get("authenticity_concerns") or []
    if concerns:
        evidence.append("Dalle foto: " + ", ".join(concerns))
        points += 3
    level = "high" if points >= 5 else "medium" if points >= 3 else "low" if points >= 1 else "ok"
    label = {
        "high": "Possibile falso: diversi segnali, verifica con cura prima di comprare",
        "medium": "Possibile falso: qualche segnale da verificare",
        "low": "Pochi segnali di contraffazione, verifica comunque etichette e dettagli",
        "ok": "Nessun segnale di contraffazione trovato (non è una garanzia di autenticità)",
    }[level]
    return Signal("possible_fake", "Autenticità", level, label, evidence)


def generic_description(inp: SignalInput) -> Signal:
    q = description_quality(inp.description)
    if not inp.description.strip():
        return Signal(
            "generic_description",
            "Descrizione",
            "info",
            "Descrizione non disponibile in questa lettura (es. card della ricerca)",
            verifiable=False,
        )
    if q.generic:
        return Signal(
            "generic_description",
            "Descrizione",
            "medium" if q.score < 25 else "low",
            "Descrizione generica: chiedi dettagli, misure e difetti al venditore",
            list(q.reasons),
        )
    return Signal(
        "generic_description", "Descrizione", "ok", "Descrizione con dettagli concreti", list(q.reasons)
    )


def label_photos(inp: SignalInput) -> Signal:
    quality = (inp.vision or {}).get("photo_quality") or {}
    has_label = quality.get("has_label_photo")
    risky = inp.brand_counterfeit_risk >= 0.15
    if has_label is True:
        return Signal("label_photos", "Foto etichetta", "ok", "Etichetta visibile nelle foto")
    if has_label is False:
        return Signal(
            "label_photos",
            "Foto etichetta",
            "medium" if risky else "low",
            "Nessuna foto dell'etichetta: chiedila prima di comprare",
            [f"{inp.photo_count} foto analizzate"],
        )
    if inp.photo_count <= 2:
        return Signal(
            "label_photos",
            "Foto etichetta",
            "medium" if risky else "low",
            "Solo poche foto: probabilmente mancano etichetta e dettagli",
            [f"{inp.photo_count} foto"],
        )
    return Signal(
        "label_photos",
        "Foto etichetta",
        "info",
        "Non verificato: serve l'analisi delle immagini (chiave AI) per riconoscere l'etichetta",
        [f"{inp.photo_count} foto"],
        verifiable=False,
    )


def seller_reviews(inp: SignalInput) -> Signal:
    if not inp.seller_known:
        return Signal(
            "seller_reviews",
            "Venditore",
            "info",
            "Valutazioni del venditore non disponibili",
            verifiable=False,
        )
    n = inp.seller_review_count
    rating = f"{inp.seller_rating:.1f}★ su {n} recensioni" if inp.seller_rating is not None and n else None
    evidence = [rating] if rating else []
    evidence += list(inp.seller_anomalies)
    if inp.seller_anomalies:
        return Signal(
            "seller_reviews", "Venditore", "medium", "Comportamento del venditore da verificare", evidence
        )
    if n == 0:
        return Signal(
            "seller_reviews",
            "Venditore",
            "low",
            "Venditore senza recensioni: nessuno storico (non è di per sé un problema)",
            evidence,
        )
    if n < 5:
        return Signal("seller_reviews", "Venditore", "low", f"Venditore con poche recensioni ({n})", evidence)
    if inp.seller_rating is not None and inp.seller_rating < 4.0:
        return Signal("seller_reviews", "Venditore", "medium", "Valutazione del venditore bassa", evidence)
    return Signal("seller_reviews", "Venditore", "ok", "Venditore con un buono storico", evidence)


def _vision_value(vision: dict[str, Any], key: str) -> str | None:
    finding = vision.get(key)
    if not isinstance(finding, dict) or finding.get("certainty") == "unverifiable":
        return None
    value = finding.get("value")
    return fold(str(value)) if value else None


def title_photo_consistency(inp: SignalInput) -> Signal:
    vision = inp.vision or {}
    if not vision or vision.get("analyzer") in (None, "heuristic"):
        return Signal(
            "title_photo_mismatch",
            "Titolo e foto",
            "info",
            "Non verificato: serve l'analisi delle immagini (chiave AI) per confrontare foto e titolo",
            verifiable=False,
        )
    mismatches: list[str] = []
    seen_brand = _vision_value(vision, "brand") or _vision_value(vision, "logo")
    if seen_brand and inp.brand_name:
        brand = fold(inp.brand_name)
        if seen_brand not in brand and brand not in seen_brand and (inp.brand_slug or "") not in seen_brand:
            mismatches.append(f"Nelle foto si legge “{seen_brand}”, l'annuncio dice “{inp.brand_name}”")
    seen_category = _vision_value(vision, "category")
    if seen_category and inp.category and fold(inp.category).split("-")[0] not in seen_category:
        title = fold(inp.title)
        if seen_category not in title:
            mismatches.append(f"Le foto sembrano mostrare “{seen_category}”")
    seen_color = _vision_value(vision, "color")
    if seen_color and inp.color and fold(inp.color) not in seen_color and seen_color not in fold(inp.color):
        mismatches.append(f"Colore nelle foto “{seen_color}”, nell'annuncio “{inp.color}”")
    if mismatches:
        level = "high" if any("si legge" in m for m in mismatches) else "low"
        return Signal(
            "title_photo_mismatch", "Titolo e foto", level, "Incongruenze tra titolo e foto", mismatches
        )
    return Signal("title_photo_mismatch", "Titolo e foto", "ok", "Foto coerenti con il titolo")


def build_signals(inp: SignalInput) -> list[Signal]:
    return [
        possible_fake(inp),
        generic_description(inp),
        label_photos(inp),
        seller_reviews(inp),
        title_photo_consistency(inp),
    ]


def worst_level(signals: list[Signal]) -> str:
    return max((s.level for s in signals), key=LEVEL_ORDER.__getitem__, default="ok")
