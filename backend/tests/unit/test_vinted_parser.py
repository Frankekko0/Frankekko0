"""Vinted page and email parsing (fixtures shared with the browser extension's tests)."""

import email
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email import policy
from pathlib import Path

import pytest

from app.acquisition.vinted_parser import (
    classify_email,
    find_price,
    items_in_email,
    load_config,
    parse_item_html,
    parse_price,
    relative_time,
    seller_key,
)
from app.domain.enums import ListingStatus

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "vinted"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def test_item_page_all_fields_match_the_shared_expectation() -> None:
    expected = json.loads((FIX / "item_active.expected.json").read_text())
    item = parse_item_html(
        (FIX / "item_active.html").read_text(), expected["url"] + "?referrer=catalog&time=1", now=NOW
    )
    got = {
        "vinted_id": item.vinted_id,
        "url": item.url,
        "title": item.title,
        "price": str(item.price),
        "currency": item.currency,
        "brand": item.brand,
        "size": item.size,
        "condition": item.condition,
        "condition_label": item.condition_label,
        "color": item.color,
        "material": item.material,
        "category_path": item.category_path,
        "favourite_count": item.favourite_count,
        "view_count": item.view_count,
        "published_at": item.published_at.isoformat(),
        "status": item.status.value,
        "buyer_protection_fee": str(item.buyer_protection_fee),
        "shipping_fee": str(item.shipping_fee),
        "images": item.images,
        "seller_rating": str(item.seller_rating),
        "seller_review_count": item.seller_review_count,
    }
    assert got == {k: v for k, v in expected.items() if k != "seller_key_from_member"}
    # No personal data: the seller is an opaque hash, never the username.
    assert item.seller_key == seller_key(expected["seller_key_from_member"])
    assert "armadio" not in json.dumps(item.__dict__, default=str)
    pl = item.to_provider_listing()
    assert pl.capture_level == "full" and len(pl.images) == 3 and pl.seller.external_id.startswith("h:")


def test_sold_and_removed_pages() -> None:
    sold = parse_item_html((FIX / "item_sold.html").read_text(), "https://www.vinted.fr/items/5555-pull")
    assert sold.status == ListingStatus.SOLD and sold.vinted_id == "5555" and sold.price == Decimal("35.00")
    gone = parse_item_html((FIX / "item_removed.html").read_text(), "https://www.vinted.it/items/777-x")
    assert gone.status == ListingStatus.REMOVED and not gone.complete
    with pytest.raises(ValueError):
        gone.to_provider_listing()


@pytest.mark.parametrize(
    ("text", "price", "currency"),
    [
        ("18,00 €", "18.00", "EUR"),
        ("€1.234,50", "1234.50", "EUR"),
        ("1 234,50 zł", "1234.50", "PLN"),
        ("£12.00", "12.00", "GBP"),
        ("19,60 € include la Protezione acquisti", "19.60", "EUR"),
    ],
)
def test_prices_and_currencies(text: str, price: str, currency: str) -> None:
    assert find_price(text, load_config()) == (Decimal(price), currency)


def test_price_edge_cases() -> None:
    assert parse_price("0") is None and parse_price("abc") is None and parse_price(18) == Decimal("18.00")
    assert find_price("taglia 42,5", load_config()) is None  # a size is not a price


@pytest.mark.parametrize(
    ("text", "delta"),
    [
        ("3 giorni fa", timedelta(days=3)),
        ("2 hours ago", timedelta(hours=2)),
        ("il y a 5 minutes", timedelta(minutes=5)),
        ("vor 2 Wochen", timedelta(weeks=2)),
        ("ieri", timedelta(days=1)),
        ("adesso", timedelta(0)),
        ("1 mese fa", timedelta(days=30)),
    ],
)
def test_relative_upload_dates(text: str, delta: timedelta) -> None:
    assert relative_time(text, NOW, load_config()) == NOW - delta


def _eml(name: str) -> tuple[str, str, str, str]:
    msg = email.message_from_bytes((FIX / name).read_bytes(), policy=policy.default)
    html = msg.get_body(("html",)).get_content()
    text = msg.get_body(("plain",)).get_content()
    return str(msg["From"]), str(msg["Subject"]), html, text


def test_favourite_sold_email() -> None:
    sender, subject, html, text = _eml("email_sold.eml")
    assert classify_email(subject, text) == "sold"
    items = items_in_email(sender, subject, html, text)
    assert [(i.kind, i.vinted_id, i.url) for i in items] == [
        ("sold", "4242424242", "https://www.vinted.it/items/4242424242-felpa-ralph-lauren-blu")
    ]


def test_price_drop_email_carries_the_new_price() -> None:
    sender, subject, html, text = _eml("email_price_drop.eml")
    items = items_in_email(sender, subject, html, text)
    assert items[0].kind == "price_drop" and items[0].price == Decimal("15.00")


def test_emails_from_other_senders_are_ignored() -> None:
    _sender, subject, html, text = _eml("email_sold.eml")
    assert items_in_email("Phisher <x@evil.example>", subject, html, text) == []
