"""Photo (vision) checks: queued after the capture response, best candidates first."""

import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.workers import vision_queue
from app.workers.main import _cron_jobs, _functions
from app.workers.vision_queue import VISION_HIGH_TOP, queue_vision, vision_order, worth_vision
from tests.api.test_api import API
from tests.api.test_extension_api import CARD, _paired
from tests.integration.test_pipeline import build_market


def _outcome(
    rap: float | None,
    flip: int,
    *,
    uploaded: bool = True,
    vision: bool | str = False,
    trigger: str | None = None,
) -> Any:
    """``vision``: False (never checked), True (checked by the model) or an analyzer name (e.g. "heuristic")."""
    lid = uuid.uuid4()
    analyzer = "claude_vision" if vision is True else vision
    listing = SimpleNamespace(
        images=[SimpleNamespace(url="https://img/1.jpg", local_path="ab/abcd.jpg" if uploaded else None)],
        identification={"vision": {"analyzer": analyzer}} if vision else {},
    )
    result = SimpleNamespace(risk_adjusted_profit=rap, flip=SimpleNamespace(score=flip))
    return SimpleNamespace(listing_id=lid, listing=listing, result=result, trigger=trigger)


def test_order_best_first_and_only_worth_checking() -> None:
    a = _outcome(12.0, 50)
    b = _outcome(30.0, 40)
    c = _outcome(12.0, 70)  # same profit as a, better flip
    d = _outcome(None, 65)  # no estimate but a high flip: last among the kept
    e = _outcome(-5.0, 20)  # not worth it
    f = _outcome(50.0, 80, uploaded=False)  # the browser has not uploaded the photos yet
    g = _outcome(50.0, 80, vision=True)  # already checked
    h = _outcome(50.0, 80)
    h.listing.images.append(SimpleNamespace(url="https://img/2.jpg", local_path=None))  # half uploaded
    order = vision_order([a, b, c, d, e, f, g, h])
    assert order == [str(o.listing_id) for o in (b, c, a, d)]
    assert vision_order([a, b], after_vision=True) == []


def test_always_checks_every_complete_gallery_not_yet_checked_best_first() -> None:
    a = _outcome(12.0, 50)
    b = _outcome(30.0, 40)
    c = _outcome(12.0, 70)
    d = _outcome(None, 65)
    e = _outcome(-5.0, 20)  # not worth it by profit or flip: checked all the same
    f = _outcome(50.0, 80, uploaded=False)  # the browser has not uploaded the photos yet
    g = _outcome(50.0, 80, vision=True)  # already checked by the model
    h = _outcome(50.0, 80)
    h.listing.images.append(SimpleNamespace(url="https://img/2.jpg", local_path=None))  # half uploaded
    removed = _outcome(50.0, 80)  # a removed photo does not hold the gallery back, a missing one does
    removed.listing.images.append(SimpleNamespace(url="https://img/3.jpg", local_path=None, removed_at="x"))
    order = vision_order([a, b, c, d, e, f, g, h, removed], always=True)
    # Profit first, then flip; no estimate after those that have one, as in the gated order.
    assert order == [str(o.listing_id) for o in (removed, b, c, a, e, d)]
    assert vision_order([a, b], after_vision=True, always=True) == []


def test_a_photo_check_means_a_model_analysis_not_the_local_measures() -> None:
    local_only = _outcome(30.0, 80, vision="heuristic")  # measured locally (or the model call failed)
    by_model = _outcome(30.0, 80, vision=True)
    unnamed = _outcome(30.0, 80)
    unnamed.listing.identification = {"vision": {"x": 1}}  # a stored block that names no analyzer
    assert worth_vision(local_only, always=True) is True  # asked again
    assert worth_vision(unnamed, always=True) is True
    assert worth_vision(by_model, always=True) is False
    # Only the analyses that touch the photos ask again: a price change never does.
    assert not worth_vision(_outcome(30.0, 80, vision="heuristic", trigger="price_change"), always=True)
    assert worth_vision(_outcome(30.0, 80, vision="heuristic", trigger="photos"), always=True)
    assert worth_vision(_outcome(30.0, 80, vision=True, trigger="photos"), always=True)  # new photos: again


