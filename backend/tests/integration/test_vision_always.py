"""A photo analysis is the model's, or it is not done: a failed or held-back call is retried, never remembered.

Measured with scripted providers (no real call is made): the model answers, refuses, or is held back by a cap.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from arq import Retry
from sqlalchemy import select

from app.ai import limiter as limiter_mod
from app.ai import llm as llm_mod
from app.ai import service
from app.ai.budget import AiBudget
from app.ai.limiter import Admission
from app.ai.llm import LLMClient
from app.core.config import get_settings
from app.db.models import Listing, ListingImage
from app.db.session import session_scope
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.media import archive
from app.vision import analyzer as az
from app.vision.analyzer import PhotoInput, VisionDeferred
from app.vision.cache import VisionCacheStore
from app.vision.types import ImageAnalysis, PhotoCheck, PhotoQuality
from app.workers import tasks, vision_queue
from app.workers.main import _functions
from app.workers.vision_queue import VISION_MAX_TRIES, retry_vision
from tests.conftest import NOW
from tests.integration.test_ai_budget import FakeAnthropic, text_response
from tests.integration.test_ai_budget import settings as ai_settings
from tests.photos import jpeg
from tests.unit.test_vision_parse import ANSWER


@pytest.fixture(autouse=True)
def no_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    async def heuristic(self: Any, photos: list[PhotoInput], ctx: dict[str, Any]) -> ImageAnalysis:
        n = len(photos)
        return ImageAnalysis(
            analyzer="heuristic",
            photo_quality=PhotoQuality(photo_count=n, analyzed_count=n),
            photo_hashes=[f"{i:016x}" for i in range(n)],
            photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.8) for i in range(n)],
        )

    monkeypatch.setattr(az.HeuristicImageAnalyzer, "analyze", heuristic)


REFUSED = SimpleNamespace(
    stop_reason="refusal",
    stop_details=SimpleNamespace(category="x"),
    model="m",
    content=[],
    usage=SimpleNamespace(input_tokens=10, output_tokens=0),
)


async def listing_with_photos(make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> uuid.UUID:
    media = get_settings().model_copy(update={"media_dir": str(tmp_path)})
    monkeypatch.setattr(archive, "get_settings", lambda: media)
    async with session_scope() as s:
        res = await IngestionService(s, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest(
            [make_listing(photos=3, published_days_ago=0.05)], now=NOW
        )
        listing_id = res.new_ids[0]
    async with session_scope() as s:  # the browser uploads the photos
        listing = await s.get(Listing, listing_id)
        assert listing is not None
        keys = (
            (
                await s.execute(
                    select(ListingImage.image_key)
                    .where(ListingImage.listing_id == listing_id)
                    .order_by(ListingImage.position)
                )
            )
            .scalars()
            .all()
        )
        for i, key in enumerate(keys):
            up = await archive.register_upload(s, listing.external_id, key, jpeg(i), "image/jpeg", media)
            assert up.stored, up.error
    return listing_id


def model_with(monkeypatch: pytest.MonkeyPatch, fake: FakeAnthropic) -> LLMClient:
    cfg = ai_settings()
    llm = LLMClient(cfg, budget=AiBudget(cfg))
    llm._client = fake  # type: ignore[assignment]
    monkeypatch.setattr(service, "get_llm", lambda: llm)
    return llm


async def run(listing_id: uuid.UUID) -> VisionDeferred | bool:
    """What ``vision_task`` does: the unit of work commits whatever ``run_vision`` stored before it raised."""
    async with session_scope() as s:
        try:
            return await service.run_vision(s, listing_id)
        except VisionDeferred as exc:
            return exc


async def stored_vision(listing_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope() as s:
        listing = await s.get(Listing, listing_id)
    assert listing is not None
    return dict((listing.identification or {}).get("vision") or {})


async def test_a_refused_call_is_not_cached_not_done_and_asked_again(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    fake = FakeAnthropic(REFUSED, text_response(ANSWER, tokens=(6000, 1500)))
    model_with(monkeypatch, fake)

    first = await run(listing_id)
    assert isinstance(first, VisionDeferred) and first.reason == "no_answer" and first.asked
    local = await stored_vision(listing_id)
    # The local measures are kept (hashes, quality) but are not a photo analysis: still "not checked".
    assert local["analyzer"] == "heuristic" and local["labels"] == []
    assert vision_queue.vision_done(SimpleNamespace(identification={"vision": local}), model=True) is False
    assert (await VisionCacheStore().stats())["entries"] == 0  # the failure is not remembered

    second = await run(listing_id)  # the retry asks the model again, and this time it answers
    assert second is True
    assert len(fake.calls) == 2
    done = await stored_vision(listing_id)
    assert done["analyzer"] == "claude_vision" and done["labels"][0]["type"] == "brand_label"
    assert vision_queue.vision_done(SimpleNamespace(identification={"vision": done}), model=True) is True
    assert (await VisionCacheStore().stats())["entries"] == 1

    third = await run(listing_id)  # a model analysis of the same photos is served from the cache
    assert third is True and len(fake.calls) == 2


async def test_a_cap_holds_the_call_back_without_asking_and_without_using_a_try(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    fake = FakeAnthropic(text_response(ANSWER, tokens=(6000, 1500)))
    model_with(monkeypatch, fake)

    class Closed:
        async def peek(self, model: str, tier: str) -> Admission:
            return Admission(False, 17.0, "rpm")

        acquire = peek

    monkeypatch.setattr(llm_mod, "get_limiter", lambda: Closed())
    held = await run(listing_id)
    assert isinstance(held, VisionDeferred)
    assert (held.reason, held.retry_after, held.asked) == ("rpm", 17.0, False)
    assert fake.calls == []  # the provider was never asked
    assert (await stored_vision(listing_id))["analyzer"] == "heuristic"

    monkeypatch.setattr(llm_mod, "get_limiter", limiter_mod.get_limiter)  # the quota is back
    assert await run(listing_id) is True and len(fake.calls) == 1


async def test_a_failed_call_never_replaces_an_existing_model_analysis(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    fake = FakeAnthropic(text_response(ANSWER, tokens=(6000, 1500)), REFUSED)
    model_with(monkeypatch, fake)
    assert await run(listing_id) is True
    before = await stored_vision(listing_id)
    assert before["analyzer"] == "claude_vision"

    # The photos change (a new prompt version stands in for it): the cache cannot answer, the model refuses.
    monkeypatch.setattr("app.vision.cache.VISION_PROMPT_VERSION", "vision-next")
    failed = await run(listing_id)
    assert isinstance(failed, VisionDeferred) and failed.changed is False
    assert await stored_vision(listing_id) == before  # the good record is untouched, not the local fallback


async def test_a_model_analyzer_that_returns_the_local_fallback_is_a_failure_all_the_same(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)

    class Sloppy(az.ImageAnalyzer):
        name = "claude_vision"

        async def analyze(self, photos: list[PhotoInput], context: dict[str, Any]) -> ImageAnalysis:
            return await az.HeuristicImageAnalyzer().analyze(photos, context)

    monkeypatch.setattr(service, "get_image_analyzer", lambda llm: Sloppy())
    monkeypatch.setattr(service, "get_llm", lambda: SimpleNamespace())
    out = await run(listing_id)
    assert isinstance(out, VisionDeferred) and out.reason == "no_model_analysis"


# ------------------------------------------------------------------ the job: retry, spare the tries, give up
def ctx_of(job_try: int = 1) -> tuple[dict[str, Any], list[str]]:
    decr: list[str] = []

    class Redis:
        async def decr(self, key: str) -> int:
            decr.append(key)
            return 0

    return {"job_try": job_try, "job_id": "vision:abc", "redis": Redis()}, decr


def deferred(reason: str, retry_after: float = 40.0) -> VisionDeferred:
    return VisionDeferred(reason, retry_after, ImageAnalysis(analyzer="heuristic"))


async def test_a_failed_call_is_retried_at_the_time_the_provider_named() -> None:
    ctx, decr = ctx_of(2)
    with pytest.raises(Retry) as e:
        await retry_vision(ctx, "lid", deferred("api_error", 60.0))
    assert 60_000 <= (e.value.defer_score or 0) <= 75_000  # a little spread on top
    assert decr == []  # the model was asked: the try counts


async def test_a_held_back_call_gives_its_try_back_so_a_burst_waiting_for_a_quota_is_not_dropped() -> None:
    ctx, decr = ctx_of(VISION_MAX_TRIES)  # even on the last try
    with pytest.raises(Retry) as e:
        await retry_vision(ctx, "lid", deferred("rpd", 3600.0))
    assert decr == ["arq:retry:vision:abc"] and (e.value.defer_score or 0) >= 3_600_000


async def test_the_wait_is_bounded_so_the_job_does_not_expire_before_it_wakes() -> None:
    ctx, _ = ctx_of()
    with pytest.raises(Retry) as e:
        await retry_vision(ctx, "lid", deferred("rpd", 20 * 3600.0))
    assert (e.value.defer_score or 0) <= 6 * 3600 * 1000 + 31_000


async def test_a_rejected_request_is_not_asked_again_it_cannot_succeed() -> None:
    ctx, decr = ctx_of(1)
    for reason in ("rejected", "no_photo_sendable"):
        await retry_vision(ctx, "lid", deferred(reason, 3600.0))  # no Retry: waiting would change nothing
    assert decr == []


async def test_after_the_last_try_the_listing_is_left_with_its_local_measures() -> None:
    ctx, decr = ctx_of(VISION_MAX_TRIES)
    await retry_vision(ctx, "lid", deferred("refused", 300.0))  # no Retry: given up (and logged)
    assert decr == []


async def test_vision_task_stores_then_retries_and_re_analyses_what_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    async def fake_enqueue(function: str, *args: Any, **kw: Any) -> bool:
        calls.append((function, *args))
        return True

    async def fake_run(db: Any, listing_id: Any) -> bool:
        exc = deferred("rate_limited", 12.0)
        exc.changed = True  # the local measures were stored: the analysis is redone with them
        raise exc

    monkeypatch.setattr(tasks, "enqueue", fake_enqueue)
    monkeypatch.setattr(tasks, "run_vision", fake_run)
    monkeypatch.setattr(tasks, "session_scope", _NoDb)
    lid = str(uuid.uuid4())
    ctx, _ = ctx_of()
    with pytest.raises(Retry):
        await tasks.vision_task(ctx, lid)
    assert calls == [("analyze_listing", lid, True)]


class _NoDb:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *a: Any) -> None:
        return None


def test_the_worker_gives_a_photo_check_several_tries() -> None:
    fn = next(f for f in _functions() if f.name == "vision_task")
    assert fn.max_tries == VISION_MAX_TRIES >= 5


async def test_the_backfill_takes_complete_galleries_that_no_model_has_analysed(
    clean_db: None, make_listing: Any
) -> None:
    from sqlalchemy import update

    from app.workers.vision_queue import listings_awaiting_vision

    async with session_scope() as s:
        res = await IngestionService(s, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest(
            [make_listing(photos=2, published_days_ago=0.05) for _ in range(7)], now=NOW
        )
    never, local, model, partial, no_photos, sold, newest = res.new_ids
    async with session_scope() as s:
        for lid in (never, local, model, sold, newest):
            await s.execute(
                update(ListingImage).where(ListingImage.listing_id == lid).values(local_path="ab/x.jpg")
            )
        await s.execute(  # half of the gallery uploaded
            update(ListingImage)
            .where(ListingImage.listing_id == partial, ListingImage.position == 0)
            .values(local_path="ab/x.jpg")
        )
        await s.execute(  # every photo removed: nothing to check
            update(ListingImage)
            .where(ListingImage.listing_id == no_photos)
            .values(removed_at=NOW, local_path=None)
        )
        for lid, vision in ((local, {"analyzer": "heuristic"}), (model, {"analyzer": "claude_vision"})):
            await s.execute(
                update(Listing).where(Listing.id == lid).values(identification={"vision": vision})
            )
        await s.execute(update(Listing).where(Listing.id == sold).values(status="sold"))
        await s.execute(update(Listing).where(Listing.id == newest).values(first_seen_at=NOW))
        await s.execute(
            update(Listing).where(Listing.id != newest).values(first_seen_at=NOW - timedelta(days=1))
        )
    async with session_scope() as s:
        ids = await listings_awaiting_vision(s, 10)
        assert set(ids) == {str(never), str(local), str(newest)}  # not: model-analysed, partial, none, sold
        assert ids[0] == str(newest)  # newest first
        assert await listings_awaiting_vision(s, 1) == [str(newest)]
