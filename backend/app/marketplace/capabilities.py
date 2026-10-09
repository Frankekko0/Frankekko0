"""What each source of listing data actually provides (``source_capabilities``).

The analysis adapts to what a source exposes and reports what is missing instead of guessing.
The tables below mirror what the code really receives today:

* item pages (extension, bookmarklet, opt-in public fetch) -> ``ParsedItem`` in
  ``acquisition/vinted_parser.py``;
* search cards -> title, price, brand, size, condition and the cover photo;
* a configured provider -> ``ProviderListing`` / ``ProviderSeller`` in ``marketplace/base.py``;
* the manual form -> ``ManualListingInput``, declared by the user, not observed.

When a source gains a field, change the table and its test together.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from app.domain.enums import AcquisitionMode


class SourceField(StrEnum):
    TITLE = "title"
    DESCRIPTION = "description"
    PRICE = "price"
    STATUS = "status"
    PHOTO_COVER = "photo_cover"
    PHOTOS_ALL = "photos_all"
    PHOTOS_FULL_RES = "photos_full_res"
    BRAND = "brand"
    SIZE = "size"
    CONDITION = "condition"
    COLOR = "color"
    MATERIAL = "material"
    CATEGORY = "category"
    SHIPPING_COST = "shipping_cost"
    BUYER_PROTECTION = "buyer_protection"
    LOCATION = "location"
    PUBLISHED_AT = "published_at"
    RENEWED_AT = "renewed_at"
    FAVOURITES = "favourites"
    VIEWS = "views"
    SELLER_RATING = "seller_rating"
    SELLER_ACCOUNT_AGE = "seller_account_age"
    SELLER_OTHER_ITEMS = "seller_other_items"
    BUNDLE = "bundle"


F = SourceField

Provenance = Literal["observed", "user_declared", "provider"]

_CARD = frozenset({F.TITLE, F.PRICE, F.STATUS, F.PHOTO_COVER, F.BRAND, F.SIZE, F.CONDITION})
_ITEM_PAGE = frozenset(
    {
        F.TITLE,
        F.DESCRIPTION,
        F.PRICE,
        F.STATUS,
        F.PHOTO_COVER,
        F.PHOTOS_ALL,
        F.PHOTOS_FULL_RES,
        F.BRAND,
        F.SIZE,
        F.CONDITION,
        F.COLOR,
        F.MATERIAL,
        F.CATEGORY,
        F.SHIPPING_COST,
        F.BUYER_PROTECTION,
        F.PUBLISHED_AT,
        F.FAVOURITES,
        F.VIEWS,
        F.SELLER_RATING,
    }
)
_MANUAL = frozenset(
    {
        F.TITLE,
        F.DESCRIPTION,
        F.PRICE,
        F.STATUS,
        F.PHOTO_COVER,
        F.PHOTOS_ALL,
        F.BRAND,
        F.SIZE,
        F.CONDITION,
        F.COLOR,
        F.MATERIAL,
        F.CATEGORY,
        F.SHIPPING_COST,
        F.BUYER_PROTECTION,
        F.LOCATION,
        F.PUBLISHED_AT,
        F.FAVOURITES,
        F.VIEWS,
        F.SELLER_RATING,
    }
)
_PROVIDER = (_ITEM_PAGE - {F.PHOTOS_FULL_RES}) | {
    F.LOCATION,
    F.SELLER_ACCOUNT_AGE,
    F.SELLER_OTHER_ITEMS,
}

MODE_FIELDS: dict[AcquisitionMode, frozenset[SourceField]] = {
    AcquisitionMode.EXTENSION_ITEM: _ITEM_PAGE,
    AcquisitionMode.EXTENSION_DEEP: _ITEM_PAGE,
    AcquisitionMode.BOOKMARKLET: _ITEM_PAGE,
    AcquisitionMode.PUBLIC_FETCH: _ITEM_PAGE,
    AcquisitionMode.EXTENSION_CARD: _CARD,
    AcquisitionMode.EXTENSION_SCAN: _CARD,  # automatic saved-search scan (slated for removal, Q1a)
    AcquisitionMode.BATCH_IMPORT: _CARD,
    AcquisitionMode.EXTENSION_REFRESH: frozenset({F.PRICE, F.STATUS}),
    AcquisitionMode.EMAIL: frozenset({F.TITLE, F.PRICE, F.STATUS}),
    AcquisitionMode.LINK_IMPORT: frozenset(),
    AcquisitionMode.MANUAL_FORM: _MANUAL,
    AcquisitionMode.PROVIDER_SCAN: _PROVIDER,
    AcquisitionMode.MIGRATED: frozenset(),
}

MODE_PROVENANCE: dict[AcquisitionMode, Provenance] = {
    AcquisitionMode.MANUAL_FORM: "user_declared",
    AcquisitionMode.PROVIDER_SCAN: "provider",
}


def capabilities_for(mode: AcquisitionMode | str) -> frozenset[SourceField]:
    """Fields a capture made in ``mode`` can contain."""
    return MODE_FIELDS[AcquisitionMode(mode)]


def provenance_for(mode: AcquisitionMode | str) -> Provenance:
    """``observed`` (read from the page), ``user_declared`` (typed) or ``provider`` (structured feed)."""
    return MODE_PROVENANCE.get(AcquisitionMode(mode), "observed")


def missing_fields(mode: AcquisitionMode | str, required: Iterable[SourceField]) -> list[SourceField]:
    """Required fields the source cannot provide, in a stable order (for coverage reports)."""
    have = capabilities_for(mode)
    return [f for f in SourceField if f in set(required) and f not in have]
