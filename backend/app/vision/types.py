"""Result types of image analysis, shared by vision analyzers and the identification engine."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.enums import Certainty


class VisualFinding(BaseModel):
    """A single fact read from the photos, always with an explicit certainty level."""

    value: str
    certainty: Certainty = Certainty.PROBABLE
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence: str | None = None


class DefectFinding(BaseModel):
    kind: Literal["stain", "hole", "fading", "wear", "pilling", "tear", "other"]
    severity: Literal["minor", "moderate", "severe"] = "minor"
    certainty: Certainty = Certainty.PROBABLE
    description: str | None = None


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
    notes: list[str] = Field(default_factory=list)
