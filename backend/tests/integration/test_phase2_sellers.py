"""Phase 2.5: a seller is a protected key plus rating and review count - nothing else."""

import uuid

import asyncpg
from sqlalchemy import select

from app.core.config import get_settings
from app.core.seller_key import PREFIX, protect
from app.db.models import Listing, Seller
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.marketplace.base import ManualListingInput, ProviderSeller
from tests.conftest import NOW
from tests.integration.test_tracking_ingest import card


async def test_the_database_holds_the_protected_key_not_the_one_received(session, make_listing) -> None:
    pl = card(make_listing(external_id="6001", seller_id="h:0000000000000000000001f4"))
    pl = pl.model_copy(
        update={
            "seller": ProviderSeller(external_id="h:0000000000000000000001f4", rating=4.8, review_count=31)
        }
    )
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest([pl], now=NOW)
    await session.commit()
    seller = (await session.execute(select(Seller))).scalar_one()
    assert seller.external_id == protect(
        "h:0000000000000000000001f4", get_settings().seller_key_secret_bytes()
    )
    assert seller.external_id.startswith(PREFIX) and "1f4" not in seller.external_id
    assert float(seller.rating) == 4.8 and seller.review_count == 31
    # Same seller on another listing: the same row (reposts and recycled photos rely on it).
    other = card(make_listing(external_id="6002")).model_copy(update={"seller": pl.seller})
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest([other], now=NOW)
    await session.commit()
    assert len((await session.execute(select(Seller))).scalars().all()) == 1
    li = (await session.execute(select(Listing).where(Listing.external_id == "6002"))).scalar_one()
    assert li.seller_id == seller.id


def test_a_seller_has_no_other_personal_data() -> None:
    assert set(Seller.__table__.columns.keys()) == {
        "id",
        "provider",
        "external_id",
        "rating",
        "review_count",
        "reliability_score",
        "reliability_details",
        "first_seen_at",
        "updated_at",
    }
    assert set(ProviderSeller.model_fields) == {"external_id", "rating", "review_count"}
    assert "seller_username" not in ManualListingInput.model_fields


def test_a_username_sent_by_an_old_client_is_ignored() -> None:
    body = ManualListingInput.model_validate(
        {
            "url": "https://www.vinted.it/items/1-x",
            "title": "Felpa",
            "price": 10,
            "seller_username": "mario_rossi",
        }
    )
    assert "mario_rossi" not in body.model_dump_json()


async def test_migration_protects_keys_in_place_and_drops_the_unused_columns() -> None:
    from tests.integration.test_migration import ADMIN_DSN, MIG_DB, MIG_DSN, alembic

    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
    await admin.execute(f"CREATE DATABASE {MIG_DB}")
    await admin.close()
    c = None
    try:
        alembic("upgrade", "0013")
        c = await asyncpg.connect(MIG_DSN)
        sid, lid = uuid.uuid4(), uuid.uuid4()
        await c.execute(
            "INSERT INTO sellers (id, provider, external_id, rating, review_count, item_count)"
            " VALUES ($1, 'vinted', 'h:0000000000000000000001f4', 4.8, 31, 77)",
            sid,
        )
        await c.execute(
            "INSERT INTO listings (id, provider, external_id, url, title, price, status, seller_id)"
            " VALUES ($1, 'vinted', '9300', 'https://www.vinted.it/items/9300', 'Felpa', 18, 'active', $2)",
            lid,
            sid,
        )
        alembic("upgrade", "0014")
        row = await c.fetchrow("SELECT * FROM sellers")
        assert row["id"] == sid  # same row: listings still point at it
        assert row["external_id"] == protect(
            "h:0000000000000000000001f4", get_settings().seller_key_secret_bytes()
        )
        assert float(row["rating"]) == 4.8 and row["review_count"] == 31
        assert not {"account_created_at", "item_count", "sold_count", "country", "last_active_at"} & set(
            row.keys()
        )
        assert await c.fetchval("SELECT seller_id FROM listings WHERE id = $1", lid) == sid
        alembic("downgrade", "0013")
        assert await c.fetchval("SELECT count(*) FROM sellers") == 1
    finally:
        if c is not None:
            await c.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
        await admin.close()
