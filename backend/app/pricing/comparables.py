"""Comparable selection and similarity scoring (pure, no I/O).

The SQL layer pre-filters candidates by brand and parent category (indexed); this module
scores each candidate against the subject on category, model, condition, size, title tokens,
gender, color, material, market and vintage-ness, then weights it by recency and by whether it
actually sold (realized prices beat asking prices).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from rapidfuzz import fuzz

from app.domain.enums import CONDITION_ORDER, Condition
from app.ingestion.normalizer import size_distance

# Relative value of an item in each condition vs. "very good" (used to normalize comparable prices).
CONDITION_MULTIPLIER: dict[str, float] = {
    Condition.NEW_WITH_TAGS: 1.15,
    Condition.NEW_WITHOUT_TAGS: 1.08,
    Condition.VERY_GOOD: 1.00,
    Condition.GOOD: 0.88,
    Condition.SATISFACTORY: 0.70,
    Condition.UNKNOWN: 0.95,
}

SIMILARITY_WEIGHTS: dict[str, float] = {
    "category": 0.25,
    "model": 0.20,
    "condition": 0.15,
    "size": 0.12,
    "title": 0.10,
    "gender": 0.05,
    "color": 0.04,
    "material": 0.04,
    "country": 0.03,
    "vintage": 0.02,
}
SOLD_WEIGHT_BONUS = 1.5
RECENCY_DECAY_DAYS = 75.0
MIN_SIMILARITY = 0.5
MAX_COMPARABLES = 60


@dataclass(frozen=True)
class ItemProfile:
    """Attributes used for matching; built from a Listing row or from ad-hoc input."""

    id: uuid.UUID | None
    title: str
    price: Decimal
    brand: str | None
    category: str | None
    parent_category: str | None
    model: str | None
    condition: str
    size: str | None
    gender: str | None = None
    color: str | None = None
    material: str | None = None
    country: str | None = None
    is_vintage: bool = False
    status: str = "active"
    published_at: datetime | None = None
    sold_at: datetime | None = None
    last_seen_at: datetime | None = None
    url: str | None = None
    favourite_count: int | None = None

    @property
    def is_sold(self) -> bool:
        return self.status in ("sold", "possibly_sold")

    def observed_at(self) -> datetime | None:
        return self.sold_at or self.last_seen_at or self.published_at


@dataclass
class ScoredComparable:
    item: ItemProfile
    similarity: float
    weight: float
    adjusted_price: float
    breakdown: dict[str, float] = field(default_factory=dict)
    included: bool = True
    exclusion_reason: str | None = None

    @property
    def is_sold(self) -> bool:
        return self.item.is_sold


def _condition_rank(value: str) -> int | None:
    try:
        return CONDITION_ORDER.get(Condition(value))
    except ValueError:
        return None


def _condition_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    ra, rb = _condition_rank(a), _condition_rank(b)
    if ra is None or rb is None:
        return 0.35
    return {1: 0.55, 2: 0.2}.get(abs(ra - rb), 0.0)


def similarity(subject: ItemProfile, cand: ItemProfile) -> tuple[float, dict[str, float]]:
    w = SIMILARITY_WEIGHTS
    parts: dict[str, float] = {}

    if subject.category and cand.category == subject.category:
        parts["category"] = w["category"]
    elif subject.parent_category and cand.parent_category == subject.parent_category:
        parts["category"] = w["category"] * 0.48
    else:
        parts["category"] = 0.0

    if subject.model is None:
        parts["model"] = w["model"] * 0.5  # no model info: neutral
    elif cand.model == subject.model:
        parts["model"] = w["model"]
    elif cand.model is None:
        parts["model"] = w["model"] * 0.35
    else:
        parts["model"] = 0.0

    parts["condition"] = w["condition"] * _condition_score(subject.condition, cand.condition)

    dist = size_distance(subject.size, cand.size)
    if dist is None:
        parts["size"] = w["size"] * 0.4
    else:
        parts["size"] = w["size"] * {0: 1.0, 1: 0.5}.get(dist, 0.0)

    parts["title"] = w["title"] * fuzz.token_set_ratio(subject.title.lower(), cand.title.lower()) / 100.0

    if subject.gender is None or cand.gender is None or "unisex" in (subject.gender, cand.gender):
        parts["gender"] = w["gender"] * 0.5
    else:
        parts["gender"] = w["gender"] if subject.gender == cand.gender else 0.0

    for key, a, b in (("color", subject.color, cand.color), ("material", subject.material, cand.material)):
        parts[key] = w[key] * (0.5 if a is None or b is None else (1.0 if a == b else 0.0))

    parts["country"] = w["country"] * (1.0 if subject.country and subject.country == cand.country else 0.5)
    parts["vintage"] = w["vintage"] if subject.is_vintage == cand.is_vintage else 0.0
    return round(sum(parts.values()), 4), parts


def adjust_for_condition(price: float, subject_condition: str, comp_condition: str) -> float:
    return (
        price
        * CONDITION_MULTIPLIER.get(subject_condition, 0.95)
        / CONDITION_MULTIPLIER.get(comp_condition, 0.95)
    )


def select_comparables(
    subject: ItemProfile,
    candidates: list[ItemProfile],
    now: datetime,
    min_similarity: float = MIN_SIMILARITY,
    max_count: int = MAX_COMPARABLES,
) -> list[ScoredComparable]:
    scored: list[ScoredComparable] = []
    for cand in candidates:
        if subject.id is not None and cand.id == subject.id:
            continue
        if subject.brand and cand.brand != subject.brand:
            continue
        sim, parts = similarity(subject, cand)
        if sim < min_similarity:
            continue
        observed = cand.observed_at() or now
        age_days = max(0.0, (now - observed).total_seconds() / 86400)
        weight = (
            sim**2 * math.exp(-age_days / RECENCY_DECAY_DAYS) * (SOLD_WEIGHT_BONUS if cand.is_sold else 1.0)
        )
        scored.append(
            ScoredComparable(
                item=cand,
                similarity=sim,
                weight=weight,
                adjusted_price=adjust_for_condition(float(cand.price), subject.condition, cand.condition),
                breakdown=parts,
            )
        )
    scored.sort(key=lambda c: (c.similarity, c.weight), reverse=True)
    return scored[:max_count]
