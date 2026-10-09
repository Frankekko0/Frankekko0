"""Photo quality and inspection coverage: two independent scores, and what photos are missing.

* ``photo_quality`` (0-100): are the photos technically good enough to prove anything (sharp,
  large, lit, not duplicated, not screenshots)? Measured on the files.
* ``inspection_coverage`` (0-100): do the photos show what an inspection of this kind of item
  needs (front, back, labels, details, sole...)? Read from the photo roles.

A good-looking photo of the wrong thing and a blurry photo of the right one fail differently, so
the two never merge. Without a photo analysis neither role information nor a coverage score is
invented: ``inspection_coverage`` is ``None`` and the checklist says the photos were not analysed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analysis.categories import ROLE_REQUEST, CategoryPlugin


@dataclass(frozen=True)
class PhotoCoverage:
    photo_quality: int | None
    inspection_coverage: int | None
    roles_seen: tuple[str, ...]
    roles_missing: tuple[str, ...]
    checklist: tuple[str, ...]  # what to ask the seller for
    analysed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "photo_quality": self.photo_quality,
            "inspection_coverage": self.inspection_coverage,
            "roles_seen": list(self.roles_seen),
            "roles_missing": list(self.roles_missing),
            "checklist": list(self.checklist),
            "analysed": self.analysed,
        }


def is_analysed(vision: dict[str, Any] | None) -> bool:
    """A photo analysis really ran (the heuristic fallback only measures files)."""
    return bool(vision) and (vision or {}).get("analyzer") not in (None, "heuristic")


def photo_quality_score(vision: dict[str, Any] | None) -> int | None:
    checks = (vision or {}).get("photo_checks") or []
    if not checks:
        return None
    n = len(checks)
    usable = sum(1 for c in checks if c.get("usable")) / n
    sharp = [c["sharpness"] for c in checks if c.get("sharpness") is not None]
    sharp_norm = min(1.0, (sum(sharp) / len(sharp)) / 0.5) if sharp else usable
    hashes = [h for h in ((vision or {}).get("photo_hashes") or []) if h]
    dup_share = 1 - len(set(hashes)) / len(hashes) if hashes else 0.0
    shots = sum(1 for c in checks if c.get("screenshot")) / n
    score = 100 * (0.5 * usable + 0.25 * sharp_norm + 0.15 * (1 - dup_share) + 0.10 * (1 - shots))
    return max(0, min(100, round(score)))


def roles_seen(vision: dict[str, Any] | None) -> set[str]:
    """Roles the photos cover: the roles the analysis gave, plus the labels it could read."""
    v = vision or {}
    roles = {r["role"] for r in v.get("photo_roles") or [] if r.get("role")}
    for lab in v.get("labels") or []:
        if lab.get("state") == "present_readable":
            if lab.get("type") in ("brand_label", "size_label", "sku_code"):
                roles.add("label")
            elif lab.get("type") in ("care_label", "composition"):
                roles.add("care_label")
    for f in v.get("photo_findings") or []:  # findings on a label or care tag count as seen
        if f.get("verdict") != "unreadable":
            if f.get("kind") == "label":
                roles.add("label")
            elif f.get("kind") == "care_tag":
                roles.add("care_label")
    return roles


def photo_coverage(vision: dict[str, Any] | None, plugin: CategoryPlugin, photo_count: int) -> PhotoCoverage:
    quality = photo_quality_score(vision)
    if not is_analysed(vision):
        note = "foto non analizzate: ruolo e copertura non valutabili"
        return PhotoCoverage(quality, None, (), (), (note,) if photo_count else ("nessuna foto",), False)
    seen = roles_seen(vision)
    weights = plugin.role_weights()
    got = sum(w for r, w in weights.items() if r in seen)
    missing = tuple(r for r in weights if r not in seen)
    return PhotoCoverage(
        quality,
        round(100 * got),
        tuple(sorted(seen)),
        missing,
        tuple(f"Chiedi la {ROLE_REQUEST[r]}" for r in missing if r in ROLE_REQUEST),
        True,
    )
