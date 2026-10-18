"""Comparable selection and similarity scoring (pure, no I/O).

The SQL layer pre-filters candidates by brand and parent category (indexed); this module
scores each candidate against the subject on category, model, condition, size, title tokens,
gender, color, material, market and vintage-ness, then weights it by recency and by the source of
its price: concluded sales beat asking prices, and the user's own resales beat everything
(``SOURCE_WEIGHTS``). Besides marketplace listings, the pool may hold the user's own purchases
and resales and prices found on other marketplaces (``app.pricing.evidence``).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from functools import lru_cache

from rapidfuzz import fuzz

from app.domain.enums import CONDITION_ORDER, Condition
from app.ingestion.normalizer import size_distance
from app.pricing.lines import line_overlap, line_tokens, model_words, same_line, title_words

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
# Weight of a comparable by where its price comes from (replaces a single "sold" bonus): the
# user's resales (price really received) count most; a purchase (price really paid, but the user
# buys below market) and a Vinted sale (last price seen) as one concluded sale; a sale reported by
# another marketplace a little less; asking prices least, other marketplaces' asks half of
# Vinted's. Asking prices are further scaled by the active factor in ``estimate_market_value``.
SOURCE_WEIGHTS: dict[str, float] = {
    "own_sale": 5.0,
    "own_purchase": 1.5,
    "vinted_sold": 1.5,
    "external_sold": 1.0,
    "vinted_asking": 1.0,
    "external_asking": 0.5,
}
SALE_SOURCES = ("own_sale", "own_purchase", "vinted_sold", "external_sold")
ASK_SOURCES = ("vinted_asking", "external_asking")
# Half-life of about a month (e-folding 45 days): a price older than three months says little about today's.
RECENCY_DECAY_DAYS = 45.0
MIN_SIMILARITY = 0.5
MAX_COMPARABLES = 60

AUDIENCE_KIDS = "kids"
TIER_MODEL = "model"
TIER_LINE = "line"
TIER_FAMILY = "family"
_TIER_RANK = {TIER_MODEL: 2, TIER_LINE: 2, TIER_FAMILY: 1}
# Why a comparable is kept out of the price (``ScoredComparable.exclusion_reason``).
REASON_OTHER_LINE = "other_line"


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
    removed_at: datetime | None = None
    url: str | None = None
    favourite_count: int | None = None
    tracked: bool = False  # followed by the user (its selling time is observed first hand)
    # Where the price comes from: "listing" (a marketplace listing, sold or on sale), "own_sale",
    # "own_purchase", "external_sold" or "external_asking" (see ``evidence_source``).
    source: str = "listing"
    source_name: str | None = None  # "vinted", "tracking", "ebay.it", ...
    ref_id: int | None = None  # sold_sales.id (own records) or external_prices.id (external rows)
    linked_listing_id: uuid.UUID | None = None  # the listing an own purchase was bought from
    # "kids" | "adult" | None (unknown) and "low" | "mid" | "high" | None: see ``app.pricing.lines``.
    audience: str | None = None
    variant: str | None = None
    # The Vinted market (domain) the listing was seen on: "it", "fr", "de"... A price from another market is not the
    # price of this one (see ``app.ingestion.normalizer.market_from_url``); ``None``: not known (own records, ...).
    market: str | None = None

    @property
    def is_sold(self) -> bool:
        return self.status == "sold"

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
    # "model" | "line" | "family": see the module docstring. A hand-built comparable is its own model.
    tier: str = TIER_MODEL

    @property
    def is_sold(self) -> bool:
        return self.item.is_sold


def evidence_source(item: ItemProfile) -> str:
    """One of ``SOURCE_WEIGHTS``: a listing is a Vinted sale when sold, an asking price otherwise."""
    if item.source == "listing":
        return "vinted_sold" if item.status == "sold" else "vinted_asking"
    return item.source


def _condition_rank(value: str) -> int | None:
    try:
        return CONDITION_ORDER.get(Condition(value))
    except ValueError:
        return None


@lru_cache(maxsize=256)
def _condition_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    ra, rb = _condition_rank(a), _condition_rank(b)
    if ra is None or rb is None:
        return 0.35
    return {1: 0.55, 2: 0.2}.get(abs(ra - rb), 0.0)


def _model_part(subject: ItemProfile, cand: ItemProfile, subject_tokens: frozenset[str] | None = None) -> float:
    """The model term. A subject whose model the catalog knows is compared on the model (a different
    height of the same shoe is a different model); one it does not know, on the distinctive words of
    the title (neutral when either title has none: nothing is known, nothing is assumed)."""
    w = _W["model"]
    if subject.model is None:
        mine = subject_tokens if subject_tokens is not None else line_tokens(subject.title, subject.size, subject.brand)
        theirs = line_tokens(cand.title, cand.size, cand.brand) if mine else frozenset()
        if not mine or not theirs:
            return w * 0.5
        return w * line_overlap(mine, theirs)
    if cand.model == subject.model:
        if subject.variant and cand.variant and subject.variant != cand.variant:
            return 0.0
        return w
    if cand.model is None:
        return w * 0.35
    return 0.0


def similarity(subject: ItemProfile, cand: ItemProfile) -> tuple[float, dict[str, float]]:
    w = SIMILARITY_WEIGHTS
    parts: dict[str, float] = {}

    if subject.category and cand.category == subject.category:
        parts["category"] = w["category"]
    elif subject.parent_category and cand.parent_category == subject.parent_category:
        parts["category"] = w["category"] * 0.48
    else:
        parts["category"] = 0.0

    parts["model"] = _model_part(subject, cand)

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


_W = SIMILARITY_WEIGHTS
_SIZE_FACTOR = {0: 1.0, 1: 0.5}


def similarity_score(
    subject: ItemProfile,
    cand: ItemProfile,
    subject_title: str | None = None,
    subject_tokens: frozenset[str] | None = None,
) -> float:
    """Same value as ``similarity(...)[0]`` without building the breakdown (hot path).

    The terms are added in the same order as the breakdown dict, so results are bit-identical.
    ``subject_tokens``: the subject's distinctive words when the caller already has them.
    """
    if subject.category and cand.category == subject.category:
        p_category = _W["category"]
    elif subject.parent_category and cand.parent_category == subject.parent_category:
        p_category = _W["category"] * 0.48
    else:
        p_category = 0.0
    p_model = _model_part(subject, cand, subject_tokens)
    p_condition = _W["condition"] * _condition_score(subject.condition, cand.condition)
    dist = size_distance(subject.size, cand.size)
    p_size = _W["size"] * 0.4 if dist is None else _W["size"] * _SIZE_FACTOR.get(dist, 0.0)
    title = subject_title if subject_title is not None else subject.title.lower()
    p_title = _W["title"] * fuzz.token_set_ratio(title, cand.title.lower()) / 100.0
    if subject.gender is None or cand.gender is None or "unisex" in (subject.gender, cand.gender):
        p_gender = _W["gender"] * 0.5
    else:
        p_gender = _W["gender"] if subject.gender == cand.gender else 0.0
    a, b = subject.color, cand.color
    p_color = _W["color"] * (0.5 if a is None or b is None else (1.0 if a == b else 0.0))
    a, b = subject.material, cand.material
    p_material = _W["material"] * (0.5 if a is None or b is None else (1.0 if a == b else 0.0))
    p_country = _W["country"] * (1.0 if subject.country and subject.country == cand.country else 0.5)
    p_vintage = _W["vintage"] if subject.is_vintage == cand.is_vintage else 0.0
    total = (
        0
        + p_category
        + p_model
        + p_condition
        + p_size
        + p_title
        + p_gender
        + p_color
        + p_material
        + p_country
        + p_vintage
    )
    return round(total, 4)


def adjust_for_condition(price: float, subject_condition: str, comp_condition: str) -> float:
    return (
        price
        * CONDITION_MULTIPLIER.get(subject_condition, 0.95)
        / CONDITION_MULTIPLIER.get(comp_condition, 0.95)
    )


def sort_key(c: ScoredComparable) -> tuple[int, float, float]:
    """Most specific tier first, then most similar, then heaviest (sort descending)."""
    return _TIER_RANK[c.tier], c.similarity, c.weight


def subject_kind(subject: ItemProfile) -> str:
    """Which tier carries the price of this subject: ``model`` (the catalog knows it), ``line`` (the title
    has distinctive words) or ``family`` (nothing distinctive: only brand and category are known)."""
    if subject.model:
        return TIER_MODEL
    if line_tokens(subject.title, subject.size, subject.brand):
        return TIER_LINE
    return TIER_FAMILY


def _tier(
    kind: str,
    subject: ItemProfile,
    cand: ItemProfile,
    subject_tokens: frozenset[str],
    subject_model_words: frozenset[str],
) -> str:
    if kind == TIER_FAMILY:
        return TIER_FAMILY
    if subject.variant and cand.variant and subject.variant != cand.variant:
        return TIER_FAMILY  # same shoe, other height: a different price level
    if kind == TIER_MODEL:
        if cand.model == subject.model:
            return TIER_MODEL
        # A listing the catalog left unnamed whose title carries all the words of the model.
        if cand.model is None and subject_model_words and subject_model_words <= title_words(cand.title):
            return TIER_MODEL
        return TIER_FAMILY
    if same_line(subject_tokens, line_tokens(cand.title, cand.size, cand.brand)):
        return TIER_LINE
    return TIER_FAMILY


def tier_of(subject: ItemProfile, cand: ItemProfile) -> str:
    """The tier one candidate would get against the subject (for explaining a stored comparable)."""
    kind = subject_kind(subject)
    tokens = line_tokens(subject.title, subject.size, subject.brand) if kind == TIER_LINE else frozenset()
    words = model_words(subject.model) if kind == TIER_MODEL and subject.model else frozenset()
    return _tier(kind, subject, cand, tokens, words)


def select_comparables(
    subject: ItemProfile,
    candidates: list[ItemProfile],
    now: datetime,
    min_similarity: float = MIN_SIMILARITY,
    max_count: int = MAX_COMPARABLES,
) -> list[ScoredComparable]:
    """Score the candidates against the subject and tier them (most specific tier first, then most similar).

    Hard filters: another brand, and kids against not-kids (a child's jacket is never a comparable of an
    adult's, whatever the brand and model; a children's item is marked as such by its title or its audience,
    an adult's mostly is not, so an unmarked listing counts as an adult one). Everything else passing ``min_similarity`` is kept, with
    its tier: a price is built from one tier only (``app.pricing.market_value``).
    """
    scored: list[ScoredComparable] = []
    subject_title = subject.title.lower()
    kind = subject_kind(subject)
    s_tokens = line_tokens(subject.title, subject.size, subject.brand) if kind == TIER_LINE else frozenset()
    m_words = model_words(subject.model) if kind == TIER_MODEL and subject.model else frozenset()
    for cand in candidates:
        if subject.id is not None and cand.id == subject.id:
            continue
        if subject.brand and cand.brand != subject.brand:
            continue
        if subject.market and cand.market and cand.market != subject.market:
            continue  # another country's sales are another market, not a comparable (PX-09)
        if (subject.audience == AUDIENCE_KIDS) != (cand.audience == AUDIENCE_KIDS):
            continue  # a child's item and an adult's are never comparables (kids are marked, adults mostly not)
        sim = similarity_score(subject, cand, subject_title, s_tokens or None)
        if sim < min_similarity:
            continue
        observed = cand.observed_at() or now
        age_days = max(0.0, (now - observed).total_seconds() / 86400)
        weight = (
            sim**2 * math.exp(-age_days / RECENCY_DECAY_DAYS) * SOURCE_WEIGHTS.get(evidence_source(cand), 1.0)
        )
        scored.append(
            ScoredComparable(
                item=cand,
                similarity=sim,
                weight=weight,
                adjusted_price=adjust_for_condition(float(cand.price), subject.condition, cand.condition),
                tier=_tier(kind, subject, cand, s_tokens, m_words),
            )
        )
    scored.sort(key=sort_key, reverse=True)
    # Per-comparable breakdowns are available on demand via similarity(); none are needed here.
    return scored[:max_count]
