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
    notes: list[str] = Field(default_factory=list)
