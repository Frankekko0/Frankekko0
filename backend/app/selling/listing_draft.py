"""A resale listing that is optimised but never untrue.

The text is built only from facts the user or the photos support: every attribute carries where it comes
from, and nothing unverified is claimed (in particular never "original" or "authentic": at most "label
visible in the photos"). What the draft cannot know it asks the user to confirm before publishing. Photos
are ordered best first and a checklist lists the shots that are still missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FORBIDDEN_CLAIMS = ("originale", "autentico", "100% originale", "garantito", "mai usato", "come nuovo")

CONDITION_TEXT = {
    "new_with_tags": "nuovo con cartellino",
    "new_without_tags": "nuovo senza cartellino",
    "very_good": "ottime condizioni",
    "good": "buone condizioni",
    "satisfactory": "condizioni discrete",
    "unknown": None,
}


@dataclass(frozen=True)
class Defect:
    kind: str  # "macchia", "foro", "pilling"...
    zone: str | None = None
    photo: int | None = None  # 1-based photo showing it
    severity: str = "minor"


@dataclass(frozen=True)
class ItemFacts:
    brand: str | None = None
    model: str | None = None
    category: str | None = None  # "felpa con cappuccio"
    size: str | None = None
    color: str | None = None
    material: str | None = None
    condition: str = "unknown"
    defects: tuple[Defect, ...] = ()
    defects_checked: bool = False  # the photos were inspected for defects (by a person or the analysis)
    measures_cm: dict[str, float] | None = None  # {"spalle": 52, "lunghezza": 70}
    label_visible_in_photos: bool = False
    photo_roles: tuple[str, ...] = ()  # roles already available among the photos


@dataclass
class ListingDraft:
    title: str
    description: str
    photo_checklist: list[str] = field(default_factory=list)
    to_confirm: list[str] = field(default_factory=list)  # things the user must check before publishing
    not_claimed: list[str] = field(default_factory=list)  # what the text deliberately does not say

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "description": self.description,
            "photo_checklist": self.photo_checklist,
            "to_confirm": self.to_confirm,
            "not_claimed": self.not_claimed,
        }


SHOTS = [
    ("front", "Davanti, intero, su fondo neutro e con buona luce (è la foto di copertina)"),
    ("back", "Dietro, intero"),
    ("label", "Etichetta interna con marca e taglia, leggibile"),
    ("care_label", "Etichetta di composizione e lavaggio"),
    ("detail", "Dettaglio del logo o della lavorazione"),
    ("flat_lay", "Capo steso con un metro accanto per le misure"),
]


def _title(f: ItemFacts, limit: int = 70) -> str:
    parts = [f.brand, f.category, f.model, f.color, f"tg. {f.size}" if f.size else None]
    title = " ".join(p for p in parts if p)
    return (title[: limit - 1].rstrip() + "…") if len(title) > limit else title


def build_draft(f: ItemFacts) -> ListingDraft:
    lines: list[str] = []
    head = " ".join(p for p in (f.brand, f.category, f.model) if p)
    attrs = [
        x
        for x in (
            f"taglia {f.size}" if f.size else None,
            f"colore {f.color}" if f.color else None,
            f"materiale: {f.material}" if f.material else None,
        )
        if x
    ]
    if head or attrs:
        lines.append((head or "Articolo") + (", " + ", ".join(attrs) if attrs else "") + ".")
    cond = CONDITION_TEXT.get(f.condition)
    to_confirm: list[str] = []
    if cond:
        lines.append(f"Condizioni: {cond}.")
    else:
        to_confirm.append("Indicare le condizioni dell'articolo")
    if f.defects:
        for d in f.defects:
            where = f" ({d.zone})" if d.zone else ""
            shown = f", vedi foto {d.photo}" if d.photo else ""
            lines.append(f"Difetto: {d.kind}{where}{shown}.")
    elif f.defects_checked:
        lines.append("Nelle foto non si vedono difetti.")
    else:
        to_confirm.append(
            "Controllare il capo e dichiarare eventuali difetti (macchie, fori, pilling, odori)"
        )
    if f.measures_cm:
        lines.append("Misure: " + ", ".join(f"{k} {v:g} cm" for k, v in f.measures_cm.items()) + ".")
    else:
        to_confirm.append("Misurare il capo (spalle, lunghezza) e inserire le misure")
    if f.label_visible_in_photos:
        lines.append("Etichetta interna visibile nelle foto.")
    lines.append("Spedizione tracciata, in 1-2 giorni lavorativi.")
    not_claimed = [
        "Autenticità: non dichiarata (nessuna prova oltre alle foto dell'etichetta)",
        "Stato di uso (indossato quante volte) e odori: non noti",
    ]
    shots = [text for role, text in SHOTS if role not in f.photo_roles]
    for d in f.defects:
        if d.photo is None:
            shots.append(f"Primo piano del difetto: {d.kind}{' ' + d.zone if d.zone else ''}")
    return ListingDraft(
        title=_title(f),
        description=" ".join(lines),
        photo_checklist=shots,
        to_confirm=to_confirm,
        not_claimed=not_claimed,
    )


def claims_not_supported(text: str) -> list[str]:
    """Forbidden claims that appear in a text (used on drafts and on texts the user edits)."""
    low = text.lower()
    return [c for c in FORBIDDEN_CLAIMS if c in low]
