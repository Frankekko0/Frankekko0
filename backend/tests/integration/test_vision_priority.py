"""Photo (vision) checks: queued after the capture response, best candidates first."""

import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.workers import vision_queue
from app.workers.main import _cron_jobs, _functions
from app.workers.vision_queue import VISION_HIGH_TOP, queue_vision, vision_order
from tests.api.test_api import API
from tests.api.test_extension_api import CARD, _paired
from tests.integration.test_pipeline import build_market


def _outcome(rap: float | None, flip: int, *, uploaded: bool = True, vision: bool = False) -> Any:
    lid = uuid.uuid4()
    listing = SimpleNamespace(
        images=[SimpleNamespace(url="https://img/1.jpg", local_path="ab/abcd.jpg" if uploaded else None)],
        identification={"vision": {"x": 1}} if vision else {},
    )
    result = SimpleNamespace(risk_adjusted_profit=rap, flip=SimpleNamespace(score=flip))
    return SimpleNamespace(listing_id=lid, listing=listing, result=result)


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
