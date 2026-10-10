"""A photo check held back by a cap costs nothing on each wake; a gallery the model refuses is not asked again.

Measured with the real ``run_vision`` and ``vision_task`` and scripted providers (no real call is made). The local
analysis is replaced by a counter: the point is how many times the decode / OCR / re-encoding work is started.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from arq import Retry
from sqlalchemy import update

from app.ai import limiter as limiter_mod
from app.ai import llm as llm_mod
from app.ai import service
from app.ai.budget import AiBudget
from app.ai.limiter import Admission
from app.ai.llm import AiDeferred, LLMClient
from app.db.models import Listing, ListingImage
from app.db.session import session_scope
from app.vision import analyzer as az
from app.vision.analyzer import GAVE_UP_NOTES, PhotoInput, VisionDeferred
from app.vision.types import ImageAnalysis, PhotoCheck, PhotoQuality
from app.workers import tasks
from app.workers.vision_queue import listings_awaiting_vision, vision_done
from tests.integration.test_ai_budget import FakeAnthropic, text_response
from tests.integration.test_ai_budget import settings as ai_settings
from tests.integration.test_vision_always import ctx_of, listing_with_photos, model_with, run, stored_vision
from tests.unit.test_vision_parse import ANSWER


@pytest.fixture
def work(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Counts the expensive local work: each start of the local analysis and each re-encoding of a gallery."""
    probe = SimpleNamespace(measured=[], prepared=[])

    async def heuristic(self: Any, photos: list[PhotoInput], ctx: dict[str, Any]) -> ImageAnalysis:
        n = len([p for p in photos if p.data])
        probe.measured.append(n)
        return ImageAnalysis(
            analyzer="heuristic",
            photo_quality=PhotoQuality(photo_count=n, analyzed_count=n),
            photo_hashes=[f"{i:016x}" for i in range(n)],
            photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.8) for i in range(n)],
        )

    real = az.prepare_gallery

    def prepare(photos: list[PhotoInput], limits: Any = az.ANTHROPIC_IMAGES) -> Any:
        probe.prepared.append(len(photos))
        return real(photos, limits)

    monkeypatch.setattr(az.HeuristicImageAnalyzer, "analyze", heuristic)
    monkeypatch.setattr(az, "prepare_gallery", prepare)
    return probe


class Closed:
    """A limiter that is at its cap: it says so when asked and when a call is made."""

    async def peek(self, model: str, tier: str) -> Admission:
        return Admission(False, 12.0, "rpm")

    acquire = peek


async def capture_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    async def fake_enqueue(function: str, *args: Any, **kw: Any) -> bool:
        calls.append((function, *args))
        return True

    monkeypatch.setattr(tasks, "enqueue", fake_enqueue)
    return calls


# ------------------------------------------------------------------ a held-back run costs nothing
async def test_a_run_held_by_a_cap_measures_the_photos_once_and_then_costs_nothing(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, work: SimpleNamespace
) -> None:
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    fake = FakeAnthropic(text_response(ANSWER, tokens=(6000, 1500)))
    model_with(monkeypatch, fake)
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: Closed())

    first = await run(listing_id)  # the first run on these photos: measured and stored
    assert isinstance(first, VisionDeferred) and first.reason == "rpm" and first.store and first.changed
    assert work.measured == [3] and work.prepared == []  # and the gallery is not re-encoded for no call
    stored = await stored_vision(listing_id)
    assert stored["analyzer"] == "heuristic" and stored["photo_hashes"] and stored["photos_key"]

    for _ in range(4):  # the job wakes again and again while the cap holds
        again = await run(listing_id)
        assert isinstance(again, VisionDeferred) and again.reason == "rpm" and not again.asked
        assert again.store is False and again.changed is False  # nothing to write: the measures are stored
    assert work.measured == [3] and work.prepared == []  # no decode, no OCR, no re-encoding on any wake
    assert await stored_vision(listing_id) == stored  # and nothing written
    assert fake.calls == []

    monkeypatch.setattr(llm_mod, "get_limiter", limiter_mod.get_limiter)  # the quota is back
    assert await run(listing_id) is True
    assert work.measured == [3]  # the stored measures went to the model: not measured a second time
    assert work.prepared == [3] and len(fake.calls) == 1
    done = await stored_vision(listing_id)
    assert done["analyzer"] == "claude_vision" and done["photo_hashes"] == stored["photo_hashes"]
    assert done["model_gave_up"] is None


