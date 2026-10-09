"""Result types of image analysis, shared by vision analyzers and the identification engine."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.enums import Certainty


class VisualFinding(BaseModel):
    """A single fact read from the photos, always with an explicit certainty level."""

    value: str
    certainty: Certainty = Certainty.PROBABLE
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence: str | None = None


DEFECT_KINDS = (
    # textiles
    "stain",
    "halo",
    "hole",
    "tear",
    "fading",
    "pilling",
    "snag",
    "seam",
    "deformation",
    "collar_cuffs",
    "print_damage",
    "wear",
    # shoes
    "sole_wear",
    "heel_wear",
    "creasing",
    "scuff",
    # bags and accessories
    "scratch",
    "corner_wear",
    "zipper",
    "lining_stain",
    "handle_wear",
    "other",
)


class DefectFinding(BaseModel):
    """A visible flaw: what, where on the item, how bad, how sure, and where in which photo.

    ``certainty`` ``unverifiable`` means it may be a shadow, a fold or compression: it is kept as a
    *potential* defect and never stated as fact."""

    kind: Literal[DEFECT_KINDS]  # type: ignore[valid-type]
    severity: Literal["minor", "moderate", "severe"] = "minor"
    certainty: Certainty = Certainty.PROBABLE
    description: str | None = None
    zone: str | None = None  # "manica sinistra", "colletto", "suola"...
    photo: int | None = None  # 0-based gallery position
    box: list[float] | None = None  # [x, y, w, h], 0..1 of the photo
    confidence: float = Field(default=0.5, ge=0, le=1)


LabelType = Literal[
    "brand_label",
    "care_label",
    "composition",
    "size_label",
    "sku_code",
    "barcode",
    "paper_tag",
    "proof_of_purchase",
]
LABEL_TYPES: tuple[str, ...] = (
    "brand_label",
    "care_label",
    "composition",
    "size_label",
    "sku_code",
    "barcode",
    "paper_tag",
    "proof_of_purchase",
)
LabelState = Literal[
    "present_readable", "present_unreadable", "not_visible", "absence_verifiable", "insufficient_information"
]


class LabelObservation(BaseModel):
    """One kind of label on the item and what the photos say about it.

    ``not_visible`` is not ``absent``: only ``absence_verifiable`` (the right spot is shown and has
    none) says there is none. A label is never claimed present without a photo that shows it."""

    type: LabelType
    state: LabelState
    photo: int | None = None
    text: str | None = None  # what was read, as read
    box: list[float] | None = None
    certainty: Certainty = Certainty.PROBABLE


PhotoRoleName = Literal[
    "front",
    "back",
    "label",
    "care_label",
    "detail",
    "worn",
    "flat_lay",
    "packshot",
    "sole",
    "inside",
    "other",
]


class OcrLineOut(BaseModel):
    text: str
    confidence: float
    box: list[float]


class OcrPhoto(BaseModel):
    """What the local OCR read on one photo."""

    photo: int  # 0-based gallery position
    lines: list[OcrLineOut] = Field(default_factory=list)


class PhotoRole(BaseModel):
    photo: int
    role: PhotoRoleName


class PhotoQuality(BaseModel):
    photo_count: int = 0
    analyzed_count: int = 0
    avg_sharpness: float | None = None  # 0..1, higher = sharper
    avg_brightness: float | None = None  # 0..1
    has_label_photo: bool | None = None
    score: int = Field(default=50, ge=0, le=100)


class PhotoCheck(BaseModel):
    """Can this photo prove anything? Measured on the full-resolution file."""

    photo: int  # 0-based gallery position
    usable: bool
    reason: str | None = (
        None  # sfocata | troppo piccola | troppo scura | sovraesposta | tagliata | troppo lontana
    )
    width: int | None = None
    height: int | None = None
    sharpness: float | None = None
    screenshot: bool = False


class PhotoEvidence(BaseModel):
    """One detail read on one photo (label, code, logo, stitching...), with where it is."""

    photo: int
    kind: str
    verdict: Literal["consistent", "concern", "unreadable"]
    certainty: Certainty = Certainty.PROBABLE
    detail: str = ""
    box: list[float] | None = None  # [x, y, w, h], 0..1 of the photo


class Provenance(BaseModel):
    """Photos that are not of the item in hand (counts) and which ones."""

    stock_or_catalog: int = 0
    screenshots: int = 0
    foreign_watermarks: int = 0
    edited_or_generated: int = 0
    reused_photos: list[int] = Field(default_factory=list)  # also in another seller's listing
    catalog_photos: list[int] = Field(default_factory=list)  # in listings of 3+ sellers
    notes: list[str] = Field(default_factory=list)


class ImageAnalysis(BaseModel):
    """Aggregated image analysis for a listing."""

    analyzer: str
    brand: VisualFinding | None = None
    logo: VisualFinding | None = None
    model: VisualFinding | None = None
    size_label: VisualFinding | None = None
    composition: VisualFinding | None = None
    product_code: VisualFinding | None = None
    category: VisualFinding | None = None
    color: VisualFinding | None = None
    condition_estimate: VisualFinding | None = None
    defects: list[DefectFinding] = Field(default_factory=list)
    authenticity_concerns: list[str] = Field(default_factory=list)
    authenticity_positive_signals: list[str] = Field(default_factory=list)
    photo_quality: PhotoQuality = Field(default_factory=PhotoQuality)
    perceptual_hashes: list[str] = Field(default_factory=list)
    # Per gallery position (None: photo not readable).
    photo_hashes: list[str | None] = Field(default_factory=list)
    photo_checks: list[PhotoCheck] = Field(default_factory=list)
    photo_findings: list[PhotoEvidence] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    # What kind of photo each one is, and the state of each kind of label (see ``LabelObservation``).
    photo_roles: list[PhotoRole] = Field(default_factory=list)
    labels: list[LabelObservation] = Field(default_factory=list)
    # Parts of the item no photo shows ("interno del colletto", "retro").
    unobserved_parts: list[str] = Field(default_factory=list)
    # Text read on the photos by the local OCR (``app.vision.ocr``), photo by photo; empty photos omitted.
    ocr: list[OcrPhoto] = Field(default_factory=list)
    ocr_engine: str | None = None
    # Facts parsed from that text (size, composition, codes, country, brands, label photos).
    ocr_facts: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
