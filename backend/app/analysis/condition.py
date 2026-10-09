"""Condition from the photos: a class, a score, the defects with their place, and what is unknown.

Rules that cannot be bent:

* *No visible defect is not no defect.* The class is always qualified by what the photos could not
  show, and a thin set of photos lowers the confidence, never the standards.
* A flaw that may be a shadow, a fold or compression (``unverifiable``) is a *potential* defect: it
  is listed, never scored as fact.
* "New with tags" needs the tag in a photo. "New without tags" cannot be verified from photos and
  is reported as such.
* Without a real photo analysis the class is "undeterminable", not a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analysis.categories import CategoryPlugin
from app.analysis.coverage import is_analysed, photo_coverage
from app.domain.enums import CONDITION_LABELS_IT, CONDITION_ORDER, Condition

CLASSES = (
    "new_with_tags",
    "new_without_tags_unverifiable",
    "like_new",
    "very_good",
    "good",
    "fair",
    "poor",
    "undeterminable",
)
CLASS_LABEL = {
    "new_with_tags": "Nuovo con cartellino (verificato)",
    "new_without_tags_unverifiable": "Nuovo senza cartellino (non verificabile)",
    "like_new": "Come nuovo",
    "very_good": "Ottime condizioni",
    "good": "Buone condizioni",
    "fair": "Discrete condizioni",
    "poor": "Scarse condizioni",
    "undeterminable": "Non determinabile",
}
# Position on the scale the declared condition uses (0 = best); "poor" lies beyond "satisfactory".
CLASS_RANK = {
    "new_with_tags": 0,
    "new_without_tags_unverifiable": 1,
    "like_new": 1,
    "very_good": 2,
    "good": 3,
    "fair": 4,
    "poor": 5,
}
BASE_PENALTY = {
    "hole": 30, "tear": 35, "stain": 18, "halo": 12, "fading": 12, "pilling": 8, "snag": 6, "seam": 14,
    "deformation": 14, "collar_cuffs": 10, "print_damage": 14, "wear": 8, "sole_wear": 15, "heel_wear": 12,
    "creasing": 5, "scuff": 8, "scratch": 8, "corner_wear": 12, "zipper": 18, "lining_stain": 10,
    "handle_wear": 12, "other": 8,
}  # fmt: skip
SEVERITY_FACTOR = {"minor": 0.4, "moderate": 0.8, "severe": 1.3}
CERTAINTY_FACTOR = {"certain": 1.0, "probable": 0.7}
USED_BASE_SCORE = 94  # a used item is never scored as perfect
IMPACT_PER_POINT = 0.55  # percent of the price per penalty point
MAX_IMPACT_PCT = 60


@dataclass
class ConditionReport:
    condition_class: str
    score: int | None
    confidence: int
    visible_defects: list[dict[str, Any]] = field(default_factory=list)
    potential_defects: list[dict[str, Any]] = field(default_factory=list)
    unobserved_parts: list[str] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    price_impact_pct: int | None = None
    missing_photos: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return CLASS_LABEL[self.condition_class]

    def as_dict(self) -> dict[str, Any]:
        return {
            "class": self.condition_class,
            "label": self.label,
            "score": self.score,
            "confidence": self.confidence,
            "visible_defects": self.visible_defects,
            "potential_defects": self.potential_defects,
            "unobserved_parts": self.unobserved_parts,
            "contradictions": self.contradictions,
            "price_impact_pct": self.price_impact_pct,
            "missing_photos": self.missing_photos,
            "notes": self.notes,
            "rule": "L'assenza di difetti visibili non è assenza di difetti.",
        }


def _defect_row(d: dict[str, Any], penalty: float | None = None) -> dict[str, Any]:
    row = {
        "kind": d.get("kind"),
        "zone": d.get("zone"),
        "severity": d.get("severity"),
        "certainty": d.get("certainty"),
        "confidence": d.get("confidence"),
        "photo": d.get("photo"),
        "box": d.get("box"),
        "description": d.get("description"),
    }
    if penalty is not None:
        row["penalty"] = round(penalty, 1)
    return row


def _has_readable_tag(vision: dict[str, Any]) -> bool:
    return any(
        lab.get("type") == "paper_tag" and lab.get("state") == "present_readable"
        for lab in vision.get("labels") or []
    )


def _class_for(score: int, defects: int, vision: dict[str, Any], declared: Condition) -> str:
    if defects == 0 and score >= 90:
        if _has_readable_tag(vision):
            return "new_with_tags"
        if declared in (Condition.NEW_WITH_TAGS, Condition.NEW_WITHOUT_TAGS):
            return "new_without_tags_unverifiable"
        return "like_new"
    if score >= 80:
        return "very_good"
    if score >= 62:
        return "good"
    if score >= 40:
        return "fair"
    return "poor"


def build_condition_report(
    vision: dict[str, Any] | None,
    declared: str,
    plugin: CategoryPlugin,
    photo_count: int,
) -> ConditionReport:
    cov = photo_coverage(vision, plugin, photo_count)
    if not is_analysed(vision):
        return ConditionReport(
            "undeterminable",
            None,
            0,
            missing_photos=list(cov.checklist),
            notes=["Foto non analizzate: la condizione è solo quella dichiarata dal venditore."],
        )
    v = vision or {}
    visible: list[dict[str, Any]] = []
    potential: list[dict[str, Any]] = []
    penalty_total = 0.0
    for d in v.get("defects") or []:
        if d.get("certainty") not in ("certain", "probable"):
            potential.append(_defect_row(d))  # may be a shadow, a fold, compression
            continue
        pen = (
            BASE_PENALTY.get(d.get("kind", "other"), 8)
            * SEVERITY_FACTOR.get(d.get("severity", "minor"), 0.4)
            * CERTAINTY_FACTOR.get(d.get("certainty", "probable"), 0.7)
        )
        penalty_total += pen
        visible.append(_defect_row(d, pen))
    score = max(0, min(100, round(USED_BASE_SCORE - penalty_total)))
    try:
        declared_c = Condition(declared)
    except ValueError:
        declared_c = Condition.UNKNOWN

    quality = (cov.photo_quality if cov.photo_quality is not None else 60) / 100
    coverage = (cov.inspection_coverage if cov.inspection_coverage is not None else 40) / 100
    # Thin coverage lowers the confidence in "no defects", never the strictness of the class.
    confidence = round(100 * (0.35 + 0.65 * coverage) * (0.5 + 0.5 * quality) * plugin.confidence_factor)
    klass = _class_for(score, len(visible), v, declared_c)

    unobserved = list(
        dict.fromkeys(
            [
                *(v.get("unobserved_parts") or []),
                *(f"{r}: nessuna foto" for r in cov.roles_missing if r in ("back", "inside", "sole")),
            ]
        )
    )
    notes = []
    if not visible and not potential:
        notes.append("Nessun difetto visibile nelle foto analizzate: non significa che non ce ne siano.")
    if potential:
        notes.append("Alcune ombre o pieghe potrebbero essere difetti: verifica con foto ravvicinate.")
    if not plugin.covered:
        notes.append("Categoria senza controlli specifici: analisi generica, confidenza ridotta.")

    contradictions: list[dict[str, Any]] = []
    impact_pct = min(MAX_IMPACT_PCT, round(penalty_total * IMPACT_PER_POINT)) if visible else 0
    if declared_c in CONDITION_ORDER:
        declared_rank = CONDITION_ORDER[declared_c]
        if declared_c == Condition.NEW_WITH_TAGS and klass != "new_with_tags" and not _has_readable_tag(v):
            contradictions.append(
                {
                    "code": "declared_tags_not_shown",
                    "severity": "medium",
                    "detail": "Dichiarato nuovo con cartellino: nessuna foto mostra il cartellino.",
                    "verifiable": False,
                }
            )
        seen_rank = CLASS_RANK.get(klass)
        if seen_rank is not None and visible and seen_rank - declared_rank >= 1:
            worst = max(visible, key=lambda d: d["penalty"])
            where = f" ({worst['zone']})" if worst.get("zone") else ""
            contradictions.append(
                {
                    "code": "declared_better_than_seen",
                    "severity": "high"
                    if seen_rank - declared_rank >= 2 or worst["severity"] == "severe"
                    else "medium",
                    "detail": f"Dichiarato «{CONDITION_LABELS_IT[declared_c]}», dalle foto «{CLASS_LABEL[klass]}»: "
                    f"{worst['kind']}{where}.",
                    "price_impact_pct": impact_pct,
                    "photo": worst.get("photo"),
                    "box": worst.get("box"),
                    "verifiable": True,
                }
            )
    return ConditionReport(
        klass,
        score,
        max(0, min(100, confidence)),
        visible,
        potential,
        unobserved,
        contradictions,
        impact_pct,
        list(cov.checklist),
        notes,
    )
