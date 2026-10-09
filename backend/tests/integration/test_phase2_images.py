"""Phase 2.3: photos are added, moved or retired - never deleted and re-inserted - and reposts come
with the evidence that decided them."""

from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select, update

from app.api.capture_pipeline import inputs_before
from app.core.config import get_settings
from app.db.models import Listing, ListingImage
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.marketplace.base import ProviderImage
from app.media import archive
from app.media.keys import image_key
from app.media.reposts import link_visual_reposts
from tests.conftest import NOW
from tests.integration.test_tracking_ingest import card

CDN = "https://images1.vinted.net/t"


def gallery(*names: str) -> list[ProviderImage]:
    return [ProviderImage(url=f"{CDN}/{n}.jpeg?s=sig{i}") for i, n in enumerate(names)]


async def current(session, external_id):
    return (
        (
            await session.execute(
                select(ListingImage)
                .join(Listing, Listing.id == ListingImage.listing_id)
                .where(Listing.external_id == external_id, ListingImage.removed_at.is_(None))
                .order_by(ListingImage.position)
            )
        )
        .scalars()
        .all()
    )


def test_the_key_ignores_host_and_signature() -> None:
    assert image_key("https://images1.vinted.net/t/01_a/f800/1.jpeg?s=abc") == image_key(
        "https://images2.vinted.net/t/01_a/f800/1.jpeg?s=zzz#x"
    )
    assert image_key("https://images1.vinted.net/t/01_a/f800/1.jpeg") != image_key(
        "https://images1.vinted.net/t/01_a/f800/2.jpeg"
    )


async def test_a_recapture_keeps_copies_moves_adds_and_retires(session, make_listing) -> None:
    base = make_listing(external_id="4001")
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM)
    first = base.model_copy(update={"images": gallery("a", "b", "c"), "images_authoritative": True})
    await svc.ingest([first], now=NOW)
    await session.flush()
    before = {i.image_key: i for i in await current(session, "4001")}
    await session.execute(
        update(ListingImage)
        .where(ListingImage.id == before[image_key(f"{CDN}/a.jpeg")].id)
        .values(local_path="aa/aaa.jpg", sha256="a" * 64, archive_status="ok")
    )
    # The seller dropped "c", put "b" first and added "d".
    second = first.model_copy(update={"images": gallery("b", "a", "d")})
    await svc.ingest([second], now=NOW + timedelta(days=1))
    await session.commit()
    now_current = await current(session, "4001")
    assert [i.url.split("/")[-1].split(".")[0] for i in now_current] == ["b", "a", "d"]
    kept = {i.image_key: i for i in now_current}
    # Same rows, same copy: nothing was deleted and re-inserted.
    a = kept[image_key(f"{CDN}/a.jpeg")]
    assert a.id == before[a.image_key].id and a.local_path == "aa/aaa.jpg" and a.sha256 == "a" * 64
    assert a.first_seen_at == NOW and a.last_seen_at == NOW + timedelta(days=1)
    retired = (
        (await session.execute(select(ListingImage).where(ListingImage.removed_at.is_not(None))))
        .scalars()
        .all()
    )
    assert [r.image_key for r in retired] == [image_key(f"{CDN}/c.jpeg")]
    assert retired[0].removed_at == NOW + timedelta(days=1)
    li = (await session.execute(select(Listing).where(Listing.external_id == "4001"))).scalar_one()
    assert li.photo_count == 3
    # A retired photo coming back is the same row again.
    third = first.model_copy(update={"images": gallery("b", "a", "c")})
    await svc.ingest([third], now=NOW + timedelta(days=2))
    await session.commit()
    back = {i.image_key: i for i in await current(session, "4001")}
    assert (
        back[image_key(f"{CDN}/c.jpeg")].id == retired[0].id
        and back[image_key(f"{CDN}/c.jpeg")].removed_at is None
    )


async def test_a_card_cover_never_retires_the_gallery(session, make_listing) -> None:
    base = make_listing(external_id="4002")
    full = base.model_copy(update={"images": gallery("a", "b", "c"), "images_authoritative": True})
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest([full], now=NOW)
    cover = card(base).model_copy(update={"images": gallery("a")})
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD).ingest(
        [cover], now=NOW + timedelta(hours=1)
    )
    await session.commit()
    assert len(await current(session, "4002")) == 3


async def test_added_or_swapped_photos_change_the_analysis_inputs(session, make_listing) -> None:
    """The capture pipeline re-analyses a known listing when the inputs it compares change: a photo
    added or swapped (same count) must be one of them."""
    base = make_listing(external_id="4003").model_copy(update={"images": gallery("a", "b")})
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM)
    await svc.ingest([base], now=NOW)
    await session.flush()
    seen = [await inputs_before(session, [base])]
    for step, names in enumerate((("a", "b"), ("a", "z"), ("a", "z", "y")), start=1):
        pl = base.model_copy(update={"images": gallery(*names), "images_authoritative": True})
        await svc.ingest([pl], now=NOW + timedelta(hours=step))
        await session.flush()
        seen.append(await inputs_before(session, [pl]))
    assert seen[1] == seen[0]  # nothing changed: the analysis is reused
    assert seen[2] != seen[1]  # a photo swapped, same count
    assert seen[3] != seen[2]  # a photo added


def jpeg(seed: int, noise: int = 0) -> bytes:
    img = Image.new("RGB", (64, 64))
    px = img.load()
    for x in range(64):
        for y in range(64):
            px[x, y] = (
                (x * 4 + seed * 17) % 256,
                (y * 4 + seed * 31 + noise * (x % 2)) % 256,
                (x * y + seed) % 256,
            )
    buf = BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