async def test_the_first_run_of_a_gallery_is_the_only_one_that_measures_it(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, work: SimpleNamespace
) -> None:
    """Different photos, different measures: a gallery that changed is measured again."""
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    model_with(monkeypatch, FakeAnthropic(text_response(ANSWER)))
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: Closed())
    await run(listing_id)
    await run(listing_id)
    assert work.measured == [3]
    async with session_scope() as s:  # a photo was replaced (its content hash is another one)
        await s.execute(
            update(ListingImage)
            .where(ListingImage.listing_id == listing_id, ListingImage.position == 0)
            .values(sha256="ab" * 32)
        )
    changed = await run(listing_id)
    assert isinstance(changed, VisionDeferred) and changed.store is True
    assert work.measured == [3, 3]


async def test_a_held_run_leaves_a_stored_model_analysis_alone_without_measuring_for_nothing(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, work: SimpleNamespace
) -> None:
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    fake = FakeAnthropic(text_response(ANSWER, tokens=(6000, 1500)))
    model_with(monkeypatch, fake)
    assert await run(listing_id) is True
    before = await stored_vision(listing_id)
    assert before["analyzer"] == "claude_vision" and work.measured == [3]

    # The cache can no longer answer (new prompt version) and the cap is closed: the measures could not be stored
    # over the model analysis anyway, so none are taken.
    monkeypatch.setattr("app.vision.cache.VISION_PROMPT_VERSION", "vision-next")
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: Closed())
    for _ in range(3):
        out = await run(listing_id)
        assert isinstance(out, VisionDeferred) and out.reason == "rpm" and out.changed is False
    assert work.measured == [3] and work.prepared == [3]  # nothing more was decoded, read or re-encoded
    assert await stored_vision(listing_id) == before


async def test_a_run_held_by_an_open_breaker_or_the_spend_budget_costs_nothing_either(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, work: SimpleNamespace
) -> None:
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    fake = FakeAnthropic(text_response(ANSWER))
    llm = model_with(monkeypatch, fake)
    assert await run(listing_id) is not None  # measured (and answered) once
    await stored_vision(listing_id)
    work.measured.clear()
    monkeypatch.setattr("app.vision.cache.VISION_PROMPT_VERSION", "vision-next")
    for _ in range(llm.settings.ai_breaker_failures):
        llm.breaker.failure()  # the provider has been failing: the breaker is open
    out = await run(listing_id)
    assert isinstance(out, VisionDeferred) and out.reason == "breaker_open" and not out.asked
    assert work.measured == [] and len(fake.calls) == 1


