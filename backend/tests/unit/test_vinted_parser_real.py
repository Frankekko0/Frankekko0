"""Real vinted.it item pages (trimmed, anonymised), shared with the browser extension's tests."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.acquisition.vinted_parser import parse_item_html

REAL = Path(__file__).resolve().parents[1] / "fixtures" / "vinted" / "real"
EXPECTED = json.loads((REAL / "expected.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 7, 23, 0, tzinfo=UTC)


@pytest.mark.parametrize("name", ["item_active_plugins.html", "item_active_no_jsonld.html"])
def test_real_item_pages_are_read_like_the_extension(name: str) -> None:
    want = EXPECTED[name]
    item = parse_item_html(
        (REAL / name).read_text(encoding="utf-8"), want["url"] + "?referrer=catalog", now=NOW
    )
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
    }
    assert got == want
