"""Domain enumerations shared by ORM models, schemas and engines."""

from __future__ import annotations

from enum import StrEnum


class Condition(StrEnum):
    NEW_WITH_TAGS = "new_with_tags"
    NEW_WITHOUT_TAGS = "new_without_tags"
    VERY_GOOD = "very_good"
    GOOD = "good"
    SATISFACTORY = "satisfactory"
    UNKNOWN = "unknown"


CONDITION_ORDER: dict[Condition, int] = {
    Condition.NEW_WITH_TAGS: 0,
    Condition.NEW_WITHOUT_TAGS: 1,
    Condition.VERY_GOOD: 2,
    Condition.GOOD: 3,
    Condition.SATISFACTORY: 4,
}

CONDITION_LABELS_IT: dict[Condition, str] = {
    Condition.NEW_WITH_TAGS: "Nuovo con cartellino",
    Condition.NEW_WITHOUT_TAGS: "Nuovo senza cartellino",
    Condition.VERY_GOOD: "Ottime condizioni",
    Condition.GOOD: "Buone condizioni",
    Condition.SATISFACTORY: "Discrete condizioni",
    Condition.UNKNOWN: "Condizione non indicata",
}


class ListingStatus(StrEnum):
    ACTIVE = "active"
    RESERVED = "reserved"
    SOLD = "sold"
    POSSIBLY_SOLD = "possibly_sold"
    REMOVED = "removed"
    UNKNOWN = "unknown"


class Certainty(StrEnum):
    """How sure the system is about an extracted fact."""

    CERTAIN = "certain"  # provided by the marketplace in a structured field / read on a label
    PROBABLE = "probable"  # inferred from text or images with good evidence
    UNVERIFIABLE = "unverifiable"  # cannot be confirmed from the available data


class DemandLevel(StrEnum):
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class VelocityBucket(StrEnum):
    D0_3 = "0-3"
    D4_7 = "4-7"
    D8_14 = "8-14"
    D15_30 = "15-30"
    D30_PLUS = "30+"


class DealTier(StrEnum):
    EXCEPTIONAL = "exceptional"
    EXCELLENT = "excellent"
    GOOD = "good"
    MODERATE = "moderate"
    LOW = "low_priority"


class RiskLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class Verdict(StrEnum):
    BUY = "BUY"
    CONSIDER = "CONSIDER"
    SKIP = "SKIP"


class RecommendedAction(StrEnum):
    BUY_NOW = "buy_now"
    MAKE_OFFER = "make_offer"
    WATCH = "watch"
    SKIP = "skip"


class FavoriteState(StrEnum):
    SAVED = "saved"
    IGNORED = "ignored"
    PURCHASED = "purchased"
    WATCHING = "watching"
    SOLD = "sold"


class AlertType(StrEnum):
    NEW_OPPORTUNITY = "new_opportunity"
    ULTRA_DEAL = "ultra_deal"
    PRICE_DROP = "price_drop"
    WATCHLIST_MATCH = "watchlist_match"
    SYSTEM = "system"


class AlertPriority(StrEnum):
    NORMAL = "normal"
    HIGH = "high"


class NotificationChannelType(StrEnum):
    IN_APP = "in_app"
    WEB_PUSH = "web_push"
    EMAIL = "email"
    TELEGRAM = "telegram"
    DISCORD = "discord"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class InventoryStatus(StrEnum):
    IN_STOCK = "in_stock"
    LISTED = "listed"
    SOLD = "sold"
    RETURNED = "returned"


class BrandTier(StrEnum):
    LUXURY = "luxury"
    PREMIUM = "premium"
    STREETWEAR = "streetwear"
    MID = "mid"
    FAST_FASHION = "fast_fashion"
    SPORT = "sport"
    OUTDOOR = "outdoor"


class Gender(StrEnum):
    MEN = "men"
    WOMEN = "women"
    UNISEX = "unisex"
    KIDS = "kids"
