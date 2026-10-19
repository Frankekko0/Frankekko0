# ruff: noqa: F811
"""F8: a paid photo check is queued only where it can change the decision, and never for work that was overtaken.

* the last photo upload used to queue the check without the value-of-information gate: an overpriced item (a clear
  PASS) spent a model request and a free-tier quota unit;
* a job that waited in the queue until its listing was sold, or until it became a repost, still spent a request;
* the worker's timeout (180 s) was shorter than the model client's worst case (90 s x 3 tries), so a slow provider ended
  as a cancellation with no trace.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from arq import Retry
from pydantic import SecretStr
from sqlalchemy import update

from app.core.config import get_settings
from app.db.models import Listing
from app.db.session import session_scope
from app.workers import tasks, vision_queue
from app.workers.main import _functions
from app.workers.vision_queue import (
    vision_budget_seconds,
    vision_job_timeout,
    voi_says_stored,
    worth_queueing,
)
from tests.api.test_api import API, seed_deal
from tests.api.test_extension_api import _paired
from tests.api.test_photo_upload import (  # noqa: F401 - the fixtures of the upload tests
    ITEM,
    media,
    put,
    queued,
)
from tests.photos import jpeg


async def capture_and_upload(
    auth_client: httpx.AsyncClient, headers: dict[str, str], price: float
) -> list[str]:
    item = {**ITEM, "price": price}
    r = await auth_client.post(f"{API}/capture/item", json={"item": item}, headers=headers)
    assert r.status_code == 200, r.text
    keys = [w["image_key"] for w in r.json()["photos_wanted"]]
    for i, key in enumerate(keys):
        assert (await put(auth_client, headers, key, jpeg(i + 1))).status_code == 200
    return keys


async def test_an_overpriced_item_gets_no_paid_check_after_its_last_photo(
    auth_client: httpx.AsyncClient,
    make_listing: Any,
    media: Any,
    queued: list[dict[str, Any]],
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    await capture_and_upload(auth_client, headers, price=400.0)  # many times the market: a clear PASS
    detail = (await auth_client.get(f"{API}/items/9300")).json()
    assert len(detail["images"]) == 3
    assert [
        c for c in queued if c["function"] == "vision_task"
    ] == []  # was: one paid call on a hopeless item


async def test_an_overpriced_item_gets_no_paid_check_when_the_browser_says_it_is_done(
    auth_client: httpx.AsyncClient, make_listing: Any, media: Any, queued: list[dict[str, Any]]
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    r = await auth_client.post(
        f"{API}/capture/item", json={"item": {**ITEM, "price": 400.0}}, headers=headers
    )
    keys = [w["image_key"] for w in r.json()["photos_wanted"]]
    await put(auth_client, headers, keys[0], jpeg(1))  # the other two could not be read
    done = await auth_client.post(f"{API}/capture/photos/9300/complete", headers=headers)
    assert done.json()["photos"] == 1
    assert [c for c in queued if c["function"] == "vision_task"] == []


async def test_a_promising_item_is_still_checked_after_its_last_photo(
    auth_client: httpx.AsyncClient,
    make_listing: Any,
    media: Any,
    queued: list[dict[str, Any]],
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    await capture_and_upload(auth_client, headers, price=11.0)
    assert [c["function"] for c in queued] == ["vision_task"]


async def test_the_explicit_choice_to_check_every_gallery_is_kept(
    auth_client: httpx.AsyncClient,
    make_listing: Any,
    media: Any,
    queued: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    always = get_settings().model_copy(
        update={"ai_vision_always": True, "ai_api_key": SecretStr("k"), "ai_vision_enabled": True}
    )
    monkeypatch.setattr(vision_queue, "get_settings", lambda: always)
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    await capture_and_upload(auth_client, headers, price=400.0)
    assert [c["function"] for c in queued] == ["vision_task"]


async def test_an_item_sold_before_its_photos_arrived_is_not_queued(
    auth_client: httpx.AsyncClient,
    make_listing: Any,
    media: Any,
    queued: list[dict[str, Any]],
) -> None:
    await seed_deal(make_listing)
    headers = await _paired(auth_client)
    r = await auth_client.post(f"{API}/capture/item", json={"item": {**ITEM, "price": 11.0}}, headers=headers)
    keys = [w["image_key"] for w in r.json()["photos_wanted"]]
    async with session_scope() as s:
        await s.execute(update(Listing).where(Listing.external_id == "9300").values(status="sold"))
    for i, key in enumerate(keys):
        await put(auth_client, headers, key, jpeg(i + 1))
    assert [c for c in queued if c["function"] == "vision_task"] == []


def test_the_stored_policy_is_the_one_of_the_live_analysis() -> None:
    worth = {"intelligence": {"voi": {"worth_it": True}}}
    assert voi_says_stored(worth, 10, None) is True
    no = {"intelligence": {"voi": {"worth_it": False}}, "verdict": "PASS", "strong_buy_requirements": []}
    assert (
        voi_says_stored(no, 90, 50) is False
    )  # the value of information decides when the analysis carries it
    buy = {
        "intelligence": {"voi": {"worth_it": False}},
        "verdict": "BUY",
        "strong_buy_requirements": [
            {"code": "label", "met": False},
            {"code": "photos_checked", "met": False},
        ],
    }
    assert voi_says_stored(buy, 70, 5) is True  # a Buy that only the photos keep from being a Strong buy
    buy["strong_buy_requirements"].append({"code": "model", "met": False})
    assert voi_says_stored(buy, 70, 5) is False
    assert voi_says_stored(None, 60, None) is True and voi_says_stored(None, 59, None) is False
    assert voi_says_stored(None, 10, 3) is True and voi_says_stored({}, None, None) is False


async def test_an_unknown_listing_or_one_without_an_analysis_queues_nothing(clean_db: None) -> None:
    assert await worth_queueing([]) == []
    assert await worth_queueing(["00000000-0000-0000-0000-000000000001"]) == []


# ------------------------------------------------------------------ work that was overtaken in the queue
async def test_a_job_whose_listing_is_no_longer_on_sale_spends_no_request(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from app.ai import service
    from tests.integration.test_ai_budget import FakeAnthropic, text_response
    from tests.integration.test_vision_always import listing_with_photos, model_with, run
    from tests.unit.test_vision_parse import ANSWER

    fake = FakeAnthropic(text_response(ANSWER, tokens=(6000, 1500)))
    for status in ("sold", "removed", "to_verify"):
        listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
        model_with(monkeypatch, fake)
        async with session_scope() as s:
            await s.execute(update(Listing).where(Listing.id == listing_id).values(status=status))
        assert await run(listing_id) is False
    assert fake.calls == []  # before: one paid request each
    # ... and a listing on sale still gets its check.
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    assert await run(listing_id) is True and len(fake.calls) == 1
    assert service  # the module under test


async def test_a_repost_of_an_item_whose_photos_a_model_has_read_spends_no_request(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from tests.integration.test_ai_budget import FakeAnthropic, text_response
    from tests.integration.test_vision_always import listing_with_photos, model_with, run
    from tests.unit.test_vision_parse import ANSWER

    root = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    repost = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    fake = FakeAnthropic(text_response(ANSWER, tokens=(6000, 1500)))
    model_with(monkeypatch, fake)
    assert await run(root) is True
    async with session_scope() as s:
        await s.execute(update(Listing).where(Listing.id == repost).values(duplicate_of_id=root))
    assert await run(repost) is False and len(fake.calls) == 1  # only the original was paid for


# ------------------------------------------------------------------ the deadline of the check
def test_the_worker_waits_longer_than_the_model_client_can() -> None:
    s = get_settings()
    anthropic = s.model_copy(update={"ai_provider": "anthropic", "ai_timeout_seconds": 90.0})
    gemini = s.model_copy(update={"ai_provider": "gemini", "ai_timeout_seconds": 90.0})
    worst_client = 90.0 * 3  # the SDK tries three times
    assert vision_budget_seconds(anthropic) > 3 * worst_client  # a gallery read in three requests
    assert vision_budget_seconds(gemini) > 3 * 90.0 and vision_budget_seconds(gemini) < vision_budget_seconds(
        anthropic
    )
    assert vision_job_timeout(anthropic) > vision_budget_seconds(anthropic)
    fn = next(f for f in _functions() if f.name == "vision_task")
    assert fn.timeout_s == vision_job_timeout(get_settings()) > 180  # was a flat 180 s


async def test_a_provider_that_never_answers_ends_as_a_counted_timeout_not_a_silent_cancellation(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from tests.integration.test_vision_always import listing_with_photos

    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)

    async def hang(db: Any, lid: Any) -> bool:
        await asyncio.sleep(30)
        return True

    failures: list[int] = []
    breaker = SimpleNamespace(failure=lambda: failures.append(1))
    monkeypatch.setattr(tasks, "run_vision", hang)
    monkeypatch.setattr(tasks, "vision_budget_seconds", lambda s: 0.05)
    monkeypatch.setattr(tasks, "get_llm", lambda: SimpleNamespace(breaker=breaker))
    with pytest.raises(Retry):
        await tasks.vision_task({"job_try": 1}, str(listing_id))
    assert failures == [1]  # the provider that does not answer is a failing provider
    # The try is counted (the model was asked): after the last try the check is left, not retried for ever.
    from app.vision.analyzer import VisionDeferred
    from app.vision.types import ImageAnalysis
    from app.workers.vision_queue import VISION_MAX_TRIES, gives_up

    last = VisionDeferred("timeout", 60.0, ImageAnalysis(analyzer="heuristic"), store=False)
    assert last.asked and gives_up(last, VISION_MAX_TRIES) and not gives_up(last, 1)
