"""PX-09: the market (Vinted domain) a listing was seen on decides which prices are comparable.

An Italian item priced on vinted.fr sales (or a mix of both) used to look like a bargain or got a polluted
range; the domain lives only in the URL, so it is read from there, kept on the profile as ``market`` and used
as a hard filter of the comparables.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.api.v1.listings import manual_to_provider
from app.core.errors import AppError
from app.ingestion.normalizer import market_country, market_currency, market_from_url
from app.marketplace.base import ManualListingInput
from app.opportunities.engine import EconomicTargets, SubjectContext, run_analysis
from app.pricing.comparables import ItemProfile, select_comparables
from app.profit.calculator import CostProfile
from app.scoring.seller import SellerProfile

NOW = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("url", "market"),
    [
        ("https://www.vinted.it/items/123-polo?referrer=catalog", "it"),
        ("https://vinted.fr/items/1", "fr"),
        ("https://www.vinted.de/items/1", "de"),
        ("https://www.vinted.co.uk/items/9", "uk"),
        ("https://www.vinted.com/items/2", "com"),
        ("http://m.vinted.nl:8080/items/3", "nl"),
        ("https://vinted.it.evil.com/items/1", None),
        ("https://evil.com/vinted.it/items/1", None),
        ("https://www.ebay.it/itm/1", None),
        (None, None),
        ("", None),
    ],
)
def test_the_market_is_read_from_the_domain_of_the_page(url: str | None, market: str | None) -> None:
    assert market_from_url(url) == market


def test_currency_and_country_follow_the_market() -> None:
    assert [market_currency(m) for m in ("it", "fr", "de", "pl", "uk", "cz")] == [
        "EUR", "EUR", "EUR", "PLN", "GBP", "CZK",
    ]  # fmt: skip
    assert [market_country(m) for m in ("it", "fr", "uk", "com", None)] == ["IT", "FR", "GB", "US", None]


def listing(url: str, **kw: object) -> ManualListingInput:
    return ManualListingInput(url=url, title="Polo Ralph Lauren blu M", price=D("20"), **kw)  # type: ignore[arg-type]


def test_a_capture_carries_the_country_of_its_market_and_a_foreign_currency_is_refused() -> None:
    pl = manual_to_provider(listing("https://www.vinted.fr/items/1234567-polo"))
    assert pl.country == "FR" and pl.currency == "EUR"
    # An explicit country still wins.
    assert manual_to_provider(listing("https://www.vinted.fr/items/1234567-polo", country="BE")).country == "BE"
    with pytest.raises(AppError) as excinfo:
        manual_to_provider(listing("https://www.vinted.pl/items/1234567-polo"))
    assert excinfo.value.code == "unsupported_currency"


def test_the_server_parser_sets_the_country_of_the_page() -> None:
    from pathlib import Path

    from app.acquisition.vinted_parser import parse_item_html

    html = (Path(__file__).resolve().parents[1] / "fixtures/vinted/real/item_active_plugins.html").read_text()
    item = parse_item_html(html, "https://www.vinted.it/items/9000000101-x", now=NOW)
    assert item.to_provider_listing().country == "IT"


def sold(price: float, market: str | None, i: int) -> ItemProfile:
    return ItemProfile(
        id=uuid.uuid4(),
        title="Polo Ralph Lauren blu M",
        price=D(str(price)),
        brand="ralph-lauren",
        category="polo-shirts",
        parent_category="tops",
        model=None,
        condition="very_good",
        size="M",
        status="sold",
        published_at=NOW - timedelta(days=i + 6),
        sold_at=NOW - timedelta(days=i + 2),
        last_seen_at=NOW - timedelta(days=i + 2),
        market=market,
    )


def subject_profile(price: float = 20) -> ItemProfile:
    return ItemProfile(
        id=None,
        title="Polo Ralph Lauren blu M",
        price=D(str(price)),
        brand="ralph-lauren",
        category="polo-shirts",
        parent_category="tops",
        model=None,
        condition="very_good",
        size="M",
        market="it",
    )


IT = [26, 27, 28, 28, 29, 28, 30, 31, 27, 28, 29, 28]
FR = [44, 45, 46, 45, 47, 44, 45, 46, 45, 44, 46, 45]


def test_only_the_same_market_is_comparable() -> None:
    mixed = [sold(p, "it", i) for i, p in enumerate(IT)] + [sold(p, "fr", i) for i, p in enumerate(FR)]
    kept = select_comparables(subject_profile(), mixed, NOW)
    assert len(kept) == len(IT) and {c.item.market for c in kept} == {"it"}
    # A row whose market is not known (the user's own records, an external price) is not thrown away.
    unknown = select_comparables(subject_profile(), [sold(30, None, 1)], NOW)
    assert len(unknown) == 1


def analysis(candidates: list[ItemProfile]):
    subject = SubjectContext(
        profile=subject_profile(20),
        description_length=60,
        photo_count=6,
        favourite_count=3,
        listing_age_hours=2,
        shipping_fee=D("2.99"),
        brand_name="Ralph Lauren",
        brand_counterfeit_risk=0.12,
        category_baseline_days=10,
        identification={},
        identification_confidence=85,
        seller=SellerProfile(D("4.9"), 80),
        description="Polo taglia M, ottime condizioni.",
    )
    return run_analysis(subject, candidates, NOW, CostProfile(), EconomicTargets())


def test_an_italian_item_is_not_priced_on_french_sales() -> None:
    only_fr = analysis([sold(p, "fr", i) for i, p in enumerate(FR)])
    assert only_fr.data_quality == "insufficient" and only_fr.market.fair_market_value is None
    assert only_fr.decision is not None and only_fr.decision.verdict.value not in ("STRONG_BUY", "BUY")


def test_foreign_rows_do_not_move_the_price_of_the_market() -> None:
    it_only = analysis([sold(p, "it", i) for i, p in enumerate(IT)])
    mixed = analysis([sold(p, "it", i) for i, p in enumerate(IT)] + [sold(p, "fr", i) for i, p in enumerate(FR)])
    assert it_only.market.fair_market_value == mixed.market.fair_market_value
    assert it_only.market.optimistic_sale_price == mixed.market.optimistic_sale_price


def test_the_row_of_a_listing_gets_its_market_from_its_url() -> None:
    from app.opportunities.pipeline import profile_from_row

    row = SimpleNamespace(
        id=uuid.uuid4(), title="Polo", price=D("20"), brand_id=None, category_id=None, model_name=None,
        condition="good", size_normalized="M", gender=None, color=None, material=None, country="FR",
        is_vintage=False, status="sold", published_at=None, sold_at=None, last_seen_at=None,
        url="https://www.vinted.fr/items/1-polo", favourite_count=0,
    )  # fmt: skip
    catalog = SimpleNamespace(
        category_slug=lambda _id: None, parent_slug=lambda _slug: None, brand_slug=lambda _id: None
    )
    assert profile_from_row(row, catalog).market == "fr"  # type: ignore[arg-type]