def test_the_settings_decide_when_always_is_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    def with_settings(**kw: Any) -> None:
        monkeypatch.setattr(vision_queue, "get_settings", lambda: Settings(**kw))

    clear = _outcome(-5.0, 20)
    done_locally = _outcome(50.0, 80, vision="heuristic")
    with_settings(ai_vision_always=True, ai_api_key="k")
    assert worth_vision(clear) is True
    with_settings(ai_vision_always=True, ai_api_key="k", ai_vision_enabled=False)  # photos switched off
    assert worth_vision(clear) is False
    with_settings(ai_vision_always=True)  # no key: no model to ask, no empty jobs
    assert worth_vision(clear) is False
    # Without a model the local measures are all there is, and they are done once: no job per analysis.
    assert worth_vision(done_locally) is False
    with_settings(ai_api_key="k")  # a model, but "always" off: the value-of-information gate stays
    assert worth_vision(clear) is False and worth_vision(done_locally) is True


async def test_queue_top_few_high_then_default_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_enqueue(function: str, *args: Any, **kw: Any) -> bool:
        calls.append({"function": function, "args": args, **kw})
        return True

    monkeypatch.setattr(vision_queue, "enqueue", fake_enqueue)
    ids = [f"id{i}" for i in range(6)]
    assert await queue_vision(ids) == 6
    assert [c["args"][0] for c in calls] == ids
    assert [c["high"] for c in calls] == [True] * VISION_HIGH_TOP + [False] * (6 - VISION_HIGH_TOP)
    assert all(c["function"] == "vision_task" and c["job_id"] == f"vision:{c['args'][0]}" for c in calls)
    # Each job due a moment after the previous one: arq runs them in this order.
    defers = [c["defer_seconds"] or 0 for c in calls]
    assert defers == sorted(defers) and len(set(defers)) == 6


async def test_a_capture_queues_no_photo_check_until_the_browser_has_uploaded_the_photos(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.session import session_scope

    async with session_scope() as s:
        await build_market(s, make_listing)
    calls: list[dict[str, Any]] = []

    async def fake_enqueue(function: str, *args: Any, **kw: Any) -> bool:
        calls.append({"function": function, "arg": args[0], **kw})
        return True

    monkeypatch.setattr(vision_queue, "enqueue", fake_enqueue)
    headers = await _paired(auth_client)
    prices = [20, 9, 60, 14, 11]
    cards = [
        {
            **CARD,
            "url": f"https://www.vinted.it/items/{8200 + i}-polo",
            "title": "Polo Ralph Lauren Custom Slim Fit blu navy M",
            "price": p,
            "image_urls": [f"https://images1.vinted.net/t/{8200 + i}/f800/x.jpeg"],
        }
        for i, p in enumerate(prices)
    ]
    r = await auth_client.post(f"{API}/capture/cards", json={"items": cards}, headers=headers)
    assert r.status_code == 200, r.text
    evals = [e for e in r.json()["evaluations"] if e["risk_adjusted_profit"] is not None]
    assert len([e for e in evals if e["risk_adjusted_profit"] > 0 or (e["flip_score"] or 0) >= 60]) >= 3
    # Promising listings, but the server holds no photo of them (it never downloads from Vinted):
    # there is nothing to check yet. The check is queued when the last photo is uploaded
    # (see ``tests/api/test_photo_upload.py``).
    assert [c for c in calls if c["function"] == "vision_task"] == []


async def test_queue_outage_never_fails_a_capture(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.session import session_scope

    async with session_scope() as s:
        await build_market(s, make_listing)

    async def down(*args: Any, **kw: Any) -> bool:
        raise ConnectionError("redis down")

    monkeypatch.setattr(vision_queue, "enqueue", down)
    headers = await _paired(auth_client)
    card = {
        **CARD,
        "url": "https://www.vinted.it/items/8300-polo",
        "title": "Polo Ralph Lauren Custom Slim Fit blu navy M",
        "price": 9,
        "image_urls": ["https://images1.vinted.net/t/8300/f800/x.jpeg"],
    }
    r = await auth_client.post(f"{API}/capture/cards", json={"items": [card]}, headers=headers)
    assert r.status_code == 200 and r.json()["stored"] == 1


def test_worker_runs_the_evidence_jobs() -> None:
    names = {f.name for f in _functions()}
    assert {
        "sync_price_evidence_task",
        "sync_price_evidence_full_task",
        "refresh_external_prices_task",
    } <= names
    crons = {c.name: c for c in _cron_jobs()}
    sync = crons["cron:sync_price_evidence_task"]
    assert sync.minute == {4, 34} and sync.run_at_startup  # queued at start, never awaited by it
    assert crons["cron:sync_price_evidence_full_task"].hour == 2
    hourly = crons["cron:refresh_external_prices_task"]
    assert hourly.minute == 51 and hourly.hour is None
