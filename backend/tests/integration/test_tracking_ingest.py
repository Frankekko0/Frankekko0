"""Persistence and traceability: snapshots, dedup by Vinted ID, enrichment, status lifecycle."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.acquisition.identity import listing_identity
from app.db.models import (
    AcquisitionAttempt,
    Listing,
    ListingImage,
    ListingSnapshot,
    Opportunity,
    OpportunityScore,
)
from app.domain.enums import AcquisitionMode, CaptureLevel, StatusEvidence
from app.ingestion.service import IngestionService
from app.marketplace.base import ProviderImage, ProviderListing
from app.opportunities.pipeline import AnalysisPipeline
from app.tracking.service import Attempt, TrackingService, record_attempts
from app.tracking.status import Observation
from tests.conftest import NOW
from tests.integration.test_pipeline import build_market


def card(pl: ProviderListing, **kw) -> ProviderListing:
    """The same listing as a search card shows it: one photo, no description, no views."""
    return pl.model_copy(
        update={
            "description": "",
            "images": pl.images[:1],
            "view_count": None,
            "capture_level": CaptureLevel.CARD,
            "seller": None,
            **kw,
        }
    )


def test_vinted_links_map_to_one_record_whatever_the_domain() -> None:
    assert listing_identity("https://www.vinted.it/items/4242-felpa") == ("vinted", "4242")
    assert listing_identity("https://www.vinted.fr/items/4242-sweat?referrer=catalog") == ("vinted", "4242")
    assert listing_identity("https://vinted.co.uk/items/4242") == ("vinted", "4242")
    # Not Vinted: kept apart, never merged into a Vinted record.
    provider, ext = listing_identity("https://example.com/items/4242-x")
    assert provider == "manual" and ext == "4242"
    assert listing_identity("https://www.vinted.it.evil.com/items/4242")[0] == "manual"


async def test_every_observation_adds_a_snapshot_and_dedups_by_id(session, make_listing) -> None:
    pl = make_listing(price=30, external_id="777")
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD)
    first = await svc.ingest([card(pl)], now=NOW)
    second = await svc.ingest(
        [card(pl, price=Decimal("26"), favourite_count=9)], now=NOW + timedelta(hours=3)
    )
    await session.commit()
    assert len(first.new_ids) == 1 and second.updated_ids == first.new_ids
    assert (await session.execute(select(func.count()).select_from(Listing))).scalar_one() == 1
    snaps = (
        (await session.execute(select(ListingSnapshot).order_by(ListingSnapshot.observed_at))).scalars().all()
    )
    assert [(s.price, s.favourite_count, s.acquisition_mode) for s in snaps] == [
        (Decimal("30.00"), 4, "extension_card"),
        (Decimal("26.00"), 9, "extension_card"),
    ]
    listing = (await session.execute(select(Listing))).scalar_one()
    assert listing.price == Decimal("26.00") and listing.favourite_count == 9
    assert listing.view_count == 0  # never seen: not overwritten with a guess
    # Scroll captures are market data: not tracked, no periodic checks.
    assert listing.tracked_at is None and listing.next_check_at is None
    assert listing.acquisition_mode == "extension_card" and listing.capture_level == "card"


async def test_richer_capture_enriches_poorer_never_downgrades(session, make_listing) -> None:
    pl = make_listing(photos=5, external_id="888")
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD).ingest([card(pl)], now=NOW)
    full = pl.model_copy(
        update={"description": "Polo originale, etichetta e cartellino in foto. Nessun difetto."}
    )
    res = await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM, track=True).ingest(
        [full], now=NOW + timedelta(hours=1)
    )
    await session.commit()
    assert res.enriched_ids == res.updated_ids
    listing = (await session.execute(select(Listing))).scalar_one()
    assert listing.capture_level == "full" and listing.photo_count == 5
    assert "etichetta" in listing.description
    assert listing.tracked_at is not None and listing.next_check_at is not None  # tracked on request
    assert listing.acquisition_mode == "extension_card"  # first acquisition is kept
    images = (await session.execute(select(ListingImage.url).order_by(ListingImage.position))).scalars().all()
    assert images == [i.url for i in pl.images]
    # A later card (one photo, no description) refreshes price/status only.
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD).ingest(
        [card(pl, price=Decimal("25"))], now=NOW + timedelta(hours=2)
    )
    await session.commit()
    session.expire_all()
    listing = (await session.execute(select(Listing))).scalar_one()
    assert listing.capture_level == "full" and listing.photo_count == 5 and "etichetta" in listing.description
    assert listing.price == Decimal("25.00")
    assert (await session.execute(select(func.count()).select_from(ListingImage))).scalar_one() == 5


async def test_sale_seen_on_the_page_records_window_estimate(session, make_listing) -> None:
    pl = make_listing(price=40, external_id="999", published_days_ago=2)
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM, track=True)
    await svc.ingest([pl], now=NOW)
    sold = pl.model_copy(update={"status": "sold", "price": Decimal("38")})
    res = await svc.ingest([sold], now=NOW + timedelta(hours=8))
    await session.commit()
    listing = (await session.execute(select(Listing))).scalar_one()
    assert listing.status == "sold"
    assert listing.sold_detected_at == NOW + timedelta(hours=8)
    assert listing.sold_at == NOW + timedelta(hours=4)
    assert listing.last_active_price == Decimal("40.00")  # last price seen while on sale
    assert listing.days_to_sell == Decimal("2.2")
    assert listing.next_check_at is None  # closed: no more checks
    assert res.status_changes == [(listing.id, "active", "sold")]


async def test_not_found_is_removed_and_failures_are_logged(session, make_listing) -> None:
    await build_market(session, make_listing)
    pl = make_listing(price=12, published_days_ago=0.1, external_id="5555")
    res = await IngestionService(session, "vinted", AcquisitionMode.LINK_IMPORT).ingest([pl], now=NOW)
    lid = res.new_ids[0]
    await AnalysisPipeline(session).analyze_many([lid], now=NOW, mode=AcquisitionMode.LINK_IMPORT)
    opp = (await session.execute(select(Opportunity).where(Opportunity.listing_id == lid))).scalar_one()
    opp_id = opp.id
    assert opp.acquisition_mode == "link_import" and opp.analysis_depth == "full"
    score = (await session.execute(select(OpportunityScore))).scalars().one()
    assert score.acquisition_mode == "link_import" and score.algorithm_version

    later = NOW + timedelta(hours=5)
    updates = await TrackingService(session).observe(
        {lid: Observation(later, StatusEvidence.NOT_FOUND)}, AcquisitionMode.PUBLIC_FETCH
    )
    await record_attempts(
        session,
        [
            Attempt(
                "public_fetch",
                "refresh",
                listing_id=lid,
                outcome="not_found",
                message="Pagina non trovata (404).",
            )
        ],
    )
    await session.commit()
    session.expire_all()
    listing = await session.get(Listing, lid)
    assert updates[lid].status == "removed" and listing.status == "removed"
    assert listing.sold_at is None and listing.removed_at == later and listing.next_check_at is None
    assert (await session.get(Opportunity, opp_id)).is_active is False
    attempt = (await session.execute(select(AcquisitionAttempt))).scalar_one()
    assert attempt.outcome == "not_found" and attempt.message.startswith("Pagina non trovata")
    snaps = (await session.execute(select(ListingSnapshot.note).order_by(ListingSnapshot.id))).scalars().all()
    assert "vendita non dedotta" in snaps[-1]


async def test_unreachable_check_backs_off_without_touching_status(session, make_listing) -> None:
    pl = make_listing(external_id="4444", published_days_ago=10)
    res = await IngestionService(session, "vinted", AcquisitionMode.LINK_IMPORT).ingest([pl], now=NOW)
    lid = res.new_ids[0]
    first_due = (await session.get(Listing, lid)).next_check_at
    await TrackingService(session).observe(
        {lid: Observation(NOW, StatusEvidence.UNREACHABLE)}, "public_fetch"
    )
    await session.commit()
    session.expire_all()
    listing = await session.get(Listing, lid)
    assert listing.status == "active" and listing.check_failures == 1
    assert listing.next_check_at - NOW == 2 * (first_due - NOW)


async def test_provider_scan_listings_are_scheduled_and_untracked(session, make_listing) -> None:
    res = await IngestionService(session, "feed").ingest([make_listing()], now=NOW)
    listing = await session.get(Listing, res.new_ids[0])
    assert listing.acquisition_mode == "provider_scan"
    assert listing.tracked_at is None and listing.next_check_at is not None


async def test_photos_and_capture_with_more_images_replace_in_order(session, make_listing) -> None:
    pl = make_listing(photos=2, external_id="3333")
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM)
    await svc.ingest([pl], now=NOW)
    more = pl.model_copy(
        update={"images": [ProviderImage(url=f"https://images1.vinted.net/t/x/{i}.jpeg") for i in range(4)]}
    )
    await svc.ingest([more], now=NOW + timedelta(minutes=5))
    await session.commit()
    urls = (
        (
            await session.execute(
                select(ListingImage.url)
                .where(ListingImage.removed_at.is_(None))
                .order_by(ListingImage.position)
            )
        )
        .scalars()
        .all()
    )
    assert urls == [f"https://images1.vinted.net/t/x/{i}.jpeg" for i in range(4)]
    # The two photos it no longer lists are history, not deleted.
    retired = (
        (await session.execute(select(ListingImage.url).where(ListingImage.removed_at.is_not(None))))
        .scalars()
        .all()
    )
    assert sorted(retired) == ["/img/1/0.jpg", "/img/1/1.jpg"]