@pytest.fixture
def media_settings(tmp_path, monkeypatch):
    s = get_settings().model_copy(update={"media_dir": str(tmp_path)})
    monkeypatch.setattr(archive, "get_settings", lambda: s)
    return s


async def test_a_copy_stores_sha256_and_dhash_and_links_a_same_seller_repost(
    session, make_listing, media_settings
) -> None:
    uploads = {"4101": jpeg(1), "4102": jpeg(1, noise=1), "4103": jpeg(1, noise=1)}  # what the browser sends

    seller = "seller-77"
    old = make_listing(external_id="4101", seller_id=seller, title="Polo Lacoste verde L").model_copy(
        update={"images": [ProviderImage(url=f"{CDN}/old/1.jpeg")], "images_authoritative": True}
    )
    new = make_listing(external_id="4102", seller_id=seller, title="Camicia bianca XL cotone").model_copy(
        update={"images": [ProviderImage(url=f"{CDN}/new/1.jpeg")], "images_authoritative": True}
    )
    other = make_listing(external_id="4103", seller_id="someone-else", title="Giacca nera M").model_copy(
        update={"images": [ProviderImage(url=f"{CDN}/other/1.jpeg")], "images_authoritative": True}
    )
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM)
    r1 = await svc.ingest([old], now=NOW)
    r2 = await svc.ingest([new, other], now=NOW + timedelta(days=3))
    ids = [*r1.new_ids, *r2.new_ids]
    await session.commit()
    for external_id, data in uploads.items():  # photos arrive after the capture, one listing at a time
        key = (
            await session.execute(
                select(ListingImage.image_key)
                .join(Listing, Listing.id == ListingImage.listing_id)
                .where(Listing.external_id == external_id)
            )
        ).scalar_one()
        up = await archive.register_upload(session, external_id, key, data, "image/jpeg", media_settings)
        assert up.stored, up.error
        await session.commit()
    imgs = (await session.execute(select(ListingImage))).scalars().all()
    assert all(i.sha256 and i.phash and i.width == 64 and i.height == 64 for i in imgs)
    rows = {r.external_id: r for r in (await session.execute(select(Listing))).scalars().all()}
    # Different title and article, same seller, same picture: a repost, with the evidence.
    assert rows["4102"].duplicate_of_id == rows["4101"].id
    ev = rows["4102"].duplicate_evidence
    assert ev["rule"] == "photo_hash" and ev["of"] == str(rows["4101"].id) and ev["distance"] <= 4
    # The same picture from another seller is not a repost (it is the photo-reuse signal).
    assert rows["4103"].duplicate_of_id is None and rows["4101"].duplicate_of_id is None
    # Running it again changes nothing.
    assert await link_visual_reposts(session, ids) == {}


async def test_other_duplicate_rules_leave_their_evidence(session, make_listing) -> None:
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM)
    orig = make_listing(external_id="4201", seller_id="s-1", title="Felpa Champion grigia M", price=20)
    await svc.ingest([orig], now=NOW)
    repost = make_listing(external_id="4202", seller_id="s-1", title="Felpa Champion grigia M", price=18)
    await svc.ingest([repost], now=NOW + timedelta(days=5))
    await session.commit()
    rows = {r.external_id: r for r in (await session.execute(select(Listing))).scalars().all()}
    assert rows["4202"].duplicate_of_id == rows["4201"].id
    assert rows["4202"].duplicate_evidence["rule"] == "title_price"
    assert rows["4202"].duplicate_evidence["of"] == str(rows["4201"].id)
    assert rows["4201"].duplicate_evidence is None


async def test_migration_keys_existing_photos_and_retires_exact_duplicates() -> None:
    import uuid

    import asyncpg

    from tests.integration.test_migration import ADMIN_DSN, MIG_DB, MIG_DSN, alembic

    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
    await admin.execute(f"CREATE DATABASE {MIG_DB}")
    await admin.close()
    c = None
    try:
        alembic("upgrade", "0011")
        c = await asyncpg.connect(MIG_DSN)
        lid = uuid.uuid4()
        await c.execute(
            "INSERT INTO listings (id, provider, external_id, url, title, price, status, capture_level)"
            " VALUES ($1, 'vinted', '9200', 'https://www.vinted.it/items/9200', 'Felpa', 18, 'active', 'full')",
            lid,
        )
        urls = [
            "https://images1.vinted.net/t/a.jpeg?s=1",
            "https://images2.vinted.net/t/b.jpeg",
            "https://images1.vinted.net/t/a.jpeg?s=2",  # the first photo again
        ]
        for pos, url in enumerate(urls):
            await c.execute(
                "INSERT INTO listing_images (listing_id, position, url, sha256, local_path)"
                " VALUES ($1, $2::smallint, $3, 'f' || $4::text, 'x/y.jpg')",
                lid,
                pos,
                url,
                str(pos),
            )
        alembic("upgrade", "0012")
        rows = await c.fetch("SELECT * FROM listing_images ORDER BY position")
        assert len(rows) == 3 and all(r["image_key"] and len(r["image_key"]) == 16 for r in rows)
        assert rows[0]["image_key"] == rows[2]["image_key"] != rows[1]["image_key"]
        assert [r["removed_at"] is not None for r in rows] == [False, False, True]
        assert rows[0]["local_path"] == "x/y.jpg"  # copies untouched
        alembic("downgrade", "0011")
        assert await c.fetchval("SELECT count(*) FROM listing_images") == 2
    finally:
        if c is not None:
            await c.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
        await admin.close()
