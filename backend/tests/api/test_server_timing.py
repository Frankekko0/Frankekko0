"""Server-Timing on the endpoints the extension waits on (db, analysis, total), scoped per request."""

import asyncio
import re

import httpx
from sqlalchemy import text

from app.core.timing import current_timing, measure_analysis, timed_request
from app.db.session import get_sessionmaker
from tests.api.test_api import API
from tests.api.test_extension_api import CARD, _paired

TIMING = re.compile(r"^db;dur=([\d.]+), analysis;dur=([\d.]+), total;dur=([\d.]+)$")


def _parse(header: str | None) -> tuple[float, float, float]:
    m = TIMING.match(header or "")
    assert m, header
    db, analysis, total = (float(x) for x in m.groups())
    return db, analysis, total


async def test_capture_endpoints_report_server_timing(auth_client: httpx.AsyncClient) -> None:
    headers = await _paired(auth_client)
    cards = [
        {
            **CARD,
            "url": f"https://www.vinted.it/items/{8100 + i}-polo",
            "title": "Polo Ralph Lauren",
            "price": 9 + i,
        }
        for i in range(3)
    ]
    r = await auth_client.post(f"{API}/capture/cards", json={"items": cards}, headers=headers)
    assert r.status_code == 200, r.text
    db, analysis, total = _parse(r.headers.get("server-timing"))
    assert db > 0 and analysis > 0 and total >= analysis and total >= db
    assert r.headers["access-control-expose-headers"] == "Server-Timing"

    ev = await auth_client.post(f"{API}/capture/evaluations", json={"vinted_ids": ["8100"]}, headers=headers)
    db, analysis, total = _parse(ev.headers.get("server-timing"))
    assert db > 0 and analysis == 0 and total >= db

    item = await auth_client.post(f"{API}/capture/item", json={"item": cards[0]}, headers=headers)
    assert _parse(item.headers.get("server-timing"))[1] > 0
    cache = await auth_client.get(f"{API}/extension/market-cache", headers=headers)
    assert _parse(cache.headers.get("server-timing"))[0] > 0

    # Other endpoints are not timed.
    ping = await auth_client.get(f"{API}/extension/ping", headers=headers)
    assert ping.status_code == 200 and "server-timing" not in ping.headers


async def test_cross_origin_callers_can_read_it(auth_client: httpx.AsyncClient) -> None:
    from app.core.config import get_settings

    origin = get_settings().cors_origins[0]
    headers = {**(await _paired(auth_client)), "Origin": origin}
    r = await auth_client.post(f"{API}/capture/evaluations", json={"vinted_ids": ["1"]}, headers=headers)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    assert "Server-Timing" in r.headers["access-control-expose-headers"]
    assert r.headers["server-timing"].startswith("db;dur=")


async def test_sql_time_is_scoped_per_request(client: httpx.AsyncClient) -> None:
    """Two requests at once: each counts only its own statements."""
    maker = get_sessionmaker()

    async def work(sleep_s: float) -> tuple[float, int, float]:
        with timed_request() as timing:
            async with maker() as s:
                await s.execute(text("SELECT pg_sleep(:s)"), {"s": sleep_s})
                await s.execute(text("SELECT 1"))
                with measure_analysis(), measure_analysis():  # nested: counted once
                    await asyncio.sleep(0.05)
            assert current_timing() is timing
        return timing.db_s, timing.statements, timing.analysis_s

    slow, fast = await asyncio.gather(work(0.3), work(0.0))
    assert current_timing() is None
    assert slow[0] >= 0.3 and fast[0] < 0.2
    assert slow[1] == fast[1] == 2
    assert 0.05 <= slow[2] < 0.2 and 0.05 <= fast[2] < 0.2

    # Outside a timed request nothing is collected (and nothing breaks).
    async with maker() as s:
        assert (await s.execute(text("SELECT 1"))).scalar_one() == 1