async def test_the_real_local_analysis_runs_once_for_a_gallery_held_back_many_times(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Same, with the real decode, hashes and OCR (small synthetic photos): ``_measure`` is the per-photo work."""
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    fake = FakeAnthropic(text_response(ANSWER))
    model_with(monkeypatch, fake)
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: Closed())
    measured: list[int] = []
    real = az._measure

    def counting(data: bytes) -> Any:
        measured.append(len(data))
        return real(data)

    monkeypatch.setattr(az, "_measure", counting)
    for _ in range(4):
        assert isinstance(await run(listing_id), VisionDeferred)
    assert len(measured) == 3  # the three photos, decoded once, on the first wake only
    stored = await stored_vision(listing_id)
    assert stored["analyzer"] == "heuristic" and len(stored["photo_hashes"]) == 3 and stored["photos_key"]

    monkeypatch.setattr(llm_mod, "get_limiter", limiter_mod.get_limiter)
    assert await run(listing_id) is True and len(measured) == 3 and len(fake.calls) == 1
    assert (await stored_vision(listing_id))["photo_hashes"] == stored["photo_hashes"]


# ------------------------------------------------------------------ the real vision_task
async def test_vision_task_commits_the_local_measures_when_the_model_is_held_back(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, work: SimpleNamespace
) -> None:
    """The real task (not a copy of its try/except): the unit of work must commit what ``run_vision`` stored before
    it raised ``VisionDeferred``, or the measures are rolled back and measured again on every wake."""
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    model_with(monkeypatch, FakeAnthropic(text_response(ANSWER)))
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: Closed())
    enqueued = await capture_enqueue(monkeypatch)
    ctx, decr = ctx_of(1)

    with pytest.raises(Retry):
        await tasks.vision_task(ctx, str(listing_id))
    stored = await stored_vision(listing_id)
    assert stored["analyzer"] == "heuristic" and len(stored["photo_hashes"]) == 3
    assert decr == ["arq:retry:vision:abc"]  # nothing was asked: the try is given back
    # The decision is redone with the measures:
    assert enqueued == [("analyze_listing", str(listing_id), True)]

    with pytest.raises(Retry):  # woken again, still held: no measuring, nothing stored, nothing queued again
        await tasks.vision_task(ctx, str(listing_id))
    assert work.measured == [3] and len(enqueued) == 1
    assert await stored_vision(listing_id) == stored


# ------------------------------------------------------------------ a gallery the model refuses
class Scripted(LLMClient):
    """A client whose model answers with a scripted reason (like Gemini's refused / truncated)."""

    def __init__(self, reason: str) -> None:
        cfg = ai_settings()
        super().__init__(cfg, budget=AiBudget(cfg))
        self._client = object()  # type: ignore[assignment]
        self.reason = reason
        self.asked = 0

    async def structured(self, **kw: Any) -> dict[str, Any] | None:
        self.asked += 1
        raise AiDeferred(self.reason, 300.0)


def use(monkeypatch: pytest.MonkeyPatch, llm: LLMClient) -> None:
    monkeypatch.setattr(service, "get_llm", lambda: llm)


async def test_a_refused_gallery_is_asked_once_and_left_with_the_reason_shown(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, work: SimpleNamespace
) -> None:
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    llm = Scripted("refused")
    use(monkeypatch, llm)
    await capture_enqueue(monkeypatch)
    ctx, decr = ctx_of(1)

    await tasks.vision_task(ctx, str(listing_id))  # no Retry: a block comes back for the same photos
    assert llm.asked == 1 and decr == []
    stored = await stored_vision(listing_id)
    # Analysed by the rules: the photos do not count as read by a model.
    assert stored["analyzer"] == "heuristic"
    assert stored["model_gave_up"] == "refused" and GAVE_UP_NOTES["refused"] in stored["notes"]
    async with session_scope() as s:
        listing = await s.get(Listing, listing_id)
        assert listing is not None
        assert vision_done(listing, model=True)  # so the analyses that follow do not queue it again
        assert str(listing_id) not in await listings_awaiting_vision(s, 10)  # nor does the backfill

    # New photos: a fresh start, and a model that now answers. Nothing of the give-up is carried over.
    async with session_scope() as s:
        await s.execute(
            update(ListingImage).where(ListingImage.listing_id == listing_id).values(sha256="cd" * 32)
        )
    model_with(monkeypatch, FakeAnthropic(text_response(ANSWER)))
    assert await run(listing_id) is True
    done = await stored_vision(listing_id)
    assert done["analyzer"] == "claude_vision" and done["model_gave_up"] is None
    assert GAVE_UP_NOTES["refused"] not in done["notes"]


async def test_a_gallery_whose_answer_is_cut_off_is_asked_once_more_and_then_left(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, work: SimpleNamespace
) -> None:
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    llm = Scripted("truncated")
    use(monkeypatch, llm)
    await capture_enqueue(monkeypatch)

    with pytest.raises(Retry):  # the first time it may be a one-off
        await tasks.vision_task(ctx_of(1)[0], str(listing_id))
    # Not marked yet: it will be asked again.
    assert not (await stored_vision(listing_id)).get("model_gave_up")
    await tasks.vision_task(ctx_of(2)[0], str(listing_id))  # the second time it is given up on: no Retry
    assert llm.asked == 2  # not five
    stored = await stored_vision(listing_id)
    assert stored["model_gave_up"] == "truncated" and GAVE_UP_NOTES["truncated"] in stored["notes"]


async def test_a_listing_with_a_model_analysis_is_never_marked_by_a_later_refusal(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, work: SimpleNamespace
) -> None:
    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    model_with(monkeypatch, FakeAnthropic(text_response(ANSWER)))
    assert await run(listing_id) is True
    before = await stored_vision(listing_id)
    monkeypatch.setattr("app.vision.cache.VISION_PROMPT_VERSION", "vision-next")
    use(monkeypatch, Scripted("refused"))
    await capture_enqueue(monkeypatch)
    await tasks.vision_task(ctx_of(1)[0], str(listing_id))  # gives up, and leaves the good record as it is
    assert await stored_vision(listing_id) == before


async def test_the_refusal_is_not_remembered_as_an_answer(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, work: SimpleNamespace
) -> None:
    from app.vision.cache import VisionCacheStore

    listing_id = await listing_with_photos(make_listing, monkeypatch, tmp_path)
    use(monkeypatch, Scripted("refused"))
    await capture_enqueue(monkeypatch)
    await tasks.vision_task(ctx_of(1)[0], str(listing_id))
    assert (await VisionCacheStore().stats())["entries"] == 0
