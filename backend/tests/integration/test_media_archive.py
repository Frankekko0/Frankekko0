"""Local copies of listing photos: safe downloads, dedup, failures, protected serving."""

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

JPEG = b"\xff\xd8\xff\xe0" + b"photo-bytes" * 50
CDN = "https://images1.vinted.net/t"


def cdn(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("gone.jpeg"):
        return httpx.Response(404)
    if path.endswith("huge.jpeg"):
        return httpx.Response(200, content=b"x" * 2_000_000, headers={"content-type": "image/jpeg"})
    if path.endswith("page.jpeg"):
        return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
    return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})


@pytest.fixture
def media_settings(tmp_path, monkeypatch):
    s = get_settings().model_copy(update={"media_dir": str(tmp_path), "image_archive_max_bytes": 1_000_000})
    monkeypatch.setattr(archive, "get_settings", lambda: s)
    monkeypatch.setattr(archive, "is_public_https_url", lambda url: True)  # no DNS in tests
    return s


async def test_archive_keeps_copies_and_explains_failures(
    session, make_listing, media_settings, tmp_path
) -> None:
    urls = [
        f"{CDN}/a/1.jpeg",
        f"{CDN}/b/1-dup.jpeg",  # same bytes as the first: stored once
        f"{CDN}/c/gone.jpeg",
        f"{CDN}/d/huge.jpeg",
        f"{CDN}/e/page.jpeg",
        "https://evil.example.com/x.jpeg",
        "/img/polo.svg",
    ]
    pl = make_listing(external_id="31337").model_copy(update={"images": [ProviderImage(url=u) for u in urls]})
    res = await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest([pl], now=NOW)
    stats = await archive.archive_listing_images(
        session, res.new_ids, media_settings, httpx.MockTransport(cdn)
    )
    await session.commit()
    assert stats == {"ok": 2, "failed": 3, "skipped": 2}
    imgs = (await session.execute(select(ListingImage).order_by(ListingImage.position))).scalars().all()
    assert [i.archive_status for i in imgs] == [
        "ok",
        "ok",
        "failed",
        "failed",
        "failed",
        "skipped",
        "skipped",
    ]
    assert imgs[0].local_path == imgs[1].local_path and imgs[0].sha256 == imgs[1].sha256
    assert len(list(tmp_path.rglob("*.jpg"))) == 1
    assert "non più disponibile" in imgs[2].archive_error and "troppo grande" in imgs[3].archive_error
    assert "non immagine" in imgs[4].archive_error and "host non consentito" in imgs[5].archive_error
    # Failed downloads are retried (up to 3 attempts), successful ones never again.
    again = await archive.archive_listing_images(
        session, res.new_ids, media_settings, httpx.MockTransport(cdn)
    )
    assert again == {"ok": 0, "failed": 3, "skipped": 0}


async def test_resolve_refuses_paths_outside_the_media_dir(media_settings, tmp_path) -> None:
    (tmp_path / "ok.jpg").write_bytes(JPEG)
    assert archive.resolve("ok.jpg", media_settings) == (tmp_path / "ok.jpg").resolve()
    assert archive.resolve("../../etc/passwd", media_settings) is None
    assert archive.resolve("missing.jpg", media_settings) is None


async def test_media_endpoint_requires_sign_in(
    client: httpx.AsyncClient, make_listing, media_settings
) -> None:
    from app.db.session import session_scope
    from tests.conftest import register

    pl = make_listing(external_id="777").model_copy(update={"images": [ProviderImage(url=f"{CDN}/z/1.jpeg")]})
    async with session_scope() as s:
        res = await IngestionService(s, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest([pl], now=NOW)
        await archive.archive_listing_images(s, res.new_ids, media_settings, httpx.MockTransport(cdn))
        image_id = (await s.execute(select(ListingImage.id))).scalar_one()
    anonymous = await client.get(f"/api/v1/media/{image_id}")
    assert anonymous.status_code == 401
    await register(client)
    r = await client.get(f"/api/v1/media/{image_id}")
    assert r.status_code == 200 and r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg" and "private" in r.headers["cache-control"]
    detail = (await client.get("/api/v1/items/777")).json()
    assert detail["images"][0]["local_url"] == f"/api/v1/media/{image_id}"
    assert (await client.get("/api/v1/media/999999")).status_code == 404
