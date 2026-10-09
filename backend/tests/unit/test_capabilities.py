"""source_capabilities: what each acquisition mode can provide, checked against the real parser."""

from pathlib import Path

import pytest

from app.acquisition.vinted_parser import ParsedItem, parse_item_html
from app.domain.enums import AcquisitionMode
from app.marketplace.capabilities import (
    MODE_FIELDS,
    SourceField,
    capabilities_for,
    missing_fields,
    provenance_for,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "vinted" / "item_active.html"
F = SourceField


def test_every_acquisition_mode_has_a_declared_profile() -> None:
    assert set(MODE_FIELDS) == set(AcquisitionMode)  # a new mode must declare what it provides


def test_item_page_provides_the_full_listing_but_not_what_the_page_lacks() -> None:
    page = capabilities_for(AcquisitionMode.EXTENSION_ITEM)
    assert {F.DESCRIPTION, F.PHOTOS_ALL, F.PHOTOS_FULL_RES, F.SHIPPING_COST, F.BUYER_PROTECTION} <= page
    assert not page & {F.LOCATION, F.RENEWED_AT, F.SELLER_ACCOUNT_AGE, F.SELLER_OTHER_ITEMS, F.BUNDLE}


def test_cards_and_links_are_progressively_poorer() -> None:
    card = capabilities_for(AcquisitionMode.EXTENSION_CARD)
    assert F.PRICE in card and F.PHOTO_COVER in card
    assert not card & {F.DESCRIPTION, F.PHOTOS_ALL, F.SHIPPING_COST}
    assert capabilities_for(AcquisitionMode.LINK_IMPORT) == frozenset()
    assert capabilities_for(AcquisitionMode.EXTENSION_REFRESH) == {F.PRICE, F.STATUS}


def test_only_the_provider_knows_the_seller_account_age() -> None:
    with_age = [m for m in AcquisitionMode if F.SELLER_ACCOUNT_AGE in capabilities_for(m)]
    assert with_age == [AcquisitionMode.PROVIDER_SCAN]


def test_missing_fields_are_reported_in_a_stable_order() -> None:
    wanted = [F.SELLER_ACCOUNT_AGE, F.PRICE, F.DESCRIPTION, F.PHOTOS_ALL]
    assert missing_fields(AcquisitionMode.EXTENSION_CARD, wanted) == [
        F.DESCRIPTION,
        F.PHOTOS_ALL,
        F.SELLER_ACCOUNT_AGE,
    ]
    assert missing_fields(AcquisitionMode.EXTENSION_ITEM, [F.PRICE, F.TITLE]) == []


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (AcquisitionMode.MANUAL_FORM, "user_declared"),
        (AcquisitionMode.PROVIDER_SCAN, "provider"),
        (AcquisitionMode.EXTENSION_ITEM, "observed"),
        (AcquisitionMode.EMAIL, "observed"),
    ],
)
def test_provenance_of_each_source(mode: AcquisitionMode, expected: str) -> None:
    assert provenance_for(mode) == expected


# The parser is the ground truth for page captures: the table must not promise more than it parses.
PARSED_ATTR: dict[SourceField, str] = {
    F.TITLE: "title",
    F.DESCRIPTION: "description",
    F.PRICE: "price",
    F.STATUS: "status",
    F.PHOTOS_ALL: "images",
    F.BRAND: "brand",
    F.SIZE: "size",
    F.CONDITION: "condition",
    F.COLOR: "color",
    F.MATERIAL: "material",
    F.CATEGORY: "category_path",
    F.SHIPPING_COST: "shipping_fee",
    F.BUYER_PROTECTION: "buyer_protection_fee",
    F.PUBLISHED_AT: "published_at",
    F.FAVOURITES: "favourite_count",
    F.VIEWS: "view_count",
    F.SELLER_RATING: "seller_rating",
}


def test_item_page_table_matches_what_the_parser_extracts() -> None:
    item = parse_item_html(FIXTURE.read_text(encoding="utf-8"), "https://www.vinted.it/items/4242424242-x")
    for fld in capabilities_for(AcquisitionMode.EXTENSION_ITEM) & PARSED_ATTR.keys():
        value = getattr(item, PARSED_ATTR[fld])
        assert value not in (None, "", []), f"{fld} is declared but the parser returned nothing"


def test_fields_declared_absent_have_no_place_in_the_parsed_item() -> None:
    absent = {F.LOCATION: "country", F.RENEWED_AT: "renewed_at", F.SELLER_ACCOUNT_AGE: "account_created_at"}
    attrs = set(ParsedItem.__dataclass_fields__)
    for fld, attr in absent.items():
        assert fld not in capabilities_for(AcquisitionMode.EXTENSION_ITEM)
        assert attr not in attrs, f"ParsedItem now has {attr!r}: update the {fld} capability"
