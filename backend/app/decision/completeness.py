"""Data completeness (0-100): how much of what a purchase decision needs is actually known.

Separate from confidence on purpose. Confidence says how sure the system is of its *market
analysis*; completeness says how much of the *listing* it could read: brand, model, size,
condition, photos, label, costs, seller. A listing can have a rich market and a poor description
(complete market, incomplete listing) or the opposite. Both stay visible and neither hides the
other.

Every missing piece is returned as ``missing_info`` with what it blocks, so the user (and the
agent) know what to ask the seller for. Nothing is invented: an unknown stays unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# (code, weight): the weights add up to 100.
WEIGHTS: dict[str, int] = {
    "brand": 12,
    "model": 10,
    "size": 8,
    "condition": 8,
    "photos": 15,
    "label_photo": 10,
    "description": 8,
    "shipping": 10,
    "seller": 7,
    "photos_analysed": 7,
    "identification": 5,
}
assert sum(WEIGHTS.values()) == 100

MIN_PHOTOS = 4
MIN_DESCRIPTION = 120


@dataclass(frozen=True)
class CompletenessInput:
    brand_known: bool
    model_known: bool
    size_known: bool
    condition_known: bool
    photo_count: int
    description_length: int
    shipping_known: bool
    seller_known: bool
    # None: not verifiable (no photo of the label could be checked, or the check does not apply).
    label_photo_seen: bool | None = None
    # True only when a photo analysis actually ran (the rule-based fallback does not count).
    photos_analysed: bool = False
    identification_confidence: int = 0


@dataclass(frozen=True)
class MissingInfo:
    code: str
    label: str
    # What its absence stops: "strong_buy" | "buy" | "confidence"
    blocks: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "label": self.label, "blocks": self.blocks}


@dataclass(frozen=True)
class Completeness:
    score: int
    components: dict[str, float]  # 0-1 per piece
    missing: list[MissingInfo]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "components": {k: round(v, 2) for k, v in self.components.items()},
            "missing": [m.as_dict() for m in self.missing],
        }


def compute_completeness(inp: CompletenessInput) -> Completeness:
    parts: dict[str, float] = {
        "brand": 1.0 if inp.brand_known else 0.0,
        "model": 1.0 if inp.model_known else 0.0,
        "size": 1.0 if inp.size_known else 0.0,
        "condition": 1.0 if inp.condition_known else 0.0,
        "photos": min(1.0, inp.photo_count / MIN_PHOTOS),
        # A label that cannot be checked earns half: unknown is not the same as absent.
        "label_photo": 0.5 if inp.label_photo_seen is None else (1.0 if inp.label_photo_seen else 0.0),
        "description": min(1.0, inp.description_length / MIN_DESCRIPTION),
        "shipping": 1.0 if inp.shipping_known else 0.0,
        "seller": 1.0 if inp.seller_known else 0.0,
        "photos_analysed": 1.0 if inp.photos_analysed else 0.0,
        "identification": max(0.0, min(1.0, inp.identification_confidence / 100)),
    }
    score = round(sum(WEIGHTS[k] * v for k, v in parts.items()))

    missing: list[MissingInfo] = []

    def need(code: str, label: str, blocks: str, missing_when: bool) -> None:
        if missing_when:
            missing.append(MissingInfo(code, label, blocks))

    need("brand", "Marca non identificata", "buy", not inp.brand_known)
    need("model", "Modello non identificato", "strong_buy", not inp.model_known)
    need("size", "Taglia non indicata", "confidence", not inp.size_known)
    need("condition", "Condizione non dichiarata", "strong_buy", not inp.condition_known)
    need(
        "photos",
        f"Poche foto ({inp.photo_count}): ne servono almeno {MIN_PHOTOS}",
        "strong_buy" if inp.photo_count < 3 else "confidence",
        inp.photo_count < MIN_PHOTOS,
    )
    need(
        "label_photo",
        "Etichetta non fotografata" if inp.label_photo_seen is False else "Etichetta non verificabile",
        "strong_buy",
        inp.label_photo_seen is not True,
    )
    need("description", "Descrizione assente o troppo breve", "confidence", inp.description_length < 40)
    need(
        "shipping",
        "Spedizione non letta dall'annuncio: costo totale stimato",
        "strong_buy",
        not inp.shipping_known,
    )
    need("seller", "Venditore non noto", "confidence", not inp.seller_known)
    need("photos_analysed", "Foto non analizzate dal modello visivo", "confidence", not inp.photos_analysed)
    return Completeness(max(0, min(100, score)), parts, missing)
