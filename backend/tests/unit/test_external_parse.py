"""External search results: prices, dates, sold markers, conditions, sizes, offers (pure)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.external.fx import RATES_DATE, to_eur
from app.external.parse import (
    detect_condition,
    domain,
    extract_size,
    find_sold,
    is_item_url,
    item_price,
    parse_date,
    parse_organic,
    parse_price,
    parse_shopping,
)
from app.external.queries import model_queries, sold_query

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "external"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------- prices
@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("59,99 €", "59.99", "EUR"),
        ("€ 59.99", "59.99", "EUR"),
        ("EUR 45,00", "45.00", "EUR"),
        ("45 euro", "45", "EUR"),
        ("1.234,56 €", "1234.56", "EUR"),
        ("€1,234.56", "1234.56", "EUR"),
        ("£40", "40", "GBP"),
        ("£124.99", "124.99", "GBP"),
        ("$45", "45", "USD"),
        ("US $45.00", "45.00", "USD"),
        ("CHF 80.-", "80", "CHF"),
        ("120 zł", "120", "PLN"),
        ("65,00 € usato", "65.00", "EUR"),
        ("A$199.00", "199.00", None),  # Australian dollars: not in the rate table
        ("450 kr", "450", None),  # SEK/DKK/NOK: ambiguous
    ],
)
def test_prices_in_every_format(text: str, amount: str, currency: str | None) -> None:
    hit = parse_price(text)
    assert hit is not None
    assert hit.amount == D(amount)
    assert hit.currency == currency


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Nike Air Max 90 usate. EUR 70,00 + EUR 6,90 di spedizione.", "70.00"),
        ("Spedizione EUR 6,90 - Prezzo EUR 45,00", "45.00"),
        ("Pre-owned · Nike · Size 10 · $85.00 + $15.00 shipping", "85.00"),
        ("Prezzo di listino EUR 150,00, ora EUR 99,00", "99.00"),
        ("Trova Nike Air Max 90 su eBay. Da EUR 45,00", None),  # a "from" price: a listing page
        ("Nessun prezzo qui", None),
    ],
)
def test_item_price_skips_shipping_list_and_from_prices(text: str, expected: str | None) -> None:
    hit = item_price(text)
    assert (hit.amount if hit else None) == (D(expected) if expected else None)


def test_fx_conversion_records_rate_and_date() -> None:
    gbp = to_eur(D("124.99"), "GBP")
    assert gbp is not None and gbp.price_eur == D("147.66")
    assert gbp.record() == {"currency": "GBP", "per_eur": "0.84645", "date": RATES_DATE}
    assert to_eur(D("45"), "eur").price_eur == D("45.00")  # type: ignore[union-attr]
    assert to_eur(D("110"), "USD").price_eur == D("98.42")  # type: ignore[union-attr]
    assert to_eur(D("10"), "AUD") is None
    assert to_eur(D("10"), None) is None


# ---------------------------------------------------------------------------- dates
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12 set 2026", datetime(2026, 9, 12, 12, tzinfo=UTC)),
        ("12 settembre 2026", datetime(2026, 9, 12, 12, tzinfo=UTC)),
        ("Sep 12, 2026", datetime(2026, 9, 12, 12, tzinfo=UTC)),
        ("12 Sep 2026", datetime(2026, 9, 12, 12, tzinfo=UTC)),
        ("12 sept. 2026", datetime(2026, 9, 12, 12, tzinfo=UTC)),
        ("12. Sept. 2026", datetime(2026, 9, 12, 12, tzinfo=UTC)),
        ("12 de septiembre de 2026", datetime(2026, 9, 12, 12, tzinfo=UTC)),
        ("3 août 2026", datetime(2026, 8, 3, 12, tzinfo=UTC)),
        ("3. März 2026", datetime(2026, 3, 3, 12, tzinfo=UTC)),
        ("2026-09-12", datetime(2026, 9, 12, 12, tzinfo=UTC)),
        ("12/09/2026", datetime(2026, 9, 12, 12, tzinfo=UTC)),
        ("3 days ago", NOW - timedelta(days=3)),
        ("3 giorni fa", NOW - timedelta(days=3)),
        ("2 settimane fa", NOW - timedelta(weeks=2)),
        ("un mese fa", NOW - timedelta(days=30)),
        ("il y a 3 jours", NOW - timedelta(days=3)),
        ("vor 3 Tagen", NOW - timedelta(days=3)),
        ("hace 3 días", NOW - timedelta(days=3)),
        ("5 hours ago", NOW - timedelta(hours=5)),
        ("ieri", NOW - timedelta(days=1)),
    ],
)
def test_dates_in_five_languages(text: str, expected: datetime) -> None:
    assert parse_date(text, NOW) == expected


def test_dates_without_year_or_in_the_future() -> None:
    assert parse_date("12 dic 2026", NOW) is None  # in the future
    assert parse_date("Set di 2 paia", NOW) is None  # "set" without a day/year is not a date
    assert parse_date("12 set", NOW) is None  # no year: only next to a sold marker
    assert parse_date("12 set", NOW, allow_no_year=True) == datetime(2026, 9, 12, 12, tzinfo=UTC)
    # Without a year and later than today: last year.
    assert parse_date("20 dic", NOW, allow_no_year=True) == datetime(2025, 12, 20, 12, tzinfo=UTC)


# ---------------------------------------------------------------------------- sold markers
@pytest.mark.parametrize(
    ("text", "date"),
    [
        ("Venduto il 12 set 2026 · EUR 62,00", datetime(2026, 9, 12, 12, tzinfo=UTC)),
        ("Venduti il 12 set", datetime(2026, 9, 12, 12, tzinfo=UTC)),
        ("Sold Sep 20, 2026 · $110.00", datetime(2026, 9, 20, 12, tzinfo=UTC)),
        ("Sold 14 Sep 2026 · £48.00", datetime(2026, 9, 14, 12, tzinfo=UTC)),
        ("Vendu le 3 sept. 2026", datetime(2026, 9, 3, 12, tzinfo=UTC)),
        ("Verkauft am 3. Sep. 2026", datetime(2026, 9, 3, 12, tzinfo=UTC)),
        ("Vendido el 5 ago 2026", datetime(2026, 8, 5, 12, tzinfo=UTC)),
        ("Venduto · Sneakers Nike Air Max 90", None),
        ("Sold for $120", None),
        ("Questo oggetto è stato venduto.", None),
    ],
)
def test_sold_markers(text: str, date: datetime | None) -> None:
    marker = find_sold(text, NOW)
    assert marker.sold
    assert marker.date == date


@pytest.mark.parametrize(
    "text",
    [
        "Venduto da: sneakers_shop · EUR 79,00",
        "Sold by Foot Locker",
        "Sold out",
        "Più di 10 venduti",
        "20 venduti",
        "20+ sold",
        "I più venduti",
        "Venduto con scatola originale",
        "Sold as is",
        "Nike Air Max 90 nuove",
    ],
)
def test_not_sold(text: str) -> None:
    assert not find_sold(text, NOW).sold


# ---------------------------------------------------------------------------- condition, size
@pytest.mark.parametrize(
    ("text", "condition"),
    [
        ("Nuovo con cartellino", "new_with_tags"),
        ("mai indossate", "new_without_tags"),
        ("Nike Air Max 90 nuove", "new_without_tags"),
        ("Ottime condizioni", "very_good"),
        ("come nuove", "very_good"),
        ("good condition", "good"),
        ("Usato: articolo usato", "used"),
        ("Pre-owned", "used"),
        ("New Balance 550 usate", "used"),  # "new" of New Balance is not a condition
        ("prodotti nuovi e usati", None),  # both: says nothing about this item
        ("", None),
    ],
)
def test_condition_markers(text: str, condition: str | None) -> None:
    assert detect_condition(text) == condition


def test_sizes_only_when_named() -> None:
    assert extract_size("Nike Air Max 90 taglia 42", "sneakers") == "EU42"
    assert extract_size("size 42 EU, ottime condizioni", "sneakers") == "EU42"
    assert extract_size("Infrared UK 9", "sneakers") == "EU43"
    assert extract_size("Felpa tg. M", "hoodies") == "M"
    assert extract_size("Nike Air Max 90", "sneakers") is None


def test_domains_and_item_pages() -> None:
    assert domain("https://www.ebay.it/itm/1") == "ebay.it"
    assert domain("https://it.vestiairecollective.com/x-123456.shtml") == "vestiairecollective.com"
    assert domain("https://www.jdsports.co.uk/product/x/1/") == "jdsports.co.uk"
    assert is_item_url("https://www.ebay.it/itm/205613245678")
    assert not is_item_url("https://www.ebay.it/b/Nike-Air-Max-90/15709/bn_7116479085")
    assert is_item_url("https://www.subito.it/abbigliamento-accessori/nike-air-max-90-512345678.htm")
    assert not is_item_url("https://www.subito.it/annunci-italia/vendita/usato/?q=air+max+90")
    assert is_item_url("https://www.grailed.com/listings/59876543-nike-air-max-90-bacon")
    assert is_item_url("https://www.depop.com/products/jamesuk-nike-air-max-90-infrared/")
    assert is_item_url("https://it.wallapop.com/item/nike-air-max-90-987654321")


# ---------------------------------------------------------------------------- offers
def test_shopping_offers_from_the_fixture() -> None:
    offers = parse_shopping(fixture("shopping_nike_air_max_90.json"), "Nike Air Max 90")
    assert len(offers) == 14
    by_source = {o.source: o for o in offers}
    nike = by_source["nike.com"]
    assert (nike.price, nike.currency, nike.second_hand, nike.price_from) == (
        D("149.99"),
        "EUR",
        False,
        "field",
    )
    jd = by_source["jdsports.co.uk"]
    assert (jd.price, jd.currency) == (D("124.99"), "GBP")
    assert by_source["vinted.it"].excluded
    assert by_source["grailed.com"].second_hand and by_source["grailed.com"].currency == "USD"
    assert by_source["stockx.com"].resale
    ebay = next(o for o in offers if o.url.endswith("196512340011"))
    assert ebay.second_hand and ebay.condition == "used"  # "65,00 € usato"
    assert next(o for o in offers if o.source == "footpatrol.com.au").currency is None


def test_organic_offers_from_the_fixtures() -> None:
    used = parse_organic(fixture("search_nike_air_max_90_used.json"), "q", "used", NOW)
    assert len(used) == 13
    first = used[0]
    assert (first.price, first.price_from, first.condition) == (D("70.00"), "snippet", "very_good")
    assert first.source_date == NOW - timedelta(days=3)  # "3 giorni fa"
    assert used[1].price_from == "attributes"
    assert not used[2].is_item  # eBay category page
    grailed = used[5]
    assert grailed.sold and grailed.source_date == datetime(2026, 8, 28, 12, tzinfo=UTC)
    sold = parse_organic(fixture("search_nike_air_max_90_sold.json"), "q", "sold", NOW)
    flags = [(o.sold, o.source_date.date().isoformat() if o.source_date else None) for o in sold]
    assert flags[0] == (True, "2026-09-12")
    assert flags[1] == (True, "2026-09-20")
    assert flags[2] == (True, "2026-09-01")  # "Venduto ·" with the result date
    assert flags[5] == (False, None)  # "Venduto da: ..." is the seller
    assert flags[8] == (False, None)  # "Più di 10 venduti"
    assert parse_organic(fixture("empty.json"), "q", "used", NOW) == []
    assert parse_shopping(fixture("empty.json"), "q") == []


def test_queries_never_include_vinted() -> None:
    shopping, used = model_queries("Nike", "Air Max 90")
    assert (shopping.endpoint, shopping.q) == ("shopping", "Nike Air Max 90")
    assert used.endpoint == "search" and used.q.startswith("Nike Air Max 90 usato prezzo (site:ebay.it OR ")
    for site in (
        "ebay.com",
        "vestiairecollective.com",
        "depop.com",
        "grailed.com",
        "subito.it",
        "wallapop.com",
    ):
        assert f"site:{site}" in used.q
    sold = sold_query("Nike", "Air Max 90")
    assert sold.purpose == "sold" and "venduto OR sold" in sold.q
    assert all("vinted" not in q.q for q in (shopping, used, sold))
    assert model_queries("Levi's", "Levi's 501")[0].q == "Levi's 501"
