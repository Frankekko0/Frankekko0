"""Phase 2.1: an unknown publication date stays unknown; the first price is dated at the first
observation; every record has a URL (database constraint) and a date."""

import uuid
from datetime import timedelta

import asyncpg
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Listing, ListingPriceHistory
from app.domain.enums import AcquisitionMode, CaptureLevel
from app.ingestion.service import IngestionService
from tests.conftest import NOW, ROOT, TEST_DB  # noqa: F401
from tests.integration.test_migration import ADMIN_DSN, MIG_DB, MIG_DSN, alembic
from tests.integration.test_tracking_ingest import card


async def test_unknown_publication_date_is_not_replaced_by_the_observation_time(
    session, make_listing
) -> None:
    pl = card(make_listing(external_id="2001")).model_copy(update={"published_at": None})
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD)
    await svc.ingest([pl], now=NOW)
    await session.commit()
    li = (await session.execute(select(Listing).where(Listing.external_id == "2001"))).scalar_one()
    assert li.published_at is None and li.published_at_kind == "unknown"
    assert li.first_seen_at == NOW
    # Recency still works (published, else first seen) without claiming a publication date.
    assert li.listed_at == NOW
    # The first price is dated when it was observed, not at an assumed publication.
    hist = (
        (await session.execute(select(ListingPriceHistory).where(ListingPriceHistory.listing_id == li.id)))
        .scalars()
        .all()
    )
    assert [h.observed_at for h in hist] == [NOW]
    # Days to sell cannot be computed from an unknown date.
    assert li.days_to_sell is None


async def test_the_kind_of_date_is_recorded_and_a_richer_capture_can_supply_it(session, make_listing) -> None:
    base = make_listing(external_id="2002", published_days_ago=2)
    c = card(base).model_copy(update={"published_at": None})
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD)
    await svc.ingest([c], now=NOW)
    full = base.model_copy(update={"published_at_kind": "exact", "capture_level": CaptureLevel.FULL})
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_DEEP).ingest(
        [full], now=NOW + timedelta(hours=1)
    )
    await session.commit()
    li = (await session.execute(select(Listing).where(Listing.external_id == "2002"))).scalar_one()
    assert li.published_at == base.published_at and li.published_at_kind == "exact"
    rel = make_listing(external_id="2003").model_copy(update={"published_at_kind": "relative"})
    await svc.ingest([rel], now=NOW)
    plain = make_listing(external_id="2004")
    await svc.ingest([plain], now=NOW)
    await session.commit()
    kinds = {
        r.external_id: r.published_at_kind
        for r in (await session.execute(select(Listing.external_id, Listing.published_at_kind))).all()
    }
    assert kinds["2003"] == "relative" and kinds["2004"] == "reported"


async def test_a_listing_without_url_is_rejected_by_the_database(session, make_listing) -> None:
    pl = card(make_listing(external_id="2005"))
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD).ingest([pl], now=NOW)
    await session.commit()
    with pytest.raises(IntegrityError):
        await session.execute(text("UPDATE listings SET url = '  ' WHERE external_id = '2005'"))
    await session.rollback()


async def test_migration_drops_dates_that_were_only_the_observation_time() -> None:
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
    await admin.execute(f"CREATE DATABASE {MIG_DB}")
    await admin.close()
    c = None
    try:
        alembic("upgrade", "0009")
        c = await asyncpg.connect(MIG_DSN)
        stamped, real = uuid.uuid4(), uuid.uuid4()
        for lid, ext, published_days_ago in ((stamped, "9001", 0), (real, "9002", 5)):
            await c.execute(
                "INSERT INTO listings (id, provider, external_id, url, title, price, status, first_seen_at,"
                " published_at, sold_at, days_to_sell) VALUES ($1, 'vinted', $2::varchar, 'https://www.vinted.it/items/'||$2::varchar,"
                " 'Felpa', 20, 'sold', now() - interval '3 days',"
                " now() - interval '3 days' - make_interval(days => $3::int), now() - interval '1 day', 2.0)",
                lid,
                ext,
                published_days_ago,
            )
        alembic("upgrade", "0010")
        rows = {r["id"]: r for r in await c.fetch("SELECT * FROM listings")}
        assert len(rows) == 2  # nothing is lost
        assert rows[stamped]["published_at"] is None and rows[stamped]["published_at_kind"] == "unknown"
        assert rows[stamped]["days_to_sell"] is None and rows[stamped]["first_seen_at"] is not None
        assert rows[real]["published_at"] is not None and rows[real]["published_at_kind"] == "reported"
        assert rows[real]["days_to_sell"] is not None
        alembic("downgrade", "0009")
        assert await c.fetchval("SELECT count(*) FROM listings") == 2
    finally:
        if c is not None:
            await c.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
        await admin.close()
