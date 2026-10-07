"""Per-request database and analysis time for API code that does not report it (benchmark helper).

The speed measurement (``extension/e2e/real-speed.e2e.cjs``) reads ``Server-Timing`` from every
FlipFinder response. Code that does not send it yet is measured by serving the same application
through this wrapper, without changing a line of it:

    cd backend && PYTHONPATH=$PWD .venv/bin/uvicorn --factory tests.bench.db_probe:create_app --port 8100

Each HTTP response then carries ``Server-Timing: db;dur=<ms>, analysis;dur=<ms>, total;dur=<ms>``
(left untouched when the application already sets the header) and, with ``FF_DB_PROBE_LOG=<file>``,
one JSON line per request is appended to that file.

* ``db``: wall time inside SQL statements (SQLAlchemy cursor events, round trip and row fetch),
  summed over the request (a context variable scopes it, so concurrent requests do not mix).
* ``analysis``: wall time inside ``AnalysisPipeline.analyze_many`` (its SQL included).
* ``total``: from the request reaching the application to the response headers.
"""

from __future__ import annotations

import json
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

_installed = False


@dataclass
class _Timing:
    db_s: float = 0.0
    statements: int = 0
    analysis_s: float = 0.0
    analysis_depth: int = 0


_current: ContextVar[_Timing | None] = ContextVar("ff_bench_timing", default=None)


def _install_listeners() -> None:
    """SQL timing on every engine (the async engines run their statements on a sync Engine)."""
    global _installed
    if _installed:
        return
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    from app.opportunities.pipeline import AnalysisPipeline

    def before(conn: Any, cursor: Any, statement: Any, params: Any, context: Any, many: bool) -> None:
        conn.info.setdefault("ff_bench_t0", []).append(time.perf_counter())

    def done(conn: Any) -> None:
        starts = conn.info.get("ff_bench_t0")
        if not starts:
            return
        elapsed = time.perf_counter() - starts.pop()
        timing = _current.get()
        if timing is not None:
            timing.db_s += elapsed
            timing.statements += 1

    def after(conn: Any, cursor: Any, statement: Any, params: Any, context: Any, many: bool) -> None:
        done(conn)

    def failed(ctx: Any) -> None:
        if ctx.connection is not None:
            done(ctx.connection)

    event.listen(Engine, "before_cursor_execute", before)
    event.listen(Engine, "after_cursor_execute", after)
    event.listen(Engine, "handle_error", failed)

    original = AnalysisPipeline.analyze_many

    async def timed_analyze_many(self: Any, *args: Any, **kwargs: Any) -> Any:
        timing = _current.get()
        if timing is None or timing.analysis_depth:
            return await original(self, *args, **kwargs)
        timing.analysis_depth += 1
        t0 = time.perf_counter()
        try:
            return await original(self, *args, **kwargs)
        finally:
            timing.analysis_depth -= 1
            timing.analysis_s += time.perf_counter() - t0

    AnalysisPipeline.analyze_many = timed_analyze_many  # type: ignore[method-assign]
    _installed = True


class TimingMiddleware:
    """Outermost ASGI layer: scopes the timing to one request and reports it."""

    def __init__(self, app: Any, log_path: str | None = None) -> None:
        self.app = app
        self.log_path = log_path

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        timing = _Timing()
        token = _current.set(timing)
        started = time.time()
        t0 = time.perf_counter()
        head: dict[str, Any] = {"status": 0, "total_ms": None, "header": "probe"}

        async def send_timed(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                head["status"] = message["status"]
                head["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                headers = list(message.get("headers") or [])
                names = {k.lower() for k, _ in headers}
                if b"server-timing" in names:
                    head["header"] = "app"  # newer code reports it itself
                else:
                    value = (
                        f"db;dur={timing.db_s * 1000:.1f}, analysis;dur={timing.analysis_s * 1000:.1f}, "
                        f"total;dur={head['total_ms']:.1f}"
                    )
                    headers.append((b"server-timing", value.encode()))
                    if (
                        b"access-control-allow-origin" in names
                        and b"access-control-expose-headers" not in names
                    ):
                        headers.append((b"access-control-expose-headers", b"Server-Timing"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_timed)
        finally:
            _current.reset(token)
            if self.log_path:
                self._log(scope, started, t0, timing, head)

    def _log(
        self, scope: dict[str, Any], started: float, t0: float, timing: _Timing, head: dict[str, Any]
    ) -> None:
        line = {
            "at": round(started * 1000, 1),
            "method": scope.get("method"),
            "path": scope.get("path"),
            "status": head["status"],
            "total_ms": head["total_ms"],
            "done_ms": round((time.perf_counter() - t0) * 1000, 1),
            "db_ms": round(timing.db_s * 1000, 1),
            "analysis_ms": round(timing.analysis_s * 1000, 1),
            "statements": timing.statements,
            "server_timing": head["header"],
        }
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
        except OSError:
            pass  # measurement aid: never fails a request


def create_app() -> Any:
    """``uvicorn --factory tests.bench.db_probe:create_app``: the real application, timed."""
    _install_listeners()
    from app.main import app

    return TimingMiddleware(app, os.environ.get("FF_DB_PROBE_LOG") or None)
