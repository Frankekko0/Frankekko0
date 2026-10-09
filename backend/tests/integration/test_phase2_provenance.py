"""Phase 2.1: an unknown publication date stays unknown; the first price is dated at the first
observation; every record has a URL (database constraint) and a date."""

import uuid
from datetime import timedelta
from decimal import Decimal

import asyncpg
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Listing, ListingPriceHistory
from app.domain.enums import AcquisitionMode, CaptureLevel
from app.ingestion.service import IngestionService
from app.marketplace.base import ProviderImage
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


# ---------------------------------------------------------------- 2.2 observations
async def _snapshots(session, external_id):
    from app.db.models import ListingSnapshot

    return (
        (
            await session.execute(
                select(ListingSnapshot)
                .join(Listing, Listing.id == ListingSnapshot.listing_id)
                .where(Listing.external_id == external_id)
                .order_by(ListingSnapshot.observed_at, ListingSnapshot.id)
            )
        )
        .scalars()
        .all()
    )


async def test_scrolling_past_the_same_card_adds_one_row_not_ten(session, make_listing) -> None:
    pl = card(make_listing(external_id="3001"))
    svc = IngestionService(
        session,
        "vinted",
        AcquisitionMode.EXTENSION_CARD,
        extension_version="1.2.0",
        parser_version="2026.10.1",
    )
    for i in range(10):
        await svc.ingest([pl], now=NOW + timedelta(minutes=5 * i))
    await session.commit()
    snaps = await _snapshots(session, "3001")
    assert len(snaps) == 1 and snaps[0].reason == "first"
    assert snaps[0].extension_version == "1.2.0" and snaps[0].parser_version == "2026.10.1"
    assert snaps[0].payload["fields"]["price"]["t"] == "observed"
    assert snaps[0].image_set and len(snaps[0].image_set) == 1
    li = (await session.execute(select(Listing).where(Listing.external_id == "3001"))).scalar_one()
    # The listing row still says it was seen and verified at the last pass.
    assert li.last_seen_at == NOW + timedelta(minutes=45)
    assert li.last_verified_at == NOW + timedelta(minutes=45)


async def test_a_heartbeat_row_after_six_hours_and_rows_for_changes(session, make_listing) -> None:
    pl = card(make_listing(external_id="3002", price=30))
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD)
    await svc.ingest([pl], now=NOW)
    await svc.ingest([pl], now=NOW + timedelta(hours=7))
    await svc.ingest([pl.model_copy(update={"price": Decimal("25")})], now=NOW + timedelta(hours=8))
    await svc.ingest(
        [pl.model_copy(update={"price": Decimal("25"), "favourite_count": 9})], now=NOW + timedelta(hours=9)
    )
    await session.commit()
    snaps = await _snapshots(session, "3002")
    assert [s.reason for s in snaps] == ["first", "heartbeat", "price", "favourites"]
    # The price history is a view over the same rows: first price and the change, once each.
    li = (await session.execute(select(Listing).where(Listing.external_id == "3002"))).scalar_one()
    points = (
        await session.execute(
            select(ListingPriceHistory.price, ListingPriceHistory.observed_at)
            .where(ListingPriceHistory.listing_id == li.id)
            .order_by(ListingPriceHistory.observed_at)
        )
    ).all()
    assert [(p.price, p.observed_at) for p in points] == [
        (Decimal("30"), NOW),
        (Decimal("25"), NOW + timedelta(hours=8)),
    ]


async def test_a_changed_gallery_is_recorded_and_a_card_cover_is_not_a_change(session, make_listing) -> None:
    base = make_listing(external_id="3003", photos=4)
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_DEEP)
    full = base.model_copy(update={"images_authoritative": True})
    await svc.ingest([full], now=NOW)
    # The same item seen later as a card (cover only): nothing changed.
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD).ingest(
        [card(base)], now=NOW + timedelta(minutes=10)
    )
    # The seller added a photo: the complete gallery has one more.
    more = make_listing(external_id="3003", photos=5, phash_seed=1).model_copy(
        update={
            "images_authoritative": True,
            "images": [*base.images, ProviderImage(url="/img/3003/new.jpg")],
        }
    )
    await svc.ingest([more], now=NOW + timedelta(minutes=20))
    await session.commit()
    snaps = await _snapshots(session, "3003")
    assert [s.reason for s in snaps] == ["first", "photos"]
    assert len(snaps[1].image_set) == 5 and snaps[1].image_set_complete


async def test_a_link_only_record_is_not_verified(session) -> None:
    from app.acquisition.service import import_links

    await import_links(session, [("https://www.vinted.it/items/3004-felpa", "3004")], now=NOW)
    await session.commit()
    li = (await session.execute(select(Listing).where(Listing.external_id == "3004"))).scalar_one()
    assert li.last_verified_at is None
    snaps = await _snapshots(session, "3004")
    assert len(snaps) == 1 and snaps[0].price is None and snaps[0].status is None
    assert "price" not in snaps[0].payload["fields"]


async def test_migration_keeps_legacy_price_points_in_the_view() -> None:
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
    await admin.execute(f"CREATE DATABASE {MIG_DB}")
    await admin.close()
    c = None
    try:
        alembic("upgrade", "0010")
        c = await asyncpg.connect(MIG_DSN)
        lid = uuid.uuid4()
        await c.execute(
            "INSERT INTO listings (id, provider, external_id, url, title, price, status, capture_level)"
            " VALUES ($1, 'vinted', '9100', 'https://www.vinted.it/items/9100', 'Felpa', 18, 'active', 'full')",
            lid,
        )
        # One price is carried by a snapshot; the earlier one only by the old history table.
        await c.execute(
            "INSERT INTO listing_snapshots (listing_id, observed_at, acquisition_mode, price)"
            " VALUES ($1, now() - interval '1 day', 'extension_item', 18)",
            lid,
        )
        for price, days in ((25, 4), (18, 1)):
            await c.execute(
                "INSERT INTO listing_price_history (listing_id, price, observed_at)"
                " VALUES ($1, $2, now() - make_interval(days => $3::int))",
                lid,
                price,
                days,
            )
        alembic("upgrade", "0011")
        rows = await c.fetch(
            "SELECT price FROM listing_price_history WHERE listing_id = $1 ORDER BY observed_at", lid
        )
        assert [float(r["price"]) for r in rows] == [25.0, 18.0]
        assert await c.fetchval("SELECT count(*) FROM listing_snapshots WHERE reason = 'migrated'") == 1
        alembic("downgrade", "0010")
        rows = await c.fetch(
            "SELECT price FROM listing_price_history WHERE listing_id = $1 ORDER BY observed_at", lid
        )
        assert [float(r["price"]) for r in rows] == [25.0, 18.0]
    finally:
        if c is not None:
            await c.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
        await admin.close()
