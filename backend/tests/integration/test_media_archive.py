"""Local copies of listing photos: uploads from the browser, validation, dedup, protected serving."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import ListingImage
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.marketplace.base import ProviderImage
from app.media import archive
from tests.conftest import NOW
from tests.photos import jpeg

PNG = jpeg(3, fmt="PNG")
CDN = "https://images1.vinted.net/t"


@pytest.fixture
def media_settings(tmp_path, monkeypatch):
    s = get_settings().model_copy(update={"media_dir": str(tmp_path), "image_archive_max_bytes": 1_000_000})
    monkeypatch.setattr(archive, "get_settings", lambda: s)
    return s


async def _listing(session, make_listing, urls, external_id="31337"):
    pl = make_listing(external_id=external_id).model_copy(
        update={"images": [ProviderImage(url=u) for u in urls]}
    )
    res = await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest([pl], now=NOW)
    await session.commit()
    keys = (
        (
            await session.execute(
                select(ListingImage.image_key)
                .where(ListingImage.listing_id == res.new_ids[0])
                .order_by(ListingImage.position)
            )
        )
        .scalars()
        .all()
    )
    return res.new_ids[0], list(keys)


async def test_uploads_are_stored_once_validated_and_linked_to_the_known_photo(
    session, make_listing, media_settings, tmp_path
) -> None:
    urls = [f"{CDN}/a/1.jpeg", f"{CDN}/b/2.jpeg", f"{CDN}/c/3.jpeg", f"{CDN}/d/4.jpeg"]
    listing_id, keys = await _listing(session, make_listing, urls)
    same = jpeg(1)
    wanted = await archive.photos_wanted(session, [listing_id])
    assert [p["image_key"] for p in wanted[listing_id]] == keys  # all four, in gallery order

    first = await archive.register_upload(session, "31337", keys[0], same, "image/jpeg", media_settings)
    second = await archive.register_upload(session, "31337", keys[1], same, "image/jpeg", media_settings)
    third = await archive.register_upload(session, "31337", keys[2], PNG, "image/png", media_settings)
    await session.commit()
    assert first.stored and second.stored and third.stored
    assert first.sha256 == second.sha256 and third.sha256 != first.sha256
    assert len(list(tmp_path.rglob("*.jpg"))) == 1 and len(list(tmp_path.rglob("*.png"))) == 1
    assert third.remaining == 1  # one photo still missing
    rows = (await session.execute(select(ListingImage).order_by(ListingImage.position))).scalars().all()
    assert [r.archive_status for r in rows[:3]] == ["ok", "ok", "ok"]
    assert rows[0].local_path == rows[1].local_path and rows[0].phash and rows[0].width == 64
    assert [p["image_key"] for p in (await archive.photos_wanted(session, [listing_id]))[listing_id]] == [
        keys[3]
    ]
    # What is stored is what was uploaded.
    assert await archive.read_copy(rows[0].local_path, media_settings) == same


@pytest.mark.parametrize(
    ("data", "declared", "reason"),
    [
        (b"", "image/jpeg", "file vuoto"),
        (b"x" * 2_000_000, "image/jpeg", "troppo grande"),
        (b"<html>not an image</html>", "image/jpeg", "non decodificabile"),
        (b"<html>", "text/html", "tipo non consentito"),
        (None, "image/png", "non è image/png"),  # a JPEG declared as PNG
        (None, None, "tipo non consentito"),
    ],
)
async def test_what_is_not_a_real_image_is_refused_with_the_reason(
    session, make_listing, media_settings, data, declared, reason
) -> None:
    listing_id, keys = await _listing(session, make_listing, [f"{CDN}/a/1.jpeg"])
    res = await archive.register_upload(
        session, "31337", keys[0], jpeg(1) if data is None else data, declared, media_settings
    )
    assert not res.stored and reason in (res.error or "")
    image = (await session.execute(select(ListingImage))).scalar_one()
    assert image.local_path is None  # nothing was attached
    assert (await archive.photos_wanted(session, [listing_id]))[listing_id]  # still wanted


async def test_a_photo_the_server_does_not_know_is_never_accepted(
    session, make_listing, media_settings, tmp_path
) -> None:
    _, keys = await _listing(session, make_listing, [f"{CDN}/a/1.jpeg"])
    unknown_key = await archive.register_upload(
        session, "31337", "0123456789abcdef", jpeg(1), "image/jpeg", media_settings
    )
    unknown_item = await archive.register_upload(
        session, "99999", keys[0], jpeg(1), "image/jpeg", media_settings
    )
    assert not unknown_key.stored and not unknown_item.stored
    assert unknown_key.listing_id is None and unknown_item.listing_id is None
    assert list(tmp_path.rglob("*.jpg")) == []  # nothing was written for them


async def test_decompression_bombs_and_tiny_images_are_refused(media_settings) -> None:
    from io import BytesIO

    from PIL import Image

    tiny = BytesIO()
    Image.new("RGB", (8, 8), "white").save(tiny, "JPEG")
    assert (
        archive.validate_image(tiny.getvalue(), "image/jpeg", media_settings).error
        == "immagine troppo piccola"
    )
    huge = BytesIO()
    Image.new("1", (9000, 9000)).save(huge, "PNG")  # ~1 KB compressed, 81 megapixels
    assert (
        archive.validate_image(huge.getvalue(), "image/png", media_settings).error == "dimensioni eccessive"
    )


async def test_resolve_refuses_paths_outside_the_media_dir(media_settings, tmp_path: Path) -> None:
    (tmp_path / "ok.jpg").write_bytes(jpeg(1))
    assert archive.resolve("ok.jpg", media_settings) == (tmp_path / "ok.jpg").resolve()
    assert archive.resolve("../../etc/passwd", media_settings) is None
    assert archive.resolve("missing.jpg", media_settings) is None


async def test_media_endpoint_requires_sign_in(
    client: httpx.AsyncClient, make_listing, media_settings
) -> None:
    from app.db.session import session_scope
    from tests.conftest import register

    async with session_scope() as s:
        _, keys = await _listing(s, make_listing, [f"{CDN}/z/1.jpeg"], external_id="777")
        up = await archive.register_upload(s, "777", keys[0], jpeg(7), "image/jpeg", media_settings)
        assert up.stored
        image_id = (await s.execute(select(ListingImage.id))).scalar_one()
    anonymous = await client.get(f"/api/v1/media/{image_id}")
    assert anonymous.status_code == 401
    await register(client)
    r = await client.get(f"/api/v1/media/{image_id}")
    assert r.status_code == 200 and r.content == jpeg(7)
