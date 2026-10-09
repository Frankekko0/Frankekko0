"""Acquisition: links, the extension taking over from a link, emails. The server reads no Vinted page."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.acquisition.service import import_links, process_email_bytes, refresh_listing
from app.acquisition.vinted_parser import parse_item_html
from app.core.redis import get_redis
from app.db.models import Listing, ListingSnapshot, Opportunity
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.opportunities.pipeline import AnalysisPipeline
from tests.conftest import NOW
from tests.integration.test_pipeline import build_market

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "vinted"
ITEM_URL = "https://www.vinted.it/items/4242424242-felpa-ralph-lauren-blu"
ACTIVE_HTML = (FIX / "item_active.html").read_text()
SOLD_HTML = (
    (FIX / "item_sold.html")
    .read_text()
    .replace("/items/5555-pull", "/items/4242424242-felpa-ralph-lauren-blu")
)


async def test_link_import_is_completed_by_the_page_the_user_opens(session, make_listing) -> None:
    """A pasted link makes a light record; the page the user then opens on Vinted (captured by the
    extension) fills it in, and the same capture later sees the sale."""
    await get_redis().flushdb()
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

    # Nothing reads Vinted by itself: "refresh" only asks the user to open the listing.
    asked = await refresh_listing(session, listing, NOW)
    assert asked.outcome == "queued" and asked.needs_extension and asked.mode == "extension_item"

    page = parse_item_html(ACTIVE_HTML, ITEM_URL, NOW).to_provider_listing()
    captured = await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest(
        [page], now=NOW + timedelta(minutes=1)
    )
    await AnalysisPipeline(session).analyze_many(captured.updated_ids, mode=AcquisitionMode.EXTENSION_ITEM)
    await session.commit()
    session.expire_all()
    listing = await session.get(Listing, lid)
    assert listing.capture_level == "full" and listing.status == "active"
    assert listing.price == Decimal("18.00") and listing.favourite_count == 17 and listing.photo_count == 3
    assert listing.buyer_protection_fee == Decimal("1.60") and listing.material_raw == "Cotone"
    opp = (await session.execute(select(Opportunity).where(Opportunity.listing_id == lid))).scalar_one()
    assert opp.analysis_depth == "full"
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
        ("extension_item", Decimal("18.00")),
    ]

    # Opened again later, the page says "sold": a confirmed sale.
    sold_page = parse_item_html(SOLD_HTML, ITEM_URL, NOW + timedelta(hours=7)).to_provider_listing()
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest(
        [sold_page], now=NOW + timedelta(hours=7)
    )
    await session.commit()
    session.expire_all()
    listing = await session.get(Listing, lid)
    assert listing.status == "sold" and listing.next_check_at is None
    assert listing.sold_at == NOW + timedelta(minutes=1) + (timedelta(hours=7) - timedelta(minutes=1)) / 2


async def test_without_server_modes_the_extension_takes_over(session, make_listing) -> None:
    res = await import_links(session, [(ITEM_URL, "4242424242")], now=NOW)
    listing = await session.get(Listing, res.created[0])
    r = await refresh_listing(session, listing, NOW)
    assert r.outcome == "queued" and r.mode == "extension_item" and r.needs_extension


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
