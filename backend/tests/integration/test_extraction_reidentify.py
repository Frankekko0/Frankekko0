"""Re-reading the identity of listings stored before the fixes (COND-001 backfill)."""

from collections.abc import Callable

import pytest
from sqlalchemy import select, update

from app.db.models import Listing
from app.ingestion.catalog import load_catalog
from app.ingestion.reidentify import reidentify
from app.ingestion.service import IngestionService
from app.marketplace.base import ProviderListing

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_a_listing_stored_with_an_unknown_condition_is_read_again(
    session, make_listing: Callable[..., ProviderListing]
) -> None:
    pl = make_listing(
        condition="Ottime",
        title="Giacca Zara nera",
        brand="Zara",
        category="Uomo > Vestiti > Cappotti e giacche > Giacche",
    )
    catalog = await load_catalog(session, force=True)
    await IngestionService(session, "test").ingest([pl])
    await session.commit()
    # What the old code stored for the real vinted.it label: the raw label kept, the condition lost.
    await session.execute(update(Listing).values(condition="unknown", category_id=None, gender=None))
    await session.commit()

    dry = await reidentify(session, apply=False)
    assert (
        dry.scanned == 1 and dry.changed == 1 and {"condition", "category_id", "gender"} <= set(dry.by_field)
    )
    row = (await session.execute(select(Listing))).scalar_one()
    assert row.condition == "unknown"  # a dry run writes nothing

    done = await reidentify(session, apply=True)
    await session.commit()
    row = (await session.execute(select(Listing))).scalar_one()
    assert (row.condition, row.condition_raw, row.gender) == ("very_good", "Ottime", "men")
    assert row.category_id is not None and done.changed_ids == [row.id]
    assert row.identification["category"]["value"] == "jackets"
    # Nothing new to say: a second pass changes nothing.
    again = await reidentify(session, apply=True)
    assert again.changed == 0


async def test_a_reading_that_finds_nothing_does_not_erase_what_a_photo_analysis_found(
    session, make_listing: Callable[..., ProviderListing]
) -> None:
    pl = make_listing(brand=None, title="Giacca nera", category=None, condition=None)
    catalog = await load_catalog(session, force=True)
    await IngestionService(session, "test").ingest([pl])
    await session.commit()
    brand_id = catalog.brand_id("moncler")
    await session.execute(
        update(Listing).values(
            brand_id=brand_id,
            identification={
                "brand": {"value": "moncler", "source": "image", "certainty": "probable", "confidence": 0.9}
            },
        )
    )
    await session.commit()
    await reidentify(session, apply=True)
    await session.commit()
    row = (await session.execute(select(Listing))).scalar_one()
    assert row.brand_id == brand_id and row.identification["brand"]["value"] == "moncler"
