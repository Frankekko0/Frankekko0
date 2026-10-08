"""Real vinted.it item pages (trimmed, anonymised), shared with the browser extension's tests."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.acquisition.vinted_parser import parse_item_html, seller_key
from app.domain.enums import ListingStatus

REAL = Path(__file__).resolve().parents[1] / "fixtures" / "vinted" / "real"
EXPECTED = json.loads((REAL / "expected.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 7, 23, 0, tzinfo=UTC)
PAGES = [k for k in EXPECTED if not k.startswith("_")]
ACTIVE_URL = EXPECTED["item_active_plugins.html"]["url"]
SOLD_URL = EXPECTED["item_sold_plugins.html"]["url"]


def _page(name: str) -> str:
    return (REAL / name).read_text(encoding="utf-8")


def _swap(html: str, old: str, new: str, count: int | None = None) -> str:
    """Edits a fixture, failing loudly when the text to change isn't there."""
    assert old in html, old
    if count is not None:
        assert html.count(old) == count, (old, html.count(old))
    return html.replace(old, new)


def test_the_real_pages_are_all_checked() -> None:
    assert sorted(PAGES) == sorted(p.name for p in REAL.glob("*.html"))


@pytest.mark.parametrize("name", PAGES)
def test_real_item_pages_are_read_like_the_extension(name: str) -> None:
    want = EXPECTED[name]
    item = parse_item_html(_page(name), want["url"] + "?referrer=catalog", now=NOW)
    got = {
        "url": item.url,
        "title": item.title,
        "price": str(item.price),
        "currency": item.currency,
        "brand": item.brand,
        "size": item.size,
        "condition": item.condition,
        "color": item.color,
        "status": item.status,
        "status_source": item.status_source,
        "images": len(item.images),
        "favourite_count": item.favourite_count,
        "view_count": item.view_count,
        "buyer_protection_fee": None if item.buyer_protection_fee is None else str(item.buyer_protection_fee),
        "seller_member": want["seller_member"],
        "seller_rating": None if item.seller_rating is None else str(item.seller_rating),
        "seller_review_count": item.seller_review_count,
        "can_buy": item.can_buy,
    }
    assert got == want
    # The seller is only an opaque key, made from the item's own seller id.
    assert item.seller_key == seller_key(want["seller_member"])
    assert item.complete


def test_a_sold_page_is_sold_by_its_status_banner_not_by_can_buy() -> None:
    sold = parse_item_html(_page("item_sold_plugins.html"), SOLD_URL, now=NOW)
    assert (sold.status, sold.status_source, sold.can_buy) == (ListingStatus.SOLD, "embedded", False)
    assert sold.to_provider_listing().status == ListingStatus.SOLD
    # Same page without the banner ("Venduto") in its data and in the sidebar: can_buy is still
    # false, but that alone is never a sale (also false for your own items, signed out, reserved).
    html = _swap(_page("item_sold_plugins.html"), '\\"title\\":\\"Venduto\\"', '\\"title\\":\\"\\"', 1)
    html = _swap(html, "<div>Venduto</div>", "<div></div>", 1)
    item = parse_item_html(html, SOLD_URL, now=NOW)
    assert item.can_buy is False
    assert (item.status, item.status_source) == (ListingStatus.ACTIVE, "default")


def test_can_buy_false_on_an_active_page_stays_active() -> None:
    html = _swap(_page("item_active_plugins.html"), '\\"can_buy\\":true', '\\"can_buy\\":false')
    item = parse_item_html(html, ACTIVE_URL, now=NOW)
    assert (item.can_buy, item.status, item.status_source) == (False, ListingStatus.ACTIVE, "jsonld")


def test_a_reserved_item_is_read_from_its_data() -> None:
    html = _swap(_page("item_active_plugins.html"), '\\"is_reserved\\":false', '\\"is_reserved\\":true')
    item = parse_item_html(html, ACTIVE_URL, now=NOW)
    assert (item.status, item.status_source) == (ListingStatus.RESERVED, "embedded")


def test_sections_of_other_items_are_never_read() -> None:
    # The seller header and the favourites section of another item (e.g. a suggested one).
    html = _page("item_active_plugins.html")
    html = _swap(
        html,
        '\\"follow_action_visible\\":false,\\"is_favourite\\":false,\\"item_id\\":\\"9000000101\\"',
        '\\"follow_action_visible\\":false,\\"is_favourite\\":false,\\"item_id\\":\\"9000000999\\"',
        1,
    )
    html = _swap(
        html,
        '\\"favourite_count\\":78,\\"is_favourite\\":false,\\"item_id\\":\\"9000000101\\"',
        '\\"favourite_count\\":999,\\"is_favourite\\":false,\\"item_id\\":\\"9000000999\\"',
        1,
    )
    item = parse_item_html(html, ACTIVE_URL, now=NOW)
    assert item.seller_rating is None and item.seller_review_count is None
    assert item.seller_key == seller_key("1000003")  # still the item's own seller id
    assert item.favourite_count == 78  # the item's own favourite button, never 999
    # A status banner of another item never makes this one sold.
    sold = _swap(_page("item_sold_plugins.html"), '\\"item_id\\":\\"9000000102\\",\\"seller_id\\":\\"1000001\\",\\"theme\\"', '\\"item_id\\":\\"9000000999\\",\\"seller_id\\":\\"1000001\\",\\"theme\\"', 1)
    sold = _swap(sold, "<div>Venduto</div>", "<div></div>", 1)
    assert parse_item_html(sold, SOLD_URL, now=NOW).status == ListingStatus.ACTIVE


def test_only_the_items_own_badge_says_sold() -> None:
    page = _swap(_page("item_sold_no_scripts.html"), "<div>Venduto</div>", "<div></div>", 1)
    assert parse_item_html(page, SOLD_URL, now=NOW).status == ListingStatus.ACTIVE
    # "Venduto" on another item's card (even right above the summary) or in a title is no sale.
    card = '<div data-testid="product-item-id-9000000555"><div>Venduto</div></div>'
    with_card = _swap(page, '<div data-testid="item-page-summary-plugin">', card + '<div data-testid="item-page-summary-plugin">', 1)
    assert parse_item_html(with_card, SOLD_URL, now=NOW).status == ListingStatus.ACTIVE
    titled = page.replace("Polo t shirt", "Sold out polo - venduto in negozio")
    assert parse_item_html(titled, SOLD_URL, now=NOW).status == ListingStatus.ACTIVE
    # The real badge, in the item's sidebar, does.
    item = parse_item_html(_page("item_sold_no_scripts.html"), SOLD_URL, now=NOW)
    assert (item.status, item.status_source) == (ListingStatus.SOLD, "text")
