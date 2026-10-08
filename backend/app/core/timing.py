"""Per-request server timing for the endpoints the browser extension waits on.

``Server-Timing: db;dur=<ms>, analysis;dur=<ms>, total;dur=<ms>`` on every timed response:

* ``db``: wall time inside SQL statements (SQLAlchemy cursor events: round trip and row fetch),
  summed over the request. A context variable scopes it, so concurrent requests never mix;
  statements run outside a timed request cost one context-variable read.
* ``analysis``: wall time inside the analysis of the captured listings
  (``AnalysisPipeline.analyze_many``, its SQL included), see ``measure_analysis``.
* ``total``: from the request reaching the application to the response headers.

The header is exposed to cross-origin callers (``Access-Control-Expose-Headers``), so the
extension and the speed measurement (``extension/e2e/real-speed.e2e.cjs``) can read it.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TIMED_PATHS = (
    "/capture/cards",
    "/capture/item",
    "/capture/evaluations",
    "/extension/page-stats",
    "/extension/market-cache",
)
_T0_KEY = "ff_timing_t0"


@dataclass
class RequestTiming:
    started: float = field(default_factory=time.perf_counter)
    db_s: float = 0.0
    statements: int = 0
    analysis_s: float = 0.0
    _analysis_depth: int = 0

    def header(self) -> str:
        total = (time.perf_counter() - self.started) * 1000
        return (
            f"db;dur={self.db_s * 1000:.1f}, analysis;dur={self.analysis_s * 1000:.1f}, total;dur={total:.1f}"
        )


_current: ContextVar[RequestTiming | None] = ContextVar("ff_request_timing", default=None)


def current_timing() -> RequestTiming | None:
    return _current.get()


@contextmanager
def timed_request() -> Iterator[RequestTiming]:
    """Scope a timing to the enclosed work (a request, or a benchmark)."""
    timing = RequestTiming()
    token = _current.set(timing)
    try:
        yield timing
    finally:
        _current.reset(token)


@contextmanager
def measure_analysis() -> Iterator[None]:
    """Count the enclosed time as analysis (nested calls are counted once)."""
    timing = _current.get()
    if timing is None or timing._analysis_depth:
        yield
        return
    timing._analysis_depth += 1
    t0 = time.perf_counter()
    try:
        yield
    finally:
        timing._analysis_depth -= 1
        timing.analysis_s += time.perf_counter() - t0


def _before(conn: Any, cursor: Any, statement: Any, params: Any, context: Any, many: bool) -> None:
    if _current.get() is not None:
        conn.info.setdefault(_T0_KEY, []).append(time.perf_counter())


def _done(conn: Any) -> None:
    starts = conn.info.get(_T0_KEY)
    if not starts:
        return
    elapsed = time.perf_counter() - starts.pop()
    timing = _current.get()
    if timing is not None:
        timing.db_s += elapsed
        timing.statements += 1


def _after(conn: Any, cursor: Any, statement: Any, params: Any, context: Any, many: bool) -> None:
    _done(conn)


def _failed(ctx: Any) -> None:
    if ctx.connection is not None:
        _done(ctx.connection)


def install_sql_timing(engine: AsyncEngine) -> None:
    """SQL timing on this engine (async engines run their statements on the sync engine)."""
    target = engine.sync_engine
    if event.contains(target, "before_cursor_execute", _before):
        return
    event.listen(target, "before_cursor_execute", _before)
    event.listen(target, "after_cursor_execute", _after)
    event.listen(target, "handle_error", _failed)


class ServerTimingMiddleware:
    """Pure ASGI (no extra task per request): times the requests to ``TIMED_PATHS``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).endswith(TIMED_PATHS):
            await self.app(scope, receive, send)
            return
        with timed_request() as timing:

            async def send_timed(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = [
                        (k, v)
                        for k, v in message.get("headers") or []
                        if k.lower() not in (b"server-timing", b"access-control-expose-headers")
                    ]
                    headers.append((b"server-timing", timing.header().encode()))
                    headers.append((b"access-control-expose-headers", b"Server-Timing"))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_timed)
