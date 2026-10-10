"""Local photo measures are reused for the same photos only; what the retry gives up on and how it spreads its wakes."""

from typing import Any

import pytest
from arq import Retry

from app.vision.analyzer import (
    GAVE_UP_NOTES,
    MAX_TRUNCATED_TRIES,
    PhotoInput,
    VisionDeferred,
    photos_fingerprint,
    stored_measures,
)
from app.vision.types import ImageAnalysis
from app.workers import vision_queue
from app.workers.vision_queue import VISION_MAX_TRIES, gives_up, retry_vision


def photo(position: int, data: bytes | None = b"x", sha: str | None = "aa") -> PhotoInput:
    return PhotoInput(position, f"k{position}", data, sha, "image/jpeg")


def test_the_fingerprint_names_the_uploaded_photos_and_nothing_else() -> None:
    a = photos_fingerprint([photo(0), photo(1)])
    assert a == photos_fingerprint([photo(0), photo(1)])
    assert a != photos_fingerprint([photo(0, sha="bb"), photo(1)])  # another content
    assert a != photos_fingerprint([photo(0), photo(1, data=b"xy")])  # another size
    assert a != photos_fingerprint([photo(0), photo(2)])  # another place in the gallery
    assert a != photos_fingerprint([photo(0)])  # a photo less
    # A photo not uploaded yet is not part of what was measured; a later upload is another set.
    assert photos_fingerprint([photo(0), photo(1), photo(2, data=None)]) == a


def record(**kw: Any) -> dict[str, Any]:
    analysis = ImageAnalysis(analyzer=kw.pop("analyzer", "heuristic"), photos_key="k1", **kw)
    return analysis.model_dump(mode="json")


def test_measures_are_reused_for_the_same_photos_only() -> None:
    assert stored_measures(record(), "k1") is not None
    assert stored_measures(record(), "k2") is None  # other photos
    assert stored_measures({}, "k1") is None and stored_measures(None, "k1") is None
    old = record()
    old.pop("photos_key")
    assert stored_measures(old, "k1") is None  # stored before the key existed: measured again, once


def test_a_model_analysis_is_never_taken_for_local_measures() -> None:
    assert stored_measures(record(analyzer="claude_vision"), "k1") is None


def test_an_earlier_give_up_does_not_follow_the_measures_into_a_new_attempt() -> None:
    note = GAVE_UP_NOTES["refused"]
    stored = record(notes=["altra nota", note], model_gave_up="refused")
    local = stored_measures(stored, "k1")
    assert local is not None and local.model_gave_up is None and local.notes == ["altra nota"]


def test_a_corrupt_record_is_measured_again() -> None:
    bad = record()
    bad["photo_checks"] = "not a list"
    assert stored_measures(bad, "k1") is None


def deferred(reason: str, retry_after: float = 40.0) -> VisionDeferred:
    return VisionDeferred(reason, retry_after, ImageAnalysis(analyzer="heuristic"))


@pytest.mark.parametrize("reason", ["refused", "rejected", "no_photo_sendable"])
def test_these_are_given_up_at_once(reason: str) -> None:
    assert gives_up(deferred(reason), 1)


def test_a_cut_off_answer_is_asked_once_more_not_five_times() -> None:
    assert MAX_TRUNCATED_TRIES == 2 < VISION_MAX_TRIES
    assert not gives_up(deferred("truncated"), 1)
    assert gives_up(deferred("truncated"), 2)


def test_the_other_failures_keep_their_tries() -> None:
    for reason in ("api_error", "connection", "bad_answer", "no_answer"):
        assert not gives_up(deferred(reason), VISION_MAX_TRIES - 1)
        assert gives_up(deferred(reason), VISION_MAX_TRIES)
    assert not gives_up(deferred("rpm"), VISION_MAX_TRIES)  # a cap never uses a try


async def test_retry_reports_that_it_gave_up_and_does_not_raise() -> None:
    assert await retry_vision({"job_try": 1}, "lid", deferred("refused")) is True


async def wait_spread(monkeypatch: pytest.MonkeyPatch, reason: str, retry_after: float) -> float:
    """The upper bound of the random spread added to the wait."""
    seen: list[float] = []

    def uniform(lo: float, hi: float) -> float:
        seen.append(hi)
        return hi

    monkeypatch.setattr(vision_queue.random, "uniform", uniform)
    with pytest.raises(Retry):
        await retry_vision({"job_try": 1}, "lid", deferred(reason, retry_after))
    return seen[0]


async def test_jobs_a_cap_holds_back_are_spread_over_at_least_fifteen_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A window slot frees every ~12 s for dozens of waiting jobs: a quarter of the wait (3 s) would wake them together.
    assert await wait_spread(monkeypatch, "rpm", 12.0) == 15.0
    assert await wait_spread(monkeypatch, "cooldown", 20.0) == 15.0
    assert await wait_spread(monkeypatch, "rpd", 20_000.0) == 30.0  # long waits keep the old bound
    assert await wait_spread(monkeypatch, "api_error", 12.0) == 3.0  # a failed call keeps its small spread
