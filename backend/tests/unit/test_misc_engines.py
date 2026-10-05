"""NL search, alert rules, learning engine, mock provider, logging redaction, deal analyst."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.ai.deal_analyst import DealContext, RuleBasedDealAnalyst, ScenarioSummary
from app.ai.nl_search import NaturalLanguageParser
from app.alerts.rules import AlertCandidate, Thresholds, WatchlistRule, decide_alerts, matches_watchlist
from app.analytics.learning import FlipRecord, compute_adjustments
from app.core.logging import REDACTED, redact
from app.domain.enums import AlertType, Verdict
from app.marketplace.base import SearchQuery
from app.marketplace.mock.provider import MockMarketplaceProvider
from app.vision.phash import dhash, hamming

parser = NaturalLanguageParser()


def test_nl_search_spec_example() -> None:
    f = parser.parse("fammi vedere felpe Ralph Lauren sotto 25 euro con almeno 50% ROI").filters
    assert f["brands"] == ["ralph-lauren"]
    assert set(f["categories"]) == {"sweatshirts", "hoodies"}
    assert f["max_price"] == 25
    assert f["min_roi"] == 0.5


def test_nl_search_english_and_sizes() -> None:
    f = parser.parse("Ralph Lauren hoodie M under 20").filters
    assert f == {
        "max_price": 20.0,
        "brands": ["ralph-lauren"],
        "categories": ["hoodies"],
        "sizes": ["M"],
        "sort": "flip",
    }
    g = parser.parse("sneakers nike 42 tra 30 e 60 euro profitto almeno 15 basso rischio").filters
    assert g["sizes"] == ["EU42"] and g["min_price"] == 30 and g["max_price"] == 60
    assert g["min_profit"] == 15 and g["max_risk"] == 25


def cand(**overrides: object) -> AlertCandidate:
    base = dict(
        opportunity_id=uuid.uuid4(),
        listing_id=uuid.uuid4(),
        root_listing_id=uuid.uuid4(),
        title="Felpa Ralph Lauren Half Zip blu",
        brand="ralph-lauren",
        category="sweatshirts",
        parent_category="tops",
        size="M",
        condition="very_good",
        country="IT",
        is_vintage=False,
        price=D("18"),
        previous_price=None,
        expected_profit=D("17"),
        expected_roi=D("0.7"),
        flip_score=88,
        confidence=80,
        risk=15,
        is_ultra=False,
        is_new=True,
    )
    base.update(overrides)
    return AlertCandidate(**base)  # type: ignore[arg-type]


THRESHOLDS = Thresholds(min_flip=85, min_roi=D("0.5"), min_profit=D("15"), min_confidence=70, max_risk=60)


def test_watchlist_matching() -> None:
    rl = WatchlistRule(
        id=uuid.uuid4(),
        name="RL",
        brands=("ralph-lauren",),
        max_buy_price=D("25"),
        min_profit=D("15"),
        min_roi=D("0.5"),
    )
    assert matches_watchlist(cand(), rl)
    assert not matches_watchlist(cand(price=D("30")), rl)
    assert not matches_watchlist(cand(brand="nike"), rl)
    nike = WatchlistRule(id=uuid.uuid4(), name="Nike", brands=("nike",), max_buy_price=D("20"))
    # No quality criteria: default floor (Flip >= 60) avoids noisy alerts.
    assert not matches_watchlist(cand(brand="nike", flip_score=40), nike)
    assert matches_watchlist(cand(brand="nike", flip_score=65), nike)
    tops = WatchlistRule(
        id=uuid.uuid4(), name="Tops", categories=("tops",), query="half zip", vintage_only=False
    )
    assert matches_watchlist(cand(), tops)


def test_alert_decisions_ultra_and_dedupe_keys() -> None:
    c = cand(is_ultra=True, flip_score=95)
    decisions = decide_alerts(c, THRESHOLDS, [])
    assert [d.type for d in decisions] == [AlertType.ULTRA_DEAL]
    assert decisions[0].priority.value == "high"
    assert str(c.root_listing_id) in decisions[0].dedupe_key
    normal = decide_alerts(cand(), THRESHOLDS, [])
    assert [d.type for d in normal] == [AlertType.NEW_OPPORTUNITY]
    assert decide_alerts(cand(flip_score=70), THRESHOLDS, []) == []


def test_price_drop_alert() -> None:
    c = cand(previous_price=D("45"), price=D("27"), is_new=False, flip_score=86)
    types = [d.type for d in decide_alerts(c, THRESHOLDS, [])]
    assert AlertType.PRICE_DROP in types
    assert AlertType.NEW_OPPORTUNITY not in types  # already known listing
    assert decide_alerts(c, THRESHOLDS, [], price_drop_enabled=False) == []


def test_no_opportunity_alerts_for_old_listings_but_price_drops_still_fire() -> None:
    old_ultra = cand(is_ultra=True, flip_score=95, listing_age_hours=24 * 40)
    assert decide_alerts(old_ultra, THRESHOLDS, []) == []  # e.g. the historical backfill
    assert decide_alerts(cand(listing_age_hours=24 * 40), THRESHOLDS, []) == []
    assert [d.type for d in decide_alerts(cand(listing_age_hours=5), THRESHOLDS, [])] == [
        AlertType.NEW_OPPORTUNITY
    ]
    dropped = cand(
        previous_price=D("45"), price=D("27"), is_new=False, flip_score=86, listing_age_hours=24 * 40
    )
    assert [d.type for d in decide_alerts(dropped, THRESHOLDS, [])] == [AlertType.PRICE_DROP]


def flip(brand: str, roi: str, profit: str = "10") -> FlipRecord:
    return FlipRecord(
        brand=brand,
        category="hoodies",
        size="M",
        purchase_price=D("18"),
        roi=D(roi),
        profit=D(profit),
        holding_days=5,
    )


def test_learning_needs_enough_flips_and_is_bounded() -> None:
    few = compute_adjustments([flip("ralph-lauren", "1.2")], {})
    assert few[("brand", "ralph-lauren")]["adjustment"] == 0
    many = [flip("ralph-lauren", "1.5", "20")] * 4 + [flip("nike", "0.1", "1")] * 4
    adj = compute_adjustments(many, {})
    assert adj[("brand", "ralph-lauren")]["adjustment"] > 0 > adj[("brand", "nike")]["adjustment"]
    assert all(-12 <= v["adjustment"] <= 12 for v in adj.values())


def test_ignored_segments_get_mild_penalty_only() -> None:
    adj = compute_adjustments([], {("category", "jeans"): (30, 0)})
    assert -8 <= adj[("category", "jeans")]["adjustment"] < 0


def test_mock_provider_is_deterministic_and_paginates() -> None:
    import asyncio

    epoch = datetime(2026, 9, 1, tzinfo=UTC)
    now = epoch + timedelta(hours=2)
    p1 = MockMarketplaceProvider(seed=7, epoch=epoch, history_days=2, now_fn=lambda: now)
    p2 = MockMarketplaceProvider(seed=7, epoch=epoch, history_days=2, now_fn=lambda: now)

    async def collect(p: MockMarketplaceProvider) -> list[str]:
        out, cursor = [], None
        while True:
            page = await p.search_listings(SearchQuery(cursor=cursor, page_size=100))
            out += [f"{x.external_id}:{x.price}:{x.status}" for x in page.listings]
            if not page.has_more:
                return out
            cursor = page.next_cursor

    a, b = asyncio.run(collect(p1)), asyncio.run(collect(p2))
    assert a == b and len(a) > 300
    assert len(set(a)) == len(a)
    ext = a[0].split(":")[0]
    listing = asyncio.run(p1.get_listing(ext))
    assert listing is not None and listing.external_id == ext
    assert asyncio.run(p1.get_listing("not-a-number")) is None


def test_log_redaction() -> None:
    event = {
        "event": "x",
        "password": "hunter2",
        "nested": {"api_key": "sk-123", "ok": 1},
        "message": "call https://api.telegram.org/bot123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAA/sendMessage",
    }
    out = redact(event)
    assert out["password"] == REDACTED and out["nested"]["api_key"] == REDACTED and out["nested"]["ok"] == 1
    assert "AAAAAAAA" not in out["message"]


def test_dhash_hamming() -> None:
    from PIL import Image

    img = Image.new("RGB", (64, 64), "white")
    for x in range(32):
        for y in range(64):
            img.putpixel((x, y), (0, 0, 0))
    h1 = dhash(img)
    h2 = dhash(img.resize((128, 128)))
    assert hamming(h1, h2) <= 4
    assert hamming(h1, dhash(img.transpose(Image.Transpose.FLIP_LEFT_RIGHT))) > 4


@pytest.mark.parametrize("profit,expected", [(D("20"), Verdict.BUY), (D("-3"), Verdict.SKIP)])
def test_rule_based_analyst(profit: D, expected: Verdict) -> None:
    ctx = DealContext(
        title="Felpa",
        brand="Ralph Lauren",
        condition="very_good",
        listing_price=D("18"),
        total_acquisition_cost=D("23"),
        fair_market_value=D("42"),
        discount_vs_market=0.57,
        scenarios=[
            ScenarioSummary(name="conservative", sale_price=D("35"), net_profit=profit - 5, roi=D("0.5")),
            ScenarioSummary(name="expected", sale_price=D("42"), net_profit=profit, roi=D("0.8")),
            ScenarioSummary(name="optimistic", sale_price=D("48"), net_profit=profit + 6, roi=D("1")),
        ],
        max_buy_price=D("24"),
        demand_level="high",
        sell_through_rate=0.6,
        estimated_days_to_sell=4,
        flip_score=88,
        confidence_score=80,
        risk_score=15,
    )
    a = RuleBasedDealAnalyst().analyze_sync(ctx)
    assert a.verdict == expected
    assert a.summary
    if expected == Verdict.BUY:
        assert "sotto la mediana" in a.summary and a.pros


def test_client_ip_ignores_forged_forwarded_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request

    from app.core.config import get_settings
    from app.core.rate_limit import client_ip

    def request(xff: str | None) -> Request:
        headers = [(b"x-forwarded-for", xff.encode())] if xff else []
        return Request({"type": "http", "headers": headers, "client": ("10.0.0.5", 1234)})

    settings = get_settings()
    # The Next.js proxy appends the real peer address after whatever the client sent.
    assert client_ip(request("6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert client_ip(request("203.0.113.9")) == "203.0.113.9"
    assert client_ip(request(None)) == "10.0.0.5"
    monkeypatch.setattr(settings, "trusted_proxy_hops", 2)  # nginx in front of Next.js
    assert client_ip(request("6.6.6.6, 203.0.113.9, 172.18.0.4")) == "203.0.113.9"
    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    assert client_ip(request("203.0.113.9")) == "10.0.0.5"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://localhost:3000", ["http://localhost:3000"]),
        ("https://a.example, https://b.example", ["https://a.example", "https://b.example"]),
        ('["https://c.example"]', ["https://c.example"]),
    ],
)
def test_cors_origins_from_environment(
    raw: str, expected: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import Settings

    monkeypatch.setenv("CORS_ORIGINS", raw)
    assert Settings().cors_origins == expected


def test_generated_vapid_keys_are_usable_by_webpush() -> None:
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid
    from py_vapid.utils import b64urlencode

    from app.tools.vapid import generate

    public_key, private_key = generate()
    restored = Vapid.from_string(private_key=private_key)  # what pywebpush does with the env value
    derived = restored.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    assert b64urlencode(derived) == public_key
