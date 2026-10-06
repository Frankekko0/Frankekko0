"""Photos found in other sellers' listings (recycled or catalogue photos) and sellers listing
the same new item in several sizes."""

from sqlalchemy import select, update

from app.db.models import Listing, ListingImage
from app.ingestion.service import IngestionService
from app.opportunities.engine import SAME_ITEM_ANOMALY
from app.opportunities.pipeline import AnalysisPipeline
from app.vision.provenance import photo_provenance
from tests.conftest import NOW


async def _ingest(session, items):
    res = await IngestionService(session, "test").ingest(items, now=NOW)
    await session.commit()
    return res.new_ids


async def _listing(session, lid):
    return (await session.execute(select(Listing).where(Listing.id == lid))).scalar_one()


async def _set_hashes(session, lid, hashes):
    for pos, h in enumerate(hashes):
        await session.execute(
            update(ListingImage)
            .where(ListingImage.listing_id == lid, ListingImage.position == pos)
            .values(phash=h)
        )
    await session.commit()


async def test_recycled_and_catalogue_photos_are_found_across_sellers(session, make_listing) -> None:
    ids = await _ingest(
        session,
        [
            make_listing(
                seller_id=f"seller{i}", photos=2, title=f"Felpa Stone Island {i}", brand="Stone Island"
            )
            for i in range(6)
        ],
    )
    subject, other, *rest = ids
    photo_a, photo_b = "f0f0f0f0f0f0f0f0", "123456789abcdef0"
    await _set_hashes(session, subject, [photo_a, photo_b])
    # Photo 1 re-uploaded by another seller (recompressed: 2 bits differ).
    await _set_hashes(session, other, ["f0f0f0f0f0f0f0f3", "0000000000000001"])
    s = await _listing(session, subject)
    prov = await photo_provenance(session, s.id, s.seller_id, s.id, [photo_a, photo_b])
    assert prov.reused_photos == [0] and prov.catalog_photos == []
    assert prov.other_listings[0] == [other]

    # Photo 2 also in three more sellers' listings: a catalogue / stock picture.
    for lid in rest[:3]:
        await _set_hashes(session, lid, ["aaaaaaaaaaaaaaaa", photo_b])
    prov = await photo_provenance(session, s.id, s.seller_id, s.id, [photo_a, photo_b])
    assert prov.reused_photos == [0] and prov.catalog_photos == [1]

    # A clearly different photo (many bits apart) never matches.
    prov = await photo_provenance(session, s.id, s.seller_id, s.id, ["0f0f0f0f0f0f0f0f", None])
    assert prov.reused_photos == [] and prov.catalog_photos == []


async def test_the_same_seller_reposting_is_not_recycling(session, make_listing) -> None:
    a, b = await _ingest(
        session,
        [
            make_listing(seller_id="same", photos=1, title="Giacca A"),
            make_listing(seller_id="same", photos=1, title="Giacca B"),
        ],
    )
    await _set_hashes(session, a, ["f0f0f0f0f0f0f0f0"])
    await _set_hashes(session, b, ["f0f0f0f0f0f0f0f0"])
    s = await _listing(session, a)
    prov = await photo_provenance(session, s.id, s.seller_id, s.id, ["f0f0f0f0f0f0f0f0"])
    assert prov.reused_photos == [] and prov.catalog_photos == []


async def test_same_new_item_in_several_sizes_is_a_seller_signal(session, make_listing) -> None:
    items = [
        make_listing(
            f"Felpa Supreme box logo nuova tg {size}",
            seller_id="shop",
            condition="Nuovo con cartellino",
            size=size,
            brand="Supreme",
        )
        for size in ("S", "M", "XL")
    ]
    items.append(
        make_listing(
            "Felpa Supreme box logo usata tg M",
            seller_id="honest",
            condition="Buone condizioni",
            brand="Supreme",
        )
    )
    ids = await _ingest(session, items)
    pipeline = AnalysisPipeline(session)
    shop = (await _listing(session, ids[0])).seller_id
    honest = (await _listing(session, ids[3])).seller_id
    from app.ingestion.catalog import load_catalog

    anomalies = await pipeline.sellers_anomalies([shop, honest], await load_catalog(session), NOW)
    assert SAME_ITEM_ANOMALY in anomalies[shop]
    assert SAME_ITEM_ANOMALY not in anomalies.get(honest, ())
