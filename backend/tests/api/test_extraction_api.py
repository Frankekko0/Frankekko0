"""The extension's capture, end to end: a real vinted.it page read as a user's browser reads it and posted."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select

from app.acquisition.vinted_parser import parse_item_html
from app.db.models import Listing
from tests.api.test_api import API
from tests.api.test_extension_api import _paired

REAL = Path(__file__).resolve().parents[1] / "fixtures" / "vinted" / "real"


def payload_of(page: str, url: str) -> dict:
    """What the extension sends for a page: the label as printed, the path with its catalogue ids."""
    it = parse_item_html((REAL / page).read_text(encoding="utf-8"), url, now=datetime(2026, 10, 7, tzinfo=UTC))
    return {
        "url": it.url,
        "title": it.title,
        "price": float(it.price or 0),
        "brand": it.brand,
        "size": it.size,
        "condition": it.condition_label,
        "color": it.color,
        "description": it.description,
        "category_path": " > ".join(it.category_path),
        "category_codes": it.category_codes,
        "image_urls": it.images[:2],
    }


async def test_a_real_page_is_stored_with_its_condition_category_audience_and_market(
    auth_client: httpx.AsyncClient, session
) -> None:
    headers = await _paired(auth_client)
    item = payload_of("item_sold_plugins.html", "https://www.vinted.it/items/9000000102-polo-t-shirt")
    assert item["condition"] == "Nuovo senza cartellino" and "5492-polo-shirts" in item["category_codes"]
    r = await auth_client.post(f"{API}/capture/item", json={"item": item}, headers=headers)
    assert r.status_code == 200, r.text
    active = payload_of("item_active_plugins.html", "https://www.vinted.it/items/9000000101-levis")
    assert active["condition"] == "Ottime"
    r = await auth_client.post(f"{API}/capture/item", json={"item": active}, headers=headers)
    assert r.status_code == 200, r.text

    rows = {x.external_id: x for x in (await session.execute(select(Listing))).scalars()}
    polo, jeans = rows["9000000102"], rows["9000000101"]
    assert (jeans.condition, jeans.condition_raw) == ("very_good", "Ottime")
    assert polo.condition == "new_without_tags"
    # The marketplace's own path decides the category (a "Polo" under "Camicie e t-shirt"), the audience comes with it.
    assert polo.identification["category"]["value"] == "polo-shirts"
    assert polo.identification["category"]["certainty"] == "certain" and polo.gender == "men"
    assert jeans.identification["category"]["value"] == "jeans" and jeans.country == "IT"
    # The description of the real Levi's declares a repaired crotch and an open hem: seen by the identification.
    assert {"rammendi", "cuciture aperte"} <= set(jeans.identification["defect_terms"])
    # Stored with condition known: it does not appear as a missing field of the analysis.
    detail = (await auth_client.get(f"{API}/items/9000000101")).json()
    assert detail["item"]["condition"] == "very_good"


async def test_a_market_with_another_currency_is_refused_not_stored_as_euros(
    auth_client: httpx.AsyncClient, session
) -> None:
    headers = await _paired(auth_client)
    item = {"url": "https://www.vinted.pl/items/9300-kurtka", "title": "Kurtka Zara czarna", "price": 80}
    r = await auth_client.post(f"{API}/capture/item", json={"item": item}, headers=headers)
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_currency"
    cards = await auth_client.post(
        f"{API}/capture/cards",
        json={"page_type": "catalog", "items": [item], "extension_version": "1.0.0"},
        headers=headers,
    )
    assert cards.status_code == 200 and cards.json()["stored"] == 0
    assert (await session.execute(select(Listing))).scalars().all() == []
