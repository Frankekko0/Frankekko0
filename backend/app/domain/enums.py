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
    """Lifecycle of a listing.

    ``sold`` is only ever set on positive evidence (the page, a notification email or the provider
    says so). A listing that disappears without that evidence is ``removed``: no sale is inferred.
    """

    ACTIVE = "active"
    RESERVED = "reserved"
    SOLD = "sold"
    REMOVED = "removed"
    UNKNOWN = "unknown"


OPEN_STATUSES = frozenset({ListingStatus.ACTIVE, ListingStatus.RESERVED, ListingStatus.UNKNOWN})
CLOSED_STATUSES = frozenset({ListingStatus.SOLD, ListingStatus.REMOVED})


class AcquisitionMode(StrEnum):
    """How a piece of data reached FlipFinder (stored on listings, snapshots, analyses, attempts)."""

    PROVIDER_SCAN = "provider_scan"  # configured provider (authorized feed)
    EXTENSION_ITEM = "extension_item"  # browser extension, item page the user opened
    EXTENSION_CARD = "extension_card"  # browser extension, card seen while scrolling
    EXTENSION_DEEP = "extension_deep"  # browser extension, deep analysis on the user's command
    EXTENSION_REFRESH = "extension_refresh"  # browser extension, slow background status check
    EXTENSION_SCAN = "extension_scan"  # browser extension, automatic scan of a saved search (opt-in)
    BATCH_IMPORT = "batch_import"  # a whole search page sent in one click
    LINK_IMPORT = "link_import"  # pasted link(s)
    MANUAL_FORM = "manual_form"  # data typed in the Analyze form
    BOOKMARKLET = "bookmarklet"
    EMAIL = "email"  # Vinted notification email
    PUBLIC_FETCH = "public_fetch"  # server-side read of a public page (opt-in)
    MIGRATED = "migrated"  # existed before acquisition modes were recorded


class CaptureLevel(StrEnum):
    """How much of a listing is known. Never downgraded by a poorer capture."""

    LINK = "link"  # only the link (Vinted ID + slug)
    CARD = "card"  # a search/catalog card: title, price, brand, size, condition, one photo
    FULL = "full"  # the item page: every field and photo


CAPTURE_RANK: dict[str, int] = {CaptureLevel.LINK: 0, CaptureLevel.CARD: 1, CaptureLevel.FULL: 2}


class StatusEvidence(StrEnum):
    """Where an observed status comes from."""

    PAGE = "page"  # read on the listing page/card
    EMAIL = "email"  # "your favourite has been sold" notification
    PROVIDER = "provider"  # a structured provider field
    NOT_FOUND = "not_found"  # the page no longer exists (removed, sale not inferred)
    UNREACHABLE = "unreachable"  # could not check (blocked, network): status unchanged


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
