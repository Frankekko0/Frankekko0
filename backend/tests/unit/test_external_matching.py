"""External search: strict model matching with counted reasons, classification of the kept
prices, the query budget and the Serper client (fake transport, never the network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.external.matching import match_offer, model_spec
from app.external.parse import Offer, parse_organic, parse_shopping
from app.external.provider import ProviderError, QueryBudget, SerperProvider, charged_search
from app.external.service import ModelSearch, classify, evaluate
from app.identification.taxonomy import DEFAULT_TAXONOMY

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "external"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
KEY = "sk-test-0123456789abcdef"
AIR_MAX_90 = model_spec(DEFAULT_TAXONOMY, "nike", "Air Max 90")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------- matching
def test_model_spec_from_the_taxonomy() -> None:
    spec = AIR_MAX_90
    assert spec is not None
    assert spec.category_slug == "sneakers" and spec.footwear
    assert set(spec.keywords) == {"air max 90", "am90"}
    assert "nike" in spec.aliases
    others = dict(spec.others)
    assert "air max 95" in others["Air Max 95"] and "Air Max 90" not in others
    assert "adidas" in spec.other_brands and "nike" not in spec.other_brands
    assert model_spec(DEFAULT_TAXONOMY, "unknown-brand", "X") is None
    # A model outside the taxonomy lines is matched by its own name.
    custom = model_spec(DEFAULT_TAXONOMY, "nike", "Air Max Plus")
    assert custom is not None and custom.keywords == ("air max plus",)


@pytest.mark.parametrize(
    "title",
    [
        "Nike Air Max 90 Essential bianche 43",
        "Scarpa Nike Air Max 90 - Uomo",
        "Nike Sportswear AIR MAX 90 - Sneakers basse",
        "Nike AM90 Infrared",
        "Air Max 90 nere 42",  # brand implied by a distinctive model name
        "Nike Air Max 90 originali, non replica",
        "Nike Air Max 90 con lacci extra e scatola",
        "Nike Air Max 90 baby blue 42",
        "Nike Air Max 90 2020 retro",
        "Nike Air Max 90 Men's Size 10",
    ],
)
def test_accepted(title: str) -> None:
    m = match_offer(AIR_MAX_90, title)  # type: ignore[arg-type]
    assert m.ok, (m.reason, m.detail)
    assert 0.5 <= m.score <= 1.0


@pytest.mark.parametrize(
    ("title", "snippet", "reason"),
    [
        ("Adidas Samba OG", "", "brand"),
        ("Nike Air Force 1 '07", "", "other_model"),
        ("Nike Air Max 95 Essential", "", "other_model"),
        ("Nike Air Max 90 / Air Max 1 bundle", "", "other_model"),
        ("Nike Air Max 270 React", "", "other_model"),
        ("Nike Revolution 6", "", "model"),
        ("Nike Air Max 90 Off-White Desert Ore", "", "other_model"),
        ("Nike x Stussy Air Max 90", "", "other_model"),
        ("Nike Air Max 90 replica 1:1", "", "replica"),
        ("Nike Air Max 90 AAA quality", "", "replica"),
        ("Scarpe stile Nike Air Max 90", "", "replica"),
        ("Sneakers tipo Air Max 90", "", "replica"),
        ("Nike Air Max 90 ispirate", "", "replica"),
        ("Nike Air Max 90", "Non originale, imitazione perfetta", "replica"),
        ("Nike Air Max 90 GS", "", "kids"),
        ("Nike Air Max 90 bambino", "", "kids"),
        ("Nike Air Max 90 Junior", "", "kids"),
        ("Nike Air Max 90 Toddler 7C", "", "kids"),
        ("Nike Air Max 90 taglia 33", "", "kids"),
        ("Nike Air Max 90 EU 28.5", "", "kids"),
        ("Nike Air Max 90 5.5Y", "", "kids"),
        ("Nike Air Max 90 6 anni", "", "kids"),
        ("Nike Air Max 90", "Per bimbo, numero 30", "kids"),
        ("Lotto 3 paia Nike Air Max 90", "", "lot"),
        ("Set di 2 paia Nike Air Max 90", "", "lot"),
        ("Nike Air Max 90 x2", "", "lot"),
        ("Nike Air Max 90 stock 10 pezzi", "", "lot"),
        ("Nike Air Max 90 bundle", "", "lot"),
        ("Lacci di ricambio per Nike Air Max 90", "", "accessory"),
        ("Solo scatola Nike Air Max 90", "", "accessory"),
        ("Solette Nike Air Max 90", "", "accessory"),
    ],
)
def test_rejected_with_reason(title: str, snippet: str, reason: str) -> None:
    m = match_offer(AIR_MAX_90, title, snippet)  # type: ignore[arg-type]
    assert not m.ok
    assert m.reason == reason, m.detail


def test_clothing_kids_sizes_and_kits() -> None:
    hoodie = model_spec(DEFAULT_TAXONOMY, "supreme", "Box Logo")
    assert hoodie is not None
    assert match_offer(hoodie, "Supreme Box Logo hoodie taglia 12").reason == "kids"
    # Moncler-style numeric sizes 0-7 are adult sizes.
    maya = model_spec(DEFAULT_TAXONOMY, "moncler", "Maya")
    assert maya is not None
    assert match_offer(maya, "Moncler Maya piumino taglia 3").ok
    assert match_offer(maya, "Moncler Maya kit").reason == "lot"


def test_number_models_and_years() -> None:
    levis = model_spec(DEFAULT_TAXONOMY, "levis", "501")
    assert levis is not None
    assert match_offer(levis, "Levi's 501 W32 L34 blu").ok
    assert match_offer(levis, "Levi's 505 W32").reason == "other_model"
    nuptse = model_spec(DEFAULT_TAXONOMY, "the-north-face", "Nuptse 700")
    assert nuptse is not None
    # 1996 is the year of the Retro Nuptse, not another model number.
    assert match_offer(nuptse, "The North Face 1996 Retro Nuptse 700 nero").ok
    assert match_offer(nuptse, "The North Face Himalayan").reason == "other_model"


def test_fixture_results_kept_and_rejected_per_reason() -> None:
    spec = AIR_MAX_90
    assert spec is not None
    offers = parse_shopping(fixture("shopping_nike_air_max_90.json"), "q")
    offers += parse_organic(fixture("search_nike_air_max_90_used.json"), "q", "used", NOW)
    offers += parse_organic(fixture("search_nike_air_max_90_sold.json"), "q", "sold", NOW)
    search = ModelSearch(spec)
    evaluate(spec, offers, search)
    assert search.results == 36
    assert search.kept_counts() == {"new": 4, "asking": 11, "sold": 6}
    assert search.rejected_counts() == {
        "source": 1,
        "kids": 3,
        "other_model": 4,
        "replica": 2,
        "accessory": 1,
        "currency": 1,
        "lot": 2,
        "not_item": 1,
    }
    for c in search.kept:
        assert c.offer.source and c.currency and c.kind in ("new", "asking", "sold")
        assert c.price_eur > 0 and c.match["fx"]["date"]
        assert "vinted" not in c.offer.url
    sold = {c.offer.url: c for c in search.kept if c.kind == "sold"}
    uk = sold["https://www.ebay.co.uk/itm/335512345678"]
    assert (uk.currency, str(uk.price), str(uk.price_eur)) == ("GBP", "48.00", "56.71")
    assert uk.offer.source_date == datetime(2026, 9, 14, 12, tzinfo=UTC)
    # Evaluating the same results again: duplicates, not new candidates.
    evaluate(spec, offers[:5], search)
    assert search.duplicates == 5 and len(search.kept) == 21


def _offer(**kw: object) -> Offer:
    base = {
        "endpoint": "shopping",
        "purpose": "shopping",
        "query": "q",
        "position": 1,
        "title": "t",
        "url": "u",
        "source": "s",
    }
    return Offer(**{**base, **kw})  # type: ignore[arg-type]


def test_classification_of_kind_and_condition() -> None:
    assert classify(_offer(sold=True)) == ("sold", "used")
    assert classify(_offer(sold=True, condition="very_good")) == ("sold", "very_good")
    assert classify(_offer(second_hand=True)) == ("asking", "used")
    assert classify(_offer(second_hand=True, condition="new_without_tags")) == ("asking", "new_without_tags")
    assert classify(_offer(resale=True)) == ("asking", "new_with_tags")
    assert classify(_offer()) == ("new", "new_with_tags")  # a retailer
    assert classify(_offer(condition="used")) == ("asking", "used")  # a retailer selling used


# ---------------------------------------------------------------------------- budget
def test_budget_caps_per_day_and_month() -> None:
    budget = QueryBudget(daily_max=3, monthly_budget=5, now=NOW)
    assert budget.remaining() == 3
    assert budget.reserve(2) and budget.today_used == 2
    assert not budget.reserve(2)  # would exceed the daily cap: nothing charged
    assert budget.today_used == 2 and budget.month_used == 2
    assert budget.reserve(1) and not budget.allows(1)
    # Next day: the daily count restarts, the monthly one goes on.
    tomorrow = QueryBudget(daily_max=3, monthly_budget=5, now=NOW + timedelta(days=1), state=budget.state)
    assert tomorrow.today_used == 0 and tomorrow.month_used == 3
    assert tomorrow.remaining() == 2
    tomorrow.charge(2)
    assert not tomorrow.allows(1)
    # Next month: both restart; the total keeps counting.
    later = QueryBudget(
        daily_max=3, monthly_budget=5, now=datetime(2026, 11, 1, tzinfo=UTC), state=tomorrow.state
    )
    assert (later.today_used, later.month_used, later.state["total_used"]) == (0, 0, 5)


# ---------------------------------------------------------------------------- provider
def _serper(handler: object) -> SerperProvider:
    return SerperProvider(KEY, transport=httpx.MockTransport(handler), retry_delay=0)  # type: ignore[arg-type]


async def test_serper_request_format() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=fixture("shopping_nike_air_max_90.json"))

    budget = QueryBudget(daily_max=10, monthly_budget=10, now=NOW)
    assert budget.reserve(1)
    response = await charged_search(_serper(handler), budget, "shopping", "Nike Air Max 90")
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://google.serper.dev/shopping"
    assert req.headers["X-API-KEY"] == KEY
    assert json.loads(req.content) == {"q": "Nike Air Max 90", "gl": "it", "hl": "it", "num": 10}
    assert response.credits == 1 and len(response.payload["shopping"]) == 14
    assert budget.today_used == 1
    assert KEY not in repr(_serper(handler))


async def test_serper_extra_credits_are_charged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"organic": [], "credits": 2})

    budget = QueryBudget(daily_max=10, monthly_budget=10, now=NOW)
    budget.reserve(1)
    await charged_search(_serper(handler), budget, "search", "q")
    assert budget.today_used == 2


async def test_serper_retries_once_on_server_errors() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json=fixture("errors.json")["500"])
        return httpx.Response(200, json=fixture("empty.json"))

    response = await _serper(flaky).search("search", "q")
    assert calls["n"] == 2 and response.payload["organic"] == []

    def down(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    calls["n"] = 0
    with pytest.raises(ProviderError) as exc:
        await _serper(down).search("search", "q")
    assert calls["n"] == 2 and not exc.value.fatal and exc.value.status == 503

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(ProviderError) as exc:
        await _serper(broken).search("search", "q")
    assert not exc.value.fatal and "ConnectTimeout" in exc.value.message


@pytest.mark.parametrize(
    ("status", "fatal", "words"),
    [
        (401, True, "Chiave Serper rifiutata"),
        (403, True, "Chiave Serper rifiutata"),
        (402, True, "Crediti Serper esauriti"),
        (429, True, "Limite di richieste"),
        (400, False, "Richiesta rifiutata"),
    ],
)
async def test_serper_client_errors(status: int, fatal: bool, words: str) -> None:
    body = fixture("errors.json").get(str(status), {"message": "Bad request"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    with pytest.raises(ProviderError) as exc:
        await _serper(handler).search("search", "q")
    assert exc.value.fatal is fatal and exc.value.status == status
    assert words in exc.value.message and KEY not in exc.value.message


async def test_serper_out_of_credits_reported_as_400() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Not enough credits", "statusCode": 400})

    with pytest.raises(ProviderError) as exc:
        await _serper(handler).search("search", "q")
    assert exc.value.fatal and "Crediti" in exc.value.message


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("Scarpa Nike Air Max 90 - Ragazzi", "kids"),
        ("Nike Air Max 90 LTR Scarpa - Ragazzo/a", "kids"),
        ("Nike Air Max 90/1 bianche", "other_model"),
        ("Nike Air Max 90 tre paia", "lot"),
    ],
)
def test_italian_retail_labels_and_hybrids_are_not_the_adult_model(title: str, reason: str) -> None:
    m = match_offer(AIR_MAX_90, title)
    assert not m.ok and m.reason == reason


def test_a_size_after_the_model_number_is_still_the_model() -> None:
    assert match_offer(AIR_MAX_90, "Nike Air Max 90 42 bianche usate").ok
