"""Decision Q3-B: the browser uploads the photos of the item page the user opened; the server never
downloads them. Covers what the server asks for, what it accepts and when the photo check starts."""

from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.media import archive
from app.workers import vision_queue
from tests.api.test_api import API, seed_deal
from tests.api.test_extension_api import CARD, _paired
from tests.photos import jpeg

URLS = [f"https://images1.vinted.net/t/9300/f800/{n}.jpeg" for n in ("a", "b", "c")]
ITEM = {
    **CARD,
    "url": "https://www.vinted.it/items/9300-polo-ralph-lauren",
    "title": "Polo Ralph Lauren Custom Slim Fit",
    "price": 11,
    "description": "Polo originale, etichetta interna presente, misure in foto.",
    "image_urls": URLS,
}


@pytest.fixture
def media(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    s = get_settings().model_copy(update={"media_dir": str(tmp_path), "image_archive_max_bytes": 200_000})
    monkeypatch.setattr(archive, "get_settings", lambda: s)
    monkeypatch.setattr("app.api.v1.extension.get_settings", lambda: s)
    return s


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_enqueue(function: str, *args: Any, **kw: Any) -> bool:
        calls.append({"function": function, "arg": args[0], **kw})
        return True

    monkeypatch.setattr(vision_queue, "enqueue", fake_enqueue)
    return calls


async def _capture(auth_client: httpx.AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    r = await auth_client.post(f"{API}/capture/item", json={"item": ITEM}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["photos_wanted"]


def put(client: httpx.AsyncClient, headers: dict[str, str], key: str, data: bytes, ctype: str = "image/jpeg"):
    return client.put(
        f"{API}/capture/photos/9300/{key}", content=data, headers={**headers, "content-type": ctype}
    )


async def test_the_capture_says_which_photos_the_server_wants_and_each_upload_narrows_it(
    auth_client: httpx.AsyncClient, make_listing: Any, media: Any, queued: list[dict[str, Any]]
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    wanted = await _capture(auth_client, headers)
    assert [(w["position"], w["url"]) for w in wanted] == list(enumerate(URLS))
    keys = [w["image_key"] for w in wanted]
    assert all(len(k) == 16 for k in keys)

    r = await put(auth_client, headers, keys[0], jpeg(1))
    assert r.status_code == 200 and r.json()["stored"] is True and r.json()["remaining"] == 2
    assert queued == []  # the photo check waits for the whole gallery
    # The same item opened again asks only for what is still missing, and still queues nothing.
    assert [w["image_key"] for w in await _capture(auth_client, headers)] == keys[1:]
    assert queued == []

    await put(auth_client, headers, keys[1], jpeg(2))
    last = await put(auth_client, headers, keys[2], jpeg(3))
    assert last.json()["remaining"] == 0
    detail = (await auth_client.get(f"{API}/items/9300")).json()
    assert len(detail["images"]) == 3
    assert [c["function"] for c in queued] == ["vision_task"]  # exactly one check, after the last photo
    assert await _capture(auth_client, headers) == []  # nothing left to ask for


async def test_a_photo_that_is_not_a_photo_the_server_asked_for_is_refused(
    auth_client: httpx.AsyncClient, make_listing: Any, media: Any, queued: list[dict[str, Any]], tmp_path: Any
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    keys = [w["image_key"] for w in await _capture(auth_client, headers)]

    unknown = await put(auth_client, headers, "0123456789abcdef", jpeg(1))
    assert unknown.status_code == 404
    other_item = await auth_client.put(
        f"{API}/capture/photos/12345/{keys[0]}",
        content=jpeg(1),
        headers={**headers, "content-type": "image/jpeg"},
    )
    assert other_item.status_code == 404
    malformed = await auth_client.put(
        f"{API}/capture/photos/9300/..%2F..%2Fetc", content=jpeg(1), headers=headers
    )
    assert malformed.status_code in (404, 422)
    not_an_image = await put(auth_client, headers, keys[0], b"<html>hello</html>")
    assert not_an_image.status_code == 422 and not_an_image.json()["error"]["code"] == "invalid_photo"
    assert "non decodificabile" in not_an_image.json()["error"]["message"]
    wrong_type = await put(auth_client, headers, keys[0], jpeg(1), "text/html")
    assert wrong_type.status_code == 422
    too_big = await put(auth_client, headers, keys[0], b"\xff\xd8" + b"x" * 300_000)
    assert too_big.status_code == 413 and too_big.json()["error"]["code"] == "photo_too_large"
    assert list(tmp_path.rglob("*.jpg")) == [] and queued == []  # nothing was stored or queued
    assert len(await _capture(auth_client, headers)) == 3  # all photos still wanted


async def test_only_a_paired_extension_can_upload(
    auth_client: httpx.AsyncClient, client: httpx.AsyncClient, make_listing: Any, media: Any
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    keys = [w["image_key"] for w in await _capture(auth_client, headers)]
    anon = httpx.AsyncClient(transport=client._transport, base_url="http://testserver")
    r = await anon.put(
        f"{API}/capture/photos/9300/{keys[0]}", content=jpeg(1), headers={"content-type": "image/jpeg"}
    )
    assert r.status_code == 401
    await anon.aclose()


async def test_uploads_can_be_switched_off(
    auth_client: httpx.AsyncClient, make_listing: Any, media: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    off = media.model_copy(update={"image_archive_enabled": False})
    monkeypatch.setattr(archive, "get_settings", lambda: off)
    assert await _capture(auth_client, headers) == []  # the server asks for nothing


async def test_a_gallery_the_browser_could_only_partly_read_is_checked_when_it_says_it_is_done(
    auth_client: httpx.AsyncClient, make_listing: Any, media: Any, queued: list[dict[str, Any]]
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    keys = [w["image_key"] for w in await _capture(auth_client, headers)]
    done = f"{API}/capture/photos/9300/complete"
    none = await auth_client.post(done, headers=headers)
    assert none.json() == {"photos": 0, "of": 3, "queued": False} and queued == []  # nothing to check
    await put(auth_client, headers, keys[0], jpeg(1))
    await put(auth_client, headers, keys[2], jpeg(3))  # the second one could not be read
    part = await auth_client.post(done, headers=headers)
    assert part.json() == {"photos": 2, "of": 3, "queued": True}
    assert [c["function"] for c in queued] == ["vision_task"]
    assert (await auth_client.post(f"{API}/capture/photos/1234/complete", headers=headers)).status_code == 404
