"""Acquisition modes: polite public fetch (simulated Vinted), refresh fallback, emails, links."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.acquisition.public_fetch import FetchOutcome, PublicPageFetcher
from app.acquisition.service import import_links, process_email_bytes, refresh_listing
from app.core.config import get_settings
from app.core.redis import get_redis
from app.db.models import AcquisitionAttempt, Listing, ListingSnapshot, Opportunity
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from tests.conftest import NOW
from tests.integration.test_pipeline import build_market

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "vinted"
ITEM_URL = "https://www.vinted.it/items/4242424242-felpa-ralph-lauren-blu"
ROBOTS_OK = "User-agent: *\nDisallow: /member/\nAllow: /\n"


def settings(**kw):
    return get_settings().model_copy(
        update={"vinted_public_fetch_enabled": True, "vinted_public_fetch_min_interval_seconds": 30, **kw}
    )


class FakeVinted:
    """Answers like Vinted would; records every request (and asserts no cookie is ever sent)."""

    def __init__(self, pages: dict[str, tuple[int, str]], robots: str = ROBOTS_OK) -> None:
        self.pages = pages
        self.robots = robots
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert "cookie" not in {k.lower() for k in request.headers}
        assert request.headers["user-agent"].startswith("FlipFinder/")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        status, body = self.pages.get(request.url.path, (404, "not found"))
        return httpx.Response(status, text=body)


async def fresh_redis() -> None:
    await get_redis().flushdb()


def fetcher(fake: FakeVinted, **kw) -> PublicPageFetcher:
    return PublicPageFetcher(settings(**kw), transport=httpx.MockTransport(fake))


ACTIVE_HTML = (FIX / "item_active.html").read_text()
SOLD_HTML = (
    (FIX / "item_sold.html")
    .read_text()
    .replace("/items/5555-pull", "/items/4242424242-felpa-ralph-lauren-blu")
)


async def test_disabled_by_default_and_never_contacts_vinted(clean_db) -> None:
    fake = FakeVinted({})
    f = PublicPageFetcher(get_settings(), transport=httpx.MockTransport(fake))
    r = await f.fetch_item(ITEM_URL)
    assert r.outcome == FetchOutcome.DISABLED and not fake.requests


async def test_polite_fetch_cache_interval_robots_and_cap(clean_db) -> None:
    await fresh_redis()
    fake = FakeVinted(
        {"/items/4242424242-felpa-ralph-lauren-blu": (200, ACTIVE_HTML), "/items/1-x": (200, ACTIVE_HTML)}
    )
    f = fetcher(fake)
    first = await f.fetch_item(ITEM_URL + "?referrer=catalog")
    assert first.outcome == FetchOutcome.OK and first.has_page
    again = await f.fetch_item(ITEM_URL)
    assert again.outcome == FetchOutcome.CACHED  # no new request
    other = await f.fetch_item("https://www.vinted.it/items/1-x")
    assert other.outcome == FetchOutcome.WAIT and 0 < other.retry_after <= 30
    assert [r.url.path for r in fake.requests] == ["/robots.txt", "/items/4242424242-felpa-ralph-lauren-blu"]
    assert (await f.fetch_item("https://www.vinted.it.evil.com/items/1")).outcome == FetchOutcome.INVALID
    assert (await f.fetch_item("https://www.vinted.it/member/5")).outcome == FetchOutcome.INVALID

    await fresh_redis()
    disallowed = fetcher(FakeVinted({}, robots="User-agent: *\nDisallow: /items/\n"))
    assert (await disallowed.fetch_item(ITEM_URL)).outcome == FetchOutcome.DISALLOWED

    await fresh_redis()
    capped = fetcher(FakeVinted({}), vinted_public_fetch_daily_cap=1)
    await capped.fetch_item("https://www.vinted.it/items/7-a")
    await get_redis().delete("ff:vfetch:last")
    assert (await capped.fetch_item("https://www.vinted.it/items/8-b")).outcome == FetchOutcome.CAP_REACHED


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (403, "Forbidden"),
        (429, "Too many requests"),
        (200, "<html><script src='https://geo.captcha-delivery.com/x.js'></script></html>"),
    ],
)
async def test_any_block_stops_reading_no_workaround(clean_db, status: int, body: str) -> None:
    await fresh_redis()
    fake = FakeVinted({"/items/4242424242-felpa-ralph-lauren-blu": (status, body)})
    f = fetcher(fake)
    r = await f.fetch_item(ITEM_URL)
    assert r.outcome == FetchOutcome.BLOCKED and "nessun tentativo di aggiramento" in r.message
    await get_redis().delete("ff:vfetch:last")
    after = await f.fetch_item("https://www.vinted.it/items/99-z")
    assert after.outcome == FetchOutcome.PAUSED and after.retry_after > 3600
    assert len(fake.requests) == 2  # robots + the blocked page; nothing after the block
    assert (await f.status())["paused_for_seconds"] > 0


async def test_link_import_then_refresh_fills_the_record(session, make_listing, monkeypatch) -> None:
    await fresh_redis()
    await build_market(session, make_listing)
    res = await import_links(session, [(ITEM_URL, "4242424242")], now=NOW)
    lid = res.created[0]
    listing = await session.get(Listing, lid)
    assert (listing.capture_level, listing.status, listing.tracked_at is not None) == (
        "link",
        "unknown",
        True,
    )
    assert listing.title == "Felpa ralph lauren blu" and listing.next_check_at == NOW

    fake = FakeVinted({"/items/4242424242-felpa-ralph-lauren-blu": (200, ACTIVE_HTML)})
    monkeypatch.setattr("app.acquisition.service.get_settings", lambda: settings(), raising=False)
    monkeypatch.setattr("app.tracking.refresh.get_settings", lambda: settings())
    result = await refresh_listing(session, listing, NOW + timedelta(minutes=1), fetcher(fake))
    await session.commit()
    assert result.outcome == "updated" and result.mode == "public_fetch"
    session.expire_all()
    listing = await session.get(Listing, lid)
    assert listing.capture_level == "full" and listing.status == "active"
    assert listing.price == Decimal("18.00") and listing.favourite_count == 17 and listing.photo_count == 3
    assert listing.buyer_protection_fee == Decimal("1.60") and listing.material_raw == "Cotone"
    opp = (await session.execute(select(Opportunity).where(Opportunity.listing_id == lid))).scalar_one()
    assert opp.acquisition_mode == "public_fetch" and opp.analysis_depth == "full"
    snaps = (
        (
            await session.execute(
                select(ListingSnapshot).where(ListingSnapshot.listing_id == lid).order_by(ListingSnapshot.id)
            )
        )
        .scalars()
        .all()
    )
    assert [(s.acquisition_mode, s.price) for s in snaps] == [
        ("link_import", None),
        ("public_fetch", Decimal("18.00")),
    ]

    # Later the page says "sold": a confirmed sale.
    await get_redis().flushdb()
    fake.pages["/items/4242424242-felpa-ralph-lauren-blu"] = (200, SOLD_HTML)
    sold = await refresh_listing(session, listing, NOW + timedelta(hours=7), fetcher(fake))
    await session.commit()
    session.expire_all()
    listing = await session.get(Listing, lid)
    assert sold.status == "sold" and listing.status == "sold" and listing.next_check_at is None
    assert listing.sold_at == NOW + timedelta(minutes=1) + (timedelta(hours=7) - timedelta(minutes=1)) / 2


async def test_block_falls_back_to_the_extension_and_is_logged(session, make_listing, monkeypatch) -> None:
    await fresh_redis()
    res = await import_links(session, [(ITEM_URL, "4242424242")], now=NOW)
    listing = await session.get(Listing, res.created[0])
    monkeypatch.setattr("app.tracking.refresh.get_settings", lambda: settings())
    fake = FakeVinted({"/items/4242424242-felpa-ralph-lauren-blu": (403, "blocked")})
    r = await refresh_listing(session, listing, NOW, fetcher(fake))
    await session.commit()
    assert r.outcome == "blocked" and r.needs_extension
    session.expire_all()
    listing = await session.get(Listing, res.created[0])
    assert listing.status == "unknown" and listing.check_failures == 1  # untouched, backing off
    attempt = (await session.execute(select(AcquisitionAttempt))).scalar_one()
    assert attempt.outcome == "blocked" and attempt.http_status == 403 and "sospesa" in attempt.message
    # Next "update now" while paused: queued for the extension, Vinted not contacted again.
    queued = await refresh_listing(session, listing, NOW, fetcher(fake))
    assert queued.outcome == "queued" and queued.needs_extension and "sospesa" in queued.message
    assert sum(1 for r in fake.requests if r.url.path.startswith("/items/")) == 1


async def test_without_server_modes_the_extension_takes_over(session, make_listing) -> None:
    res = await import_links(session, [(ITEM_URL, "4242424242")], now=NOW)
    listing = await session.get(Listing, res.created[0])
    r = await refresh_listing(session, listing, NOW)
    assert r.outcome == "queued" and r.mode == "extension_refresh" and r.needs_extension


async def test_known_listing_link_keeps_its_data(session, make_listing) -> None:
    pl = make_listing(price=25, external_id="4242424242")
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD).ingest([pl], now=NOW)
    res = await import_links(session, [(ITEM_URL, "4242424242")], now=NOW + timedelta(hours=1))
    await session.commit()
    session.expire_all()
    listing = (await session.execute(select(Listing))).scalar_one()
    assert res.existing == [listing.id] and not res.created
    assert listing.price == Decimal("25.00") and listing.status == "active"  # untouched
    assert listing.tracked_at is not None and listing.next_check_at == NOW + timedelta(hours=1)


async def test_sold_email_confirms_sale_and_price_drop_updates(session, make_listing) -> None:
    await build_market(session, make_listing)
    pl = make_listing(price=18, external_id="4242424242", published_days_ago=2)
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest([pl], now=NOW)
    drop = await process_email_bytes(
        session, [(FIX / "email_price_drop.eml").read_bytes()], NOW + timedelta(hours=2)
    )
    await session.commit()
    assert drop.price_drops == 1 and drop.created == 0
    session.expire_all()
    listing = (await session.execute(select(Listing).where(Listing.external_id == "4242424242"))).scalar_one()
    assert listing.price == Decimal("15.00") and listing.tracked_at is not None
    lid = listing.id
    sold = await process_email_bytes(
        session, [(FIX / "email_sold.eml").read_bytes()], NOW + timedelta(hours=6)
    )
    await session.commit()
    session.expire_all()
    listing = await session.get(Listing, lid)
    assert sold.sold == 1 and listing.status == "sold"
    assert listing.sold_at == NOW + timedelta(hours=4)  # midpoint: price-drop email (active) .. sold email
    notes = (
        (
            await session.execute(
                select(ListingSnapshot.note)
                .where(ListingSnapshot.listing_id == listing.id)
                .order_by(ListingSnapshot.id)
            )
        )
        .scalars()
        .all()
    )
    assert notes[-2:] == ["Email Vinted: prezzo ribassato.", "Email Vinted: articolo venduto."]
    junk = await process_email_bytes(session, [b"From: someone@example.com\nSubject: hi\n\nhello"], NOW)
    assert junk.ignored == 1
