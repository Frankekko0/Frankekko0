"""Phase 2.4: a listing not confirmed for too long becomes "da verificare", disappears from the
buyable feed, and goes back to what the page says when it is seen again."""

from datetime import timedelta

from sqlalchemy import select

from app.db.models import Listing, Opportunity
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.opportunities.pipeline import AnalysisPipeline
from app.tracking.verification import VerifyThresholds, mark_stale_listings
from tests.conftest import NOW
from tests.integration.test_pipeline import build_market
from tests.integration.test_tracking_ingest import card


async def _listing(session, external_id):
    return (await session.execute(select(Listing).where(Listing.external_id == external_id))).scalar_one()


async def test_stale_listings_become_to_verify_and_return_when_seen(session, make_listing) -> None:
    await build_market(session, make_listing)
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD)
    fresh = card(make_listing(external_id="5001"))
    old = card(make_listing(external_id="5002"))
    reserved = card(make_listing(external_id="5003", status="reserved"))
    sold = card(make_listing(external_id="5004", status="sold", sold_after_days=1))
    await svc.ingest([old, reserved, sold], now=NOW - timedelta(hours=60))
    res = await svc.ingest([fresh], now=NOW - timedelta(hours=1))
    await session.flush()
    await AnalysisPipeline(session).analyze_many(
        list(await _ids(session, "5002")), mode=AcquisitionMode.EXTENSION_CARD
    )
    await session.commit()
    assert res.new_ids

    marked = await mark_stale_listings(session, NOW, VerifyThresholds())
    await session.commit()
    rows = {r.external_id: r for r in (await session.execute(select(Listing))).scalars().all()}
    assert set(marked) == {rows["5002"].id, rows["5003"].id}
    assert rows["5002"].status == "to_verify" and rows["5002"].status_before_verify == "active"
    assert rows["5003"].status == "to_verify" and rows["5003"].status_before_verify == "reserved"
    # Fresh, and closed states, are untouched; nothing is deduced to be sold or removed.
    assert rows["5001"].status == "active" and rows["5004"].status == "sold"
    # No longer offered as an opportunity.
    opp = (
        await session.execute(select(Opportunity).where(Opportunity.listing_id == rows["5002"].id))
    ).scalar_one()
    assert opp.is_active is False
    # Idempotent.
    assert await mark_stale_listings(session, NOW, VerifyThresholds()) == []

    # Seen again by the user's browser: what the card says wins, and it is verified now.
    again = await svc.ingest([old], now=NOW + timedelta(minutes=5))
    await session.commit()
    back = await _listing(session, "5002")
    assert back.status == "active" and back.last_verified_at == NOW + timedelta(minutes=5)
    assert (rows["5002"].id, "to_verify", "active") in again.status_changes


async def test_a_card_that_shows_it_sold_closes_a_stale_listing(session, make_listing) -> None:
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD)
    old = card(make_listing(external_id="5010"))
    await svc.ingest([old], now=NOW - timedelta(hours=60))
    await mark_stale_listings(session, NOW, VerifyThresholds())
    await svc.ingest([old.model_copy(update={"status": "sold"})], now=NOW + timedelta(minutes=1))
    await session.commit()
    li = await _listing(session, "5010")
    assert li.status == "sold" and li.sold_at is not None


async def _ids(session, external_id):
    return [(await _listing(session, external_id)).id]
