"""Baseline measurement of the capture -> analysis -> persistence pipeline (benchmark helper).

It replays, against a THROWAWAY database and Redis db, what the browser extension does: a search page of
96 cards sent as 12 + 48 + 36 (``FIRST_CHUNK``/``NEXT_CHUNK`` in ``extension/src/background.js``) to
``POST /capture/cards`` and single item pages to ``POST /capture/item``, through the real ASGI application
(authentication with an extension key, rate limits off, ingestion, analysis, persistence, quick evaluations).
Nothing is sent to Vinted: the listings are synthetic, generated from the seeded market
(``tests/bench/seed_market.py``, run here when the database holds no synthetic market).

The same fixed workload (``--seed``) is replayed in these passes, and the result is ONE JSON object on stdout:

* ``cold_new``       caches emptied (Redis db, comparables pools, catalog, identification engine), new listings
* ``warm_unchanged`` the same captures again, right after (listings known, nothing changed)
* ``warm_new``       harness rows purged, caches kept: the same inputs as new listings with warm caches
* ``price_drop``     a quarter of the listings re-captured cheaper, the rest unchanged
* ``size_curve``     request latency against the number of cards (1..96), warm: fixed cost + cost per card
* ``market_change``  a closet page that brings a sold item, then a normal page (pool cache dropped)
* ``concurrent``     several pages at once (one process, one event loop)
* ``worker_batch``   the worker's ``analyze_batch`` (nightly re-analysis path) grouped vs arbitrary order
* ``ai_queue``       real arq worker + cron, the AI model replaced by a fixed-latency fake: queue wait
* ``read_side``      feed / stats / market-cache / page-stats / evaluations, cache miss vs hit

Per pass: per-stage time (exclusive, with the SQL time inside it), requests and listings, median/p95 latency,
listings per minute, time to the first analysed listing, SQL statements and DB time, CPU time, event-loop
stalls, repeated computations (same listing analysed again with unchanged inputs).

The AI is never called: the capture path has no model call; ``ai_queue`` replaces ``LLMClient.structured`` with
a fixed-latency fake so its cost is excluded and only the waiting is measured.

Usage (from backend/):

    PYTHONPATH=$PWD python -m tests.bench.measure_pipeline \\
        --database-url postgresql+asyncpg://flipfinder:flipfinder@localhost:5432/flipfinder_bench2 \\
        --redis-url redis://localhost:6379/9 [--out /tmp/audit_perf/baseline.json]

Refuses a database whose name does not look like a throwaway one (see ``seed_market.THROWAWAY_MARKERS``),
one holding purchases or sales, and Redis db 0. It deletes only its own listings (Vinted ids
``BENCH_ID_LO`` .. ``BENCH_ID_HI``) and flushes the given Redis db.
"""

# Table names in the f-strings below are constants of this file; ``git`` is read for the report only.
# ruff: noqa: S608, S607, ASYNC221
from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import json
import math
import os
import random
import re
import resource
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

# Vinted ids of the harness's own captures (the seeded market uses 9_000_000_000_000_001 ..).
BENCH_ID_LO = "9000000100000000"
BENCH_ID_HI = "9000000200000000"
BENCH_EMAIL = "bench-measure@example.invalid"
THROWAWAY_MARKERS = ("bench", "baseline", "speed", "test", "e2e", "tmp", "throwaway", "synthetic")
FIRST_CHUNK, NEXT_CHUNK, CARDS_PER_PAGE = 12, 48, 96  # extension/src/background.js, a Vinted page


def log(*a: Any) -> None:
    print(*a, file=sys.stderr, flush=True)


# ----------------------------------------------------------------------------------------- statistics
def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def dist(values: list[float], nd: int = 1) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "p50": round(pct(values, 0.5) or 0, nd),
        "p95": round(pct(values, 0.95) or 0, nd),
        "max": round(max(values), nd),
        "mean": round(statistics.fmean(values), nd),
    }


# ------------------------------------------------------------------------------------------- the probe
_PARAMS = re.compile(r"(\$\d+)(\s*,\s*\$\d+)+")
_MANY = re.compile(r"\(\s*\$\d+(?:\s*,\s*\$\d+)*\s*\)(\s*,\s*\(\s*\$\d+(?:\s*,\s*\$\d+)*\s*\))+")


def fingerprint(sql: str) -> str:
    s = " ".join(sql.split())
    s = _MANY.sub("(...)xN", s)
    s = _PARAMS.sub("$n,..", s)
    s = re.sub(r"\$\d+", "$n", s)
    return s if len(s) <= 220 else s[:110] + " ... " + s[-100:]


@dataclass
class Frame:
    name: str
    t0: float
    child: float = 0.0
    db: float = 0.0
    stmts: int = 0


@dataclass
class StageAcc:
    excl: float = 0.0
    incl: float = 0.0
    calls: int = 0
    db: float = 0.0
    stmts: int = 0


@dataclass
class Req:
    label: str
    kind: str
    n: int
    wall: float = 0.0
    ttfb: float = 0.0
    cpu: float = 0.0
    db: float = 0.0
    stmts: int = 0
    many_sets: int = 0
    status: int = 0
    server_timing: str = ""
    resp_bytes: int = 0
    scored: int = 0
    stages: dict[str, StageAcc] = field(default_factory=dict)
    counters: dict[str, float] = field(default_factory=dict)
    t_start: float = 0.0
    t_end: float = 0.0
    top_incl: float = 0.0
    body: Any = None
    statements: dict[str, list[float]] = field(default_factory=dict)  # fingerprint -> [calls, seconds, sets]

    def count(self, key: str, by: float = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + by


class Probe:
    """One request at a time (the harness is sequential): a stack of stages, SQL attributed to the top one."""

    def __init__(self) -> None:
        self.stack: list[Frame] = []
        self.rec: Req | None = None
        self.enabled = True

    @contextmanager
    def stage(self, name: str) -> Any:
        if self.rec is None:
            yield
            return
        frame = Frame(name, time.perf_counter())
        self.stack.append(frame)
        try:
            yield
        finally:
            self.stack.pop()
            inc = time.perf_counter() - frame.t0
            rec = self.rec
            if rec is not None:
                acc = rec.stages.setdefault(name, StageAcc())
                acc.excl += inc - frame.child
                acc.incl += inc
                acc.calls += 1
                acc.db += frame.db
                acc.stmts += frame.stmts
            if self.stack:
                self.stack[-1].child += inc
            elif rec is not None:
                rec.top_incl += inc

    def sql(self, statement: str, seconds: float, many: int = 0, stmts: int = 1) -> None:
        rec = self.rec
        if rec is None:
            return
        rec.db += seconds
        rec.stmts += stmts
        rec.many_sets += many
        if self.stack:
            self.stack[-1].db += seconds
            self.stack[-1].stmts += stmts
        fp = fingerprint(statement)
        e = rec.statements.setdefault(fp, [0, 0.0, 0])
        e[0] += stmts
        e[1] += seconds
        e[2] += many


PROBE = Probe()


def wrap(owner: Any, attr: str, stage: str) -> None:
    """Replace ``owner.attr`` (sync or async function/method) by a timed one; idempotent."""
    orig = getattr(owner, attr)
    if getattr(orig, "_ff_wrapped", False):
        return
    if asyncio.iscoroutinefunction(orig):

        @functools.wraps(orig)
        async def awrapper(*a: Any, **k: Any) -> Any:
            with PROBE.stage(stage):
                return await orig(*a, **k)

        awrapper._ff_wrapped = True  # type: ignore[attr-defined]
        setattr(owner, attr, awrapper)
    else:

        @functools.wraps(orig)
        def swrapper(*a: Any, **k: Any) -> Any:
            with PROBE.stage(stage):
                return orig(*a, **k)

        swrapper._ff_wrapped = True  # type: ignore[attr-defined]
        setattr(owner, attr, swrapper)


# ---------------------------------------------------------------------------------------- CLI + guards
def throwaway_name(url: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(url).database or ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--database-url", required=True, help="postgresql+asyncpg://... of a THROWAWAY database")
    p.add_argument("--redis-url", required=True, help="redis://host:port/N of a throwaway Redis db (flushed)")
    p.add_argument("--out", default=None, help="also write the JSON here")
    p.add_argument("--seed", type=int, default=11, help="workload seed (same seed = same listings)")
    p.add_argument("--pages", type=int, default=4, help="search pages of 96 cards per pass")
    p.add_argument("--items", type=int, default=24, help="item pages per pass")
    p.add_argument("--curve-reps", type=int, default=3, help="repetitions per size of the size curve")
    p.add_argument("--concurrency", type=int, default=4, help="simultaneous pages in the concurrent pass")
    p.add_argument("--worker-listings", type=int, default=160, help="listings of the worker_batch pass")
    p.add_argument("--queue-seconds", type=int, default=75, help="length of the ai_queue pass (0 = skip)")
    p.add_argument("--ai-latency", type=float, default=0.8, help="fixed latency of the fake model call (s)")
    p.add_argument("--only", default=None, help="comma-separated passes to run (default: all)")
    p.add_argument("--per-segment", type=int, default=40, help="seed_market listings per segment if seeding")
    p.add_argument("--top-statements", type=int, default=8)
    p.add_argument("--full", action="store_true", help="keep every stage and statement in the JSON")
    p.add_argument(
        "--populate",
        action="store_true",
        help="first analyse every active synthetic listing (so the read side and the re-analysis run on a "
        "populated opportunities table); about a minute and a half on 4 400 listings",
    )
    p.add_argument(
        "--profile", default=None, help="also cProfile this pass (e.g. warm_new): top functions in the JSON"
    )
    args = p.parse_args(argv)
    name = throwaway_name(args.database_url)
    if not any(m in name.lower() for m in THROWAWAY_MARKERS):
        sys.exit(
            f"Refused: database '{name}' does not look throwaway (its name must contain one of "
            f"{', '.join(THROWAWAY_MARKERS)}). The harness deletes listings and flushes Redis."
        )
    from urllib.parse import urlparse

    db_index = urlparse(args.redis_url).path.strip("/") or "0"
    if db_index == "0":
        sys.exit("Refused: Redis db 0 is not a throwaway db (use e.g. redis://localhost:6379/9).")
    return args


def prepare_environment(args: argparse.Namespace) -> None:
    """The application reads its settings from the environment: point them at the throwaway services."""
    os.environ["DATABASE_URL"] = args.database_url
    os.environ["REDIS_URL"] = args.redis_url
    os.environ["ENVIRONMENT"] = "development"
    os.environ["AI_API_KEY"] = ""
    os.environ["IMAGE_ARCHIVE_ENABLED"] = "false"
    os.environ["LOG_LEVEL"] = "ERROR"
    os.environ["LOG_JSON"] = "false"
    os.environ.setdefault("JWT_SECRET", "bench-secret-bench-secret-bench-secret-0123456789")
    os.environ.setdefault("MARKETPLACE_PROVIDER", "none")


# ------------------------------------------------------------------------------------------ workload
@dataclass
class Workload:
    pages: list[list[dict[str, Any]]]  # search pages: card payloads
    items: list[dict[str, Any]]  # item-page payloads
    curve: dict[int, list[list[dict[str, Any]]]]  # size -> groups of card payloads
    closet: list[dict[str, Any]]  # closet page with sold items
    after_closet: list[dict[str, Any]]  # a normal page right after it
    concurrent: list[list[dict[str, Any]]]  # pages for the concurrent pass
    mc_groups: list[list[dict[str, Any]]] = field(default_factory=list)
    diverse: list[dict[str, Any]] = field(
        default_factory=list
    )  # cards of many brands (a mixed feed / favourites)
    worker_ids: int = 0
    n_listings: int = 0


def _vid(counter: list[int]) -> str:
    counter[0] += 1
    return str(int(BENCH_ID_LO) + counter[0])


def build_workload(args: argparse.Namespace) -> Workload:
    """Deterministic listings drawn from the seeded segments: ~25% priced as deals, ~60% around the market,
    ~15% above it; search pages cluster by brand like a real search does."""
    from tests.bench import seed_market as sm

    rng = random.Random(args.seed)
    counter = [0]
    segs = sm.segments()
    by_brand: dict[str, list[Any]] = {}
    for s in segs:
        by_brand.setdefault(s.brand_slug, []).append(s)
    brands = sorted(b for b, v in by_brand.items() if len(v) >= 3)
    mult = sm._condition_multipliers()
    now = datetime.now(UTC)

    def card(seg: Any, *, status: str = "active") -> dict[str, Any]:
        cond, label, _ = sm._weighted(rng, sm.CONDITION_MIX)
        fair = seg.median * mult[cond]
        r = rng.random()
        factor = (
            rng.uniform(0.5, 0.8)
            if r < 0.25
            else rng.uniform(0.85, 1.25)
            if r < 0.85
            else rng.uniform(1.3, 1.8)
        )
        price = float(sm._round_price(fair * factor))
        _color, color_it = rng.choice(sm.COLORS)
        size = sm._weighted(rng, sm.SIZE_SYSTEM.get(seg.category, sm.LETTERS))[0]
        size_raw = "Taglia unica" if size == "ONESIZE" else size.removeprefix("EU").removeprefix("W")
        title = sm._title(rng, seg, color_it)
        vid = _vid(counter)
        return {
            "url": f"https://www.vinted.it/items/{vid}-{sm._slug(title)}",
            "title": title,
            "price": price,
            "brand": seg.brand_name,
            "size": size_raw,
            "condition": label,
            "color": color_it,
            "image_urls": [f"https://images1.vinted.net/t/01_{vid}/f800/1.webp"],
            "status": status,
            "favourite_count": int(rng.expovariate(1 / 4)),
            "buyer_protection_fee": round(0.7 + price * 0.05, 2),
            "images_source": "card",
            "_seg": seg.category_name,
        }

    def search_page(n: int, brand: str | None = None) -> list[dict[str, Any]]:
        brand = brand or rng.choice(brands)
        return [card(rng.choice(by_brand[brand])) for _ in range(n)]

    def item_from(c: dict[str, Any]) -> dict[str, Any]:
        i = dict(c)
        seg = i.pop("_seg")
        vid = i["url"].split("/items/")[1].split("-")[0]
        i.update(
            description=(
                f"{i['condition']}, taglia {i['size']}. Indossato poche volte, nessun difetto, "
                f"lavato a basse temperature. Spedisco in 24 ore con corriere tracciato. "
                f"Annuncio sintetico (benchmark): non è un annuncio reale."
            ),
            image_urls=[
                f"https://images1.vinted.net/t/01_{vid}/f800/{k}.webp"
                for k in range(1, 5 + rng.randint(0, 2))
            ],
            category_path=f"Uomo > Abbigliamento > {seg}",
            seller_key="h:" + "".join(rng.choice("0123456789abcdef") for _ in range(24)),
            seller_rating=round(rng.uniform(4.2, 5.0), 1),
            seller_review_count=rng.randint(0, 300),
            shipping_fee=round(rng.choice((2.99, 3.49, 4.29, 5.19)), 2),
            published_at=(now - timedelta(hours=rng.uniform(0.2, 60))).isoformat(),
            published_at_kind="exact",
            view_count=rng.randint(5, 200),
            images_source="item_json",
            source="extension_item",
        )
        return i

    def clean(c: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in c.items() if not k.startswith("_")}

    pages = [[clean(c) for c in search_page(CARDS_PER_PAGE)] for _ in range(args.pages)]
    item_cards = [card(rng.choice(segs)) for _ in range(args.items)]
    items = [item_from(c) for c in item_cards]
    for it in items:
        it.pop("_seg", None)
    sizes = (1, 6, 12, 24, 48, 96)
    curve = {n: [[clean(c) for c in search_page(n)] for _ in range(args.curve_reps)] for n in sizes}
    closet_brand = rng.choice(brands)
    closet = [clean(card(rng.choice(by_brand[closet_brand]), status="sold")) for _ in range(6)] + [
        clean(card(rng.choice(by_brand[closet_brand]))) for _ in range(18)
    ]
    diverse = [clean(card(rng.choice(segs))) for _ in range(args.worker_listings)]
    mc_brand = rng.choice([b for b in brands if b != closet_brand])
    after_closet = [clean(c) for c in search_page(FIRST_CHUNK, mc_brand)]
    mc_groups = [[clean(c) for c in search_page(FIRST_CHUNK, mc_brand)] for _ in range(3)]
    concurrent = [[clean(c) for c in search_page(NEXT_CHUNK)] for _ in range(args.concurrency)]
    w = Workload(pages, items, curve, closet, after_closet, concurrent, mc_groups=mc_groups, diverse=diverse)
    w.n_listings = counter[0]
    return w


def reprice(
    cards: list[dict[str, Any]], rng: random.Random, share: float, cut: tuple[float, float]
) -> list[dict[str, Any]]:
    """The same cards with a share of them cheaper (a seller lowering the price)."""
    out = []
    for c in cards:
        c = dict(c)
        if rng.random() < share:
            c["price"] = round(max(3.0, c["price"] * (1 - rng.uniform(*cut))), 2)
        out.append(c)
    return out


# ------------------------------------------------------------------------------------- application
APP: SimpleNamespace = SimpleNamespace()  # filled by load_application()


def load_application() -> None:
    """Import the application (after the environment is set) and install the probes."""
    import logging

    import httpx
    import structlog
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    from app.api import capture_pipeline as cp
    from app.api.v1 import extension as ext
    from app.api.v1 import listings as lst
    from app.core.cache import Cache
    from app.core.rate_limit import RateLimit
    from app.ingestion import service as ing
    from app.ingestion.catalog import reset_catalog_cache
    from app.main import create_app
    from app.opportunities import engine as eng
    from app.opportunities import pipeline as pl

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    APP.httpx, APP.ext, APP.lst, APP.ing, APP.eng, APP.pl, APP.cp = httpx, ext, lst, ing, eng, pl, cp
    APP.reset_catalog_cache = reset_catalog_cache

    from fastapi import Request

    async def no_limit(self: Any, request: Request) -> None:  # the harness is not a user: no 90/min cap
        return None

    no_limit.__annotations__ = {
        "request": Request,
        "return": None,
    }  # FastAPI reads them (no future-annotations)
    RateLimit.__call__ = no_limit  # type: ignore[method-assign]

    # ---- SQL events (class level: every engine, created later too) -----------------------------
    def before(conn: Any, cursor: Any, statement: Any, params: Any, context: Any, many: bool) -> None:
        conn.info.setdefault("ff_bench_t0", []).append(time.perf_counter())

    def finish(conn: Any, statement: str, many: bool, params: Any) -> None:
        starts = conn.info.get("ff_bench_t0")
        if not starts:
            return
        elapsed = time.perf_counter() - starts.pop()
        PROBE.sql(statement, elapsed, len(params) if many and params is not None else 0)

    def after(conn: Any, cursor: Any, statement: Any, params: Any, context: Any, many: bool) -> None:
        finish(conn, statement, many, params)

    def failed(ctx: Any) -> None:
        if ctx.connection is not None:
            finish(ctx.connection, str(ctx.statement or ""), False, None)

    event.listen(Engine, "before_cursor_execute", before)
    event.listen(Engine, "after_cursor_execute", after)
    event.listen(Engine, "handle_error", failed)

    # ---- stages --------------------------------------------------------------------------------
    for mod in (ext, lst, ing, pl):
        if hasattr(mod, "load_catalog"):
            wrap(mod, "load_catalog", "catalog")
    wrap(ext, "manual_to_provider", "parse_to_provider")
    wrap(ext, "persist_observations", "persist_observations")
    wrap(ext, "vision_order", "vision_order")
    wrap(ext, "_touch_sync", "touch_sync")
    wrap(ext, "quick_evaluations", "quick_evaluations")
    wrap(ext, "photos_wanted", "photos_wanted")
    wrap(ext, "analysis_summary", "analysis_summary")
    wrap(Cache, "bump", "cache_bump")
    wrap(lst, "inputs_before", "inputs_before")
    wrap(lst, "prepare_pools", "prepare_pools")
    wrap(ing.IngestionService, "ingest", "ingest")
    for name, stage in (
        ("_upsert_sellers", "ingest.sellers"),
        ("_last_snapshots", "ingest.last_snapshots"),
        ("_detect_duplicates", "ingest.dedup"),
        ("_upsert_products", "ingest.products"),
        ("_record_sales", "ingest.record_sales"),
    ):
        wrap(ing.IngestionService, name, stage)
    wrap(ing, "identify_listing", "identify")
    wrap(ing, "listing_columns", "normalise")
    wrap(ing, "sync_images", "ingest.images")
    A = pl.AnalysisPipeline
    wrap(A, "analyze_many", "analyze_many")
    for name, stage in (
        ("deactivate", "an.deactivate"),
        ("sellers_anomalies", "an.seller_anomalies"),
        ("price_histories", "an.price_histories"),
        ("seller_habits", "an.seller_habits"),
        ("segment_priors", "an.priors"),
        ("evidence_state", "an.evidence_state"),
        ("price_evidence", "an.price_evidence"),
        ("_unbranded_candidates", "an.unbranded_candidates"),
        ("record_demand", "an.record_demand"),
        ("persist_many", "persist_many"),
        ("_current_analyses", "persist.current_analyses"),
        ("_copy_comparables", "persist.comparables_copy"),
        ("_fetch_split", "pool.fetch_split"),
    ):
        wrap(A, name, stage)
    wrap(A, "candidate_pool", "pool_load")
    wrap(cp.CapturePipeline, "candidate_pool", "pool_lookup")
    wrap(pl, "run_analysis", "run_analysis")
    for name, stage in (
        ("opportunity_values", "persist.opportunity_values"),
        ("build_blocks", "persist.build_blocks"),
        ("finalize_dossier", "persist.finalize_dossier"),
        ("inputs_hash", "persist.hash"),
        ("result_hash", "persist.hash"),
        ("listing_inputs", "persist.listing_inputs"),
    ):
        wrap(pl, name, stage)
    wrap(A, "subject_context", "an.subject_context")
    wrap(A, "_keep_review", "persist.keep_review")
    wrap(eng, "price_estimate", "market_value")
    for name in (
        "select_comparables",
        "estimate_from_similar",
        "calibrate",
        "analyze_demand",
        "analyze_velocity",
        "build_signals",
        "assess_risk",
        "compute_confidence",
        "compute_completeness",
        "compute_flip_score",
        "build_offer_plan",
        "build_explanation",
        "market_comparison",
        "time_online",
        "evaluate_deal",
        "analyze_text",
        "decide",
        "build_deal_context",
        "build_headline",
        "build_insights",
        "build_dossier",
        "build_provenance",
        "assess_authenticity",
        "sale_probability",
        "profit_scenarios",
        "seller_risk_score",
    ):
        if hasattr(eng, name):
            wrap(eng, name, f"ra.{name}")
    wrap(eng.RuleBasedDealAnalyst, "analyze_sync", "ra.rule_analyst")
    wrap(eng.intel, "distribution", "ra.monte_carlo")
    wrap(eng.intel, "value_of_analysis", "ra.voi")
    wrap(eng.pm, "premortem", "ra.premortem")
    from sqlalchemy.ext.asyncio import AsyncSession

    wrap(AsyncSession, "commit", "commit")

    # ---- counters for repeated work -------------------------------------------------------------
    orig_needed = lst.analysis_needed

    async def counted_needed(session: Any, result: Any, before_: Any, *a: Any, **k: Any) -> Any:
        touched = len(dict.fromkeys([*result.new_ids, *result.updated_ids]))
        with PROBE.stage("analysis_needed"):
            out = await orig_needed(session, result, before_, *a, **k)
        if PROBE.rec is not None:
            PROBE.rec.count("touched", touched)
            PROBE.rec.count("needed", len(out))
        return out

    lst.analysis_needed = counted_needed

    orig_ingest = ing.IngestionService.ingest

    async def counted_ingest(self: Any, listings: Any, now: Any = None) -> Any:
        res = await orig_ingest(self, listings, now)
        if PROBE.rec is not None:
            PROBE.rec.count("new", len(res.new_ids))
            PROBE.rec.count("updated", len(res.updated_ids))
            PROBE.rec.count("price_changes", len(res.price_changes))
            PROBE.rec.count("status_changes", len(res.status_changes))
        return res

    ing.IngestionService.ingest = counted_ingest  # type: ignore[method-assign]

    orig_persist = A.persist_many

    async def counted_persist(self: Any, items: Any, now: Any, mode: Any = None, trigger: Any = None) -> Any:
        outs = await orig_persist(self, items, now, mode, trigger)
        rec = PROBE.rec
        if rec is not None:
            rec.count("analysed", len(outs))
            rec.count("analysis_records_written", sum(1 for o in outs if o.analysis_created))
            rec.count("identical_recomputed", sum(1 for o in outs if not o.analysis_created))
            rec.count("comparable_rows_written", sum(len(r.comparables) for _, r in items))
            rec.count("opportunities_upserted", len(outs))
        return outs

    A.persist_many = counted_persist  # type: ignore[method-assign]

    orig_split = A._fetch_split

    async def counted_split(self: Any, base: Any, now: Any, limit: int) -> Any:
        out = await orig_split(self, base, now, limit)
        if PROBE.rec is not None:
            PROBE.rec.count("pool_rows_loaded", sum(len(x) for x in out))
        return out

    A._fetch_split = counted_split  # type: ignore[method-assign]

    # ---- the application and its client ----------------------------------------------------------
    fastapi_app = create_app()

    class Edge:
        """Outermost ASGI layer: first response byte time of the request being measured."""

        def __init__(self, app: Any) -> None:
            self.app = app

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            t0 = time.perf_counter()

            async def send2(message: Any) -> None:
                if message["type"] == "http.response.start" and PROBE.rec is not None:
                    PROBE.rec.ttfb = time.perf_counter() - t0
                await send(message)

            await self.app(scope, receive, send2)

    APP.asgi = Edge(fastapi_app)
    APP.client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=APP.asgi), base_url="http://bench", timeout=300.0
    )


# ------------------------------------------------------------------------------- database helpers
async def sql_scalar(sql: str, **params: Any) -> Any:
    from sqlalchemy import text

    from app.db.session import session_scope

    async with session_scope() as s:
        return (await s.execute(text(sql), params)).scalar()


async def sql_exec(sql: str, **params: Any) -> None:
    from sqlalchemy import text

    from app.db.session import session_scope

    async with session_scope() as s:
        await s.execute(text(sql), params)


async def purge_bench_rows() -> int:
    """Delete the harness's own listings (everything hanging off them cascades) and what only they used."""
    from sqlalchemy import text

    from app.db.session import session_scope

    where = "provider = 'vinted' AND external_id >= :lo AND external_id < :hi"
    async with session_scope() as s:
        p = {"lo": BENCH_ID_LO, "hi": BENCH_ID_HI}
        await s.execute(
            text(f"DELETE FROM sold_sales WHERE listing_id IN (SELECT id FROM listings WHERE {where})"), p
        )
        n = (await s.execute(text(f"DELETE FROM listings WHERE {where}"), p)).rowcount
        await s.execute(
            text(
                "DELETE FROM sellers WHERE id NOT IN (SELECT seller_id FROM listings WHERE seller_id IS NOT NULL)"
            )
        )
        await s.execute(text("TRUNCATE external_searches, vision_cache"))
    # Production never deletes listings; the cached comparables pools would still name the purged ones (the
    # comparables COPY has a foreign key to them), so the in-process copies go too. Redis caches stay.
    APP.cp.forget_pools()
    return int(n or 0)


async def reset_caches() -> None:
    """Cold start of everything the API process and Redis remember (not PostgreSQL's buffer cache)."""
    from app.core.redis import get_redis

    await get_redis().flushdb()
    APP.cp.forget_pools()
    APP.reset_catalog_cache()
    APP.ing._engines.clear()
    from app.alerts.service import reset_audience_cache

    reset_audience_cache()


async def ensure_market(args: argparse.Namespace) -> dict[str, Any]:
    """The synthetic market must be there (seed_market, same database)."""
    n = await sql_scalar("SELECT count(*) FROM listings WHERE raw->>'synthetic' = 'true'")
    info: dict[str, Any] = {"synthetic_listings": int(n or 0)}
    if (n or 0) < 1000:
        from tests.bench import seed_market as sm

        log("seeding the synthetic market ...")
        ns = argparse.Namespace(
            database_url=args.database_url,
            per_segment=args.per_segment,
            seed=1,
            now=None,
            redis_url=args.redis_url,
            force=False,
        )
        info["seeded"] = await sm.run(ns)
        info["synthetic_listings"] = int(
            await sql_scalar("SELECT count(*) FROM listings WHERE raw->>'synthetic' = 'true'")
        )
    own = await sql_scalar("SELECT (SELECT count(*) FROM purchases) + (SELECT count(*) FROM sales)")
    if own:
        sys.exit(f"Refused: the database holds {own} purchases/sales (a real user's data).")
    return info


async def populate_opportunities() -> dict[str, Any]:
    """Analyse every active synthetic listing in batches of 40 grouped by brand and category, the way the
    worker's bulk path should (not measured): a realistic opportunities table for the read side."""
    from sqlalchemy import select

    from app.db.models import Listing
    from app.db.session import session_scope
    from app.opportunities.pipeline import AnalysisPipeline

    t0 = time.perf_counter()
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Listing.id, Listing.brand_id, Listing.category_id).where(
                    Listing.status == "active",
                    Listing.external_id < BENCH_ID_LO,
                    Listing.duplicate_of_id.is_(None),
                )
            )
        ).all()
    rows.sort(key=lambda r: (r.brand_id or 0, r.category_id or 0))
    ids = [r.id for r in rows]
    done = 0
    for i in range(0, len(ids), 40):
        async with session_scope() as s:
            done += len(await AnalysisPipeline(s).analyze_many(ids[i : i + 40]))
    return {"analysed": done, "seconds": round(time.perf_counter() - t0, 1)}


async def ensure_user() -> str:
    """A bench user with an extension key (the user is created once, the key is re-issued each run)."""
    from sqlalchemy import delete, select

    from app.core.security import hash_api_key, hash_password, new_api_key
    from app.db.models import ApiKey, User
    from app.db.session import session_scope
    from app.seed import ensure_user_defaults

    async with session_scope() as s:
        user = (await s.execute(select(User).where(User.email == BENCH_EMAIL))).scalar_one_or_none()
        if user is None:
            user = User(
                email=BENCH_EMAIL, password_hash=hash_password("bench-pass-123456"), display_name="Bench"
            )
            s.add(user)
            await s.flush()
            await ensure_user_defaults(s, user)
        await s.execute(delete(ApiKey).where(ApiKey.user_id == user.id))
        key = new_api_key()
        s.add(ApiKey(user_id=user.id, name="bench", prefix=key[:12], key_hash=hash_api_key(key)))
        APP.user_id = user.id
        APP.token_version = user.token_version
    APP.headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    return key


# -------------------------------------------------------------------------------------- the runner
class LoopLag:
    """Event-loop stalls: a task that wants to wake every 2 ms and notes how late it was."""

    def __init__(self) -> None:
        self.lags: list[float] = []
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        while True:
            t = time.perf_counter()
            await asyncio.sleep(0.002)
            self.lags.append(max(0.0, time.perf_counter() - t - 0.002) * 1000)

    def start(self) -> None:
        self.lags = []
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> dict[str, Any]:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        big = [x for x in self.lags if x > 20]
        return {"samples": len(self.lags), **dist(self.lags, 1), "stalls_over_20ms": len(big)}


LAG = LoopLag()


async def post(kind: str, payload: Any, label: str, n: int) -> Req:
    path = "/api/v1/capture/cards" if kind == "cards" else "/api/v1/capture/item"
    body = (
        {"page_type": "catalog", "items": payload, "extension_version": "bench", "parser_version": "bench"}
        if kind == "cards"
        else {
            "item": payload,
            "mode": "extension_item",
            "extension_version": "bench",
            "parser_version": "bench",
        }
    )
    raw = json.dumps(body).encode()
    rec = Req(label, kind, n)
    PROBE.rec = rec
    cpu0 = time.process_time()
    rec.t_start = time.perf_counter()
    resp = await APP.client.post(path, content=raw, headers=APP.headers)
    rec.t_end = time.perf_counter()
    rec.wall = rec.t_end - rec.t_start
    rec.cpu = time.process_time() - cpu0
    PROBE.rec = None
    rec.status = resp.status_code
    rec.resp_bytes = len(resp.content)
    rec.server_timing = resp.headers.get("server-timing", "")
    if resp.status_code == 200:
        data = resp.json()
        evals = data.get("evaluations") if kind == "cards" else [data.get("evaluation")]
        rec.body = data if kind == "item" else None
        rec.scored = sum(1 for e in (evals or []) if e and e.get("flip_score") is not None)
        rec.count("evaluations", len([e for e in (evals or []) if e]))
    else:
        log("request failed", resp.status_code, resp.text[:300])
    return rec


def page_chunks(page: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """How the extension sends a page: a small first request, then 48 cards at a time."""
    chunks = [page[:FIRST_CHUNK]]
    rest = page[FIRST_CHUNK:]
    chunks += [rest[i : i + NEXT_CHUNK] for i in range(0, len(rest), NEXT_CHUNK)]
    return [c for c in chunks if c]


async def run_pages(pages: list[list[dict[str, Any]]], label: str) -> tuple[list[Req], list[float]]:
    """Pages one after the other, chunks one after the other; returns the requests and, for every card,
    how long it waited since its page started (when its chunk's answer came back)."""
    reqs: list[Req] = []
    waits: list[float] = []
    for pi, page in enumerate(pages):
        t_page = time.perf_counter()
        for ci, chunk in enumerate(page_chunks(page)):
            r = await post("cards", chunk, f"{label}/p{pi}c{ci}", len(chunk))
            reqs.append(r)
            waits += [(r.t_end - t_page) * 1000] * len(chunk)
    return reqs, waits


async def run_items(items: list[dict[str, Any]], label: str) -> list[Req]:
    return [await post("item", it, f"{label}/i{i}", 1) for i, it in enumerate(items)]


FULL_OUTPUT = [False]  # --full: every stage and statement in the JSON

# Exclusive stage time summed into the buckets of the question: capture ingest, normalisation + identification,
# comparables / market value, scoring + decision, persistence, response building.
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "1_capture_ingest_persist_observations",
        ("ingest", "ingest.", "inputs_before", "touch_sync", "commit"),
    ),
    ("2_normalisation_identification", ("identify", "normalise")),
    ("3a_comparables_pool_load", ("pool_lookup", "pool_load", "pool.fetch_split", "an.unbranded_candidates")),
    (
        "3b_market_value",
        ("market_value", "ra.select_comparables", "ra.estimate_from_similar", "ra.calibrate"),
    ),
    ("4_scoring_decision", ("run_analysis", "ra.")),
    (
        "5_persist_analysis",
        ("persist_many", "persist.", "an.record_demand"),
    ),
    (
        "6_analysis_context_reads",
        (
            "analyze_many",
            "an.",
            "analysis_needed",
            "prepare_pools",
            "catalog",
        ),
    ),
    ("7_response_quick_evaluations", ("quick_evaluations", "analysis_summary", "photos_wanted")),
    ("8_other_glue", ("parse_to_provider", "persist_observations", "vision_order", "cache_bump")),
)


def stage_group(stage: str) -> str:
    # market-value stages first (they are nested names under ``ra.``)
    for name, prefixes in _GROUPS:
        if name.startswith("3b") and any(stage == p or stage.startswith(p) for p in prefixes):
            return name
    for name, prefixes in _GROUPS:
        if any(stage == p or (p.endswith(".") and stage.startswith(p)) for p in prefixes):
            return name
    return "8_other_glue"


def summarize(
    name: str, reqs: list[Req], waits: list[float], lag: dict[str, Any], top: int, wall: float | None = None
) -> dict[str, Any]:
    ok = [r for r in reqs if r.status == 200]
    listings = sum(r.n for r in ok)
    total_wall = wall if wall is not None else sum(r.wall for r in ok)
    per_listing_amortized = [r.wall * 1000 / r.n for r in ok for _ in range(r.n) if r.n]
    stages: dict[str, StageAcc] = {}
    counters: dict[str, float] = {}
    statements: dict[str, list[float]] = {}
    for r in ok:
        for k, a in r.stages.items():
            t = stages.setdefault(k, StageAcc())
            t.excl += a.excl
            t.incl += a.incl
            t.calls += a.calls
            t.db += a.db
            t.stmts += a.stmts
        for k, v in r.counters.items():
            counters[k] = counters.get(k, 0) + v
        for fp, e in r.statements.items():
            s = statements.setdefault(fp, [0, 0.0, 0])
            s[0] += e[0]
            s[1] += e[1]
            s[2] += e[2]
    other = max(0.0, sum(r.wall - r.top_incl for r in ok))
    L = max(1, listings)
    stage_rows = {
        k: {
            "ms_per_listing": round(a.excl * 1000 / L, 3),
            "db_ms_per_listing": round(a.db * 1000 / L, 3),
            "stmts_per_listing": round(a.stmts / L, 3),
            "calls": a.calls,
            "total_ms": round(a.excl * 1000, 1),
            "inclusive_ms_per_listing": round(a.incl * 1000 / L, 3),
        }
        for k, a in sorted(stages.items(), key=lambda kv: -kv[1].excl)
    }
    groups: dict[str, float] = {}
    group_db: dict[str, float] = {}
    for k, a in stages.items():
        g = stage_group(k)
        groups[g] = groups.get(g, 0.0) + a.excl
        group_db[g] = group_db.get(g, 0.0) + a.db
    groups["framework_auth_serialisation_background"] = (
        groups.get("framework_auth_serialisation_background", 0.0) + other
    )
    total_s = sum(r.wall for r in ok) or 1.0
    stage_group_rows = {
        g: {
            "ms_per_listing": round(v * 1000 / L, 3),
            "share_of_wall": round(v / total_s, 3),
            "db_ms_per_listing": round(group_db.get(g, 0.0) * 1000 / L, 3),
        }
        for g, v in sorted(groups.items(), key=lambda kv: -kv[1])
    }
    if not FULL_OUTPUT[0]:
        stage_rows = {
            k: v
            for k, v in stage_rows.items()
            if v["ms_per_listing"] >= 0.05 or v["db_ms_per_listing"] >= 0.05
        }
    n_req = max(1, len(ok))
    first_chunk = [r for r in ok if r.label.endswith("c0") and r.kind == "cards"]
    stmts = sum(r.stmts for r in ok)
    by_kind: dict[str, Any] = {}
    for kind in sorted({r.kind for r in ok}):
        rs = [r for r in ok if r.kind == kind]
        by_kind[kind] = {
            "requests": len(rs),
            "listings": sum(r.n for r in rs),
            "latency_ms": dist([r.wall * 1000 for r in rs]),
            "ms_per_listing_in_request": dist([r.wall * 1000 / r.n for r in rs if r.n]),
            "statements_per_request": round(sum(r.stmts for r in rs) / len(rs), 1),
        }
    out: dict[str, Any] = {
        "requests": len(reqs),
        "failed_requests": len(reqs) - len(ok),
        "listings": listings,
        "wall_s": round(total_wall, 2),
        "listings_per_minute": round(listings / total_wall * 60, 1) if total_wall else None,
        "by_request_kind": by_kind,
        "request_latency_ms": dist([r.wall * 1000 for r in ok]),
        "time_to_first_byte_ms": dist([r.ttfb * 1000 for r in ok]),
        "latency_per_listing_amortized_ms": dist(per_listing_amortized),
        "listing_wait_since_page_start_ms": dist(waits),
        "time_to_first_analysed_listing_ms": dist([r.wall * 1000 for r in first_chunk])
        if first_chunk
        else None,
        "sql": {
            "statements_per_request": round(stmts / n_req, 1),
            "statements_per_listing": round(stmts / L, 2),
            "executemany_param_sets_per_listing": round(sum(r.many_sets for r in ok) / L, 2),
            "db_ms_per_listing": round(sum(r.db for r in ok) * 1000 / L, 2),
            "db_share_of_wall": round(sum(r.db for r in ok) / max(1e-9, sum(r.wall for r in ok)), 3),
        },
        "cpu_ms_per_listing": round(sum(r.cpu for r in ok) * 1000 / L, 2),
        "cpu_share_of_wall": round(sum(r.cpu for r in ok) / max(1e-9, sum(r.wall for r in ok)), 3),
        "stage_groups": stage_group_rows,
        "stages": stage_rows,
        "not_in_any_stage_ms_per_listing": round(other * 1000 / L, 3),
        "counters": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in sorted(counters.items())},
        "event_loop_lag_ms": lag,
        "response_kb_per_request": round(sum(r.resp_bytes for r in ok) / n_req / 1024, 1),
        "scored_share": round(sum(r.scored for r in ok) / L, 3),
        "top_statements": [
            {
                "calls": int(e[0]),
                "total_ms": round(e[1] * 1000, 1),
                "avg_ms": round(e[1] * 1000 / max(1, e[0]), 2),
                "sql": fp,
            }
            for fp, e in sorted(statements.items(), key=lambda kv: -kv[1][1])[
                : (10_000 if FULL_OUTPUT[0] else top)
            ]
        ],
    }
    if counters.get("touched"):
        out["unchanged_skipped_by_analysis_needed"] = int(counters["touched"] - counters.get("needed", 0))
    return out


async def measured(name: str, coro_factory: Any, args: argparse.Namespace) -> dict[str, Any]:
    profiler = None
    if args.profile == name:
        import cProfile

        profiler = cProfile.Profile()
        profiler.enable()
    LAG.start()
    t0 = time.perf_counter()
    reqs, waits = await coro_factory()
    wall = time.perf_counter() - t0
    lag = await LAG.stop()
    res = summarize(name, reqs, waits, lag, args.top_statements, wall)
    if profiler is not None:
        import pstats

        profiler.disable()
        st = pstats.Stats(profiler)
        rows = []
        for (fn, line, func_name), (_cc, nc, tt, ct, _callers) in st.stats.items():  # type: ignore[attr-defined]
            rows.append(
                (tt, ct, nc, f"{fn.split('site-packages/')[-1].split('backend/')[-1]}:{line}:{func_name}")
            )
        rows.sort(reverse=True)
        res["profile_top_by_own_time"] = [
            {"own_s": round(a, 3), "cum_s": round(b, 3), "calls": c, "where": w} for a, b, c, w in rows[:40]
        ]
        rows.sort(key=lambda r: -r[1])
        res["profile_top_by_cumulative"] = [
            {"own_s": round(a, 3), "cum_s": round(b, 3), "calls": c, "where": w}
            for a, b, c, w in rows[:60]
            if "measure_pipeline" not in w
        ][:35]
    log(
        f"[{name}] {res['listings']} listings in {res['wall_s']}s -> {res['listings_per_minute']}/min, "
        f"p50 {res['request_latency_ms'].get('p50')} ms/request"
    )
    return res


# ------------------------------------------------------------------------------------------ passes
async def pass_main(args: argparse.Namespace, w: Workload, want: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    rng = random.Random(args.seed + 1)

    # process warm-up (imports, SQLAlchemy compiled-statement cache): reported apart, then purged
    warm_cards = w.after_closet[:3]
    first = await post(
        "cards",
        [dict(c, url=c["url"].replace("/items/9", "/items/8")) for c in warm_cards],
        "process-first",
        3,
    )
    out["process_first_request_ms"] = round(first.wall * 1000, 1)
    out["process_first_request_statements"] = first.stmts
    await purge_bench_rows()
    await sql_exec("DELETE FROM listings WHERE external_id LIKE '8000%' AND provider='vinted'")

    async def full(
        label: str, pages: list[list[dict[str, Any]]], items: list[dict[str, Any]]
    ) -> tuple[list[Req], list[float]]:
        r1, w1 = await run_pages(pages, label)
        r2 = await run_items(items, label)
        return r1 + r2, w1

    if "cold_new" in want:
        await purge_bench_rows()
        await reset_caches()
        out["cold_new"] = await measured("cold_new", lambda: full("cold", w.pages, w.items), args)
    if "warm_unchanged" in want:
        out["warm_unchanged"] = await measured("warm_unchanged", lambda: full("warm", w.pages, w.items), args)
    if "warm_new" in want:
        await purge_bench_rows()
        out["warm_new"] = await measured("warm_new", lambda: full("warmnew", w.pages, w.items), args)
        out["warm_new"]["storage_per_captured_listing"] = await row_sizes()
    if "price_drop" in want:
        dropped_pages = [reprice(p, rng, 0.25, (0.10, 0.25)) for p in w.pages]
        dropped_items = reprice(w.items, rng, 0.25, (0.10, 0.25))
        out["price_drop"] = await measured(
            "price_drop", lambda: full("drop", dropped_pages, dropped_items), args
        )
    if "size_curve" in want:
        out["size_curve"] = await pass_curve(args, w)
    if "market_change" in want:
        out["market_change"] = await pass_market_change(args, w)
    if "concurrent" in want:
        out["concurrent"] = await pass_concurrent(args, w)
    if "diverse_new" in want or "worker_batch" in want:
        out["diverse_new"] = await pass_diverse(args, w)
    return out


async def pass_diverse(args: argparse.Namespace, w: Workload) -> dict[str, Any]:
    """Cards of many brands in 48-card requests (a mixed feed, favourites, a closet): each brand needs its own
    comparables pool. The rows stay: ``worker_batch`` re-analyses them."""
    await purge_bench_rows()
    await reset_caches()

    async def go() -> tuple[list[Req], list[float]]:
        reqs = []
        for i in range(0, len(w.diverse), NEXT_CHUNK):
            chunk = w.diverse[i : i + NEXT_CHUNK]
            reqs.append(await post("cards", chunk, f"diverse/{i // NEXT_CHUNK}", len(chunk)))
        return reqs, []

    return await measured("diverse_new", go, args)


async def pass_curve(args: argparse.Namespace, w: Workload) -> dict[str, Any]:
    await purge_bench_rows()
    curve: dict[str, Any] = {}
    xs: list[float] = []
    ys: list[float] = []
    for n, groups in w.curve.items():
        lat = []
        stm = []
        for gi, g in enumerate(groups):
            r = await post("cards", g, f"curve/{n}/{gi}", n)
            if r.status == 200:
                lat.append(r.wall * 1000)
                stm.append(r.stmts)
                xs.append(n)
                ys.append(r.wall * 1000)
        curve[str(n)] = {
            "latency_ms": dist(lat),
            "statements": round(statistics.fmean(stm), 1) if stm else None,
        }
    fit: dict[str, Any] = {}
    if len(set(xs)) > 1:
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sum((x - mx) ** 2 for x in xs)
        fit = {"fixed_ms_per_request": round(my - slope * mx, 1), "ms_per_card": round(slope, 2)}
    return {"by_cards_per_request": curve, "linear_fit": fit}


async def pass_market_change(args: argparse.Namespace, w: Workload) -> dict[str, Any]:
    """A request that brings a sold listing drops every pool (``prepare_pools``); the next page of the same
    brand pays for the reload. Sequence: prime (brand A), A again (pool hit), a closet page of brand B with 6
    sold items, A (pool dropped?), A again."""
    await purge_bench_rows()
    g = w.mc_groups
    prime = await post("cards", g[0], "mc/prime", len(g[0]))
    hit = await post("cards", g[1], "mc/brand-A-pool-warm", len(g[1]))
    closet = await post("cards", w.closet, "mc/closet-6-sold", len(w.closet))
    after = await post("cards", w.after_closet, "mc/brand-A-after-closet", len(w.after_closet))
    again = await post("cards", g[2], "mc/brand-A-again", len(g[2]))

    def row(r: Req) -> dict[str, Any]:
        return {
            "ms": round(r.wall * 1000, 1),
            "statements": r.stmts,
            "pool_lookups": r.stages.get("pool_lookup", StageAcc()).calls,
            "pool_loads": r.stages.get("pool_load", StageAcc()).calls,
            "pool_rows_loaded": r.counters.get("pool_rows_loaded", 0),
        }

    return {
        "prime_12_cards": row(prime),
        "same_brand_pool_warm_12": row(hit),
        "closet_page_6_sold_24_cards": row(closet),
        "same_brand_right_after_closet_12": row(after),
        "same_brand_again_12": row(again),
    }


async def pass_concurrent(args: argparse.Namespace, w: Workload) -> dict[str, Any]:
    """Several 48-card requests at once against one process: do they overlap or queue behind the CPU work?"""
    await purge_bench_rows()
    n = len(w.concurrent)
    seq_t0 = time.perf_counter()
    seq = [await post("cards", c, f"seq/{i}", len(c)) for i, c in enumerate(w.concurrent[:1])]
    one = time.perf_counter() - seq_t0
    await purge_bench_rows()
    LAG.start()
    t0 = time.perf_counter()
    # the probe tracks one request at a time: counters are off here, only wall time is read
    PROBE.rec = None

    async def one_post(i: int, c: list[dict[str, Any]]) -> tuple[float, int]:
        t = time.perf_counter()
        body = json.dumps(
            {"page_type": "catalog", "items": c, "extension_version": "bench", "parser_version": "bench"}
        ).encode()
        resp = await APP.client.post("/api/v1/capture/cards", content=body, headers=APP.headers)
        return time.perf_counter() - t, resp.status_code

    results = await asyncio.gather(*[one_post(i, c) for i, c in enumerate(w.concurrent)])
    wall = time.perf_counter() - t0
    lag = await LAG.stop()
    cards = sum(len(c) for c in w.concurrent)
    return {
        "simultaneous_requests": n,
        "cards_each": len(w.concurrent[0]),
        "single_request_ms": round(one * 1000, 1),
        "all_done_ms": round(wall * 1000, 1),
        "per_request_ms": [round(r[0] * 1000, 1) for r in results],
        "statuses": [r[1] for r in results],
        "speedup_vs_sequential": round(one * n / wall, 2),
        "listings_per_minute": round(cards / wall * 60, 1),
        "event_loop_lag_ms": lag,
        "note": "one process; production runs WEB_CONCURRENCY=2 uvicorn workers",
        "first_request_sequential_ok": all(r.status == 200 for r in seq),
    }


# ----------------------------------------------------------------------------------- worker batch
async def pass_worker_batch(args: argparse.Namespace) -> dict[str, Any]:
    """The worker's ``analyze_batch`` (re-analysis of active opportunities: nightly calibration, start-up, photo
    follow-ups) called directly: cost per listing, grouped by brand/category vs in arbitrary order."""
    import uuid

    from sqlalchemy import select

    from app.db.models import Listing, Opportunity
    from app.db.session import session_scope
    from app.workers import tasks

    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Opportunity.listing_id, Listing.brand_id, Listing.category_id)
                .join(Listing, Listing.id == Opportunity.listing_id)
                .where(Opportunity.is_active.is_(True))
            )
        ).all()
    if len(rows) < 40:
        return {"skipped": f"only {len(rows)} active opportunities"}
    rng = random.Random(args.seed + 2)
    rows = list(rows)
    rng.shuffle(rows)
    rows = rows[: args.worker_listings]
    arbitrary = [
        str(r.listing_id) for r in rows
    ]  # ``fit_price_calibration_task``: select order, chunks of 40
    grouped = [str(r.listing_id) for r in sorted(rows, key=lambda r: (r.brand_id or 0, r.category_id or 0))]
    out: dict[str, Any] = {"listings": len(rows), "batch_size": tasks.ANALYSIS_BATCH_DEFAULT}
    for name, order in (("arbitrary_order", arbitrary), ("grouped_by_brand_category", grouped)):
        await reset_caches()  # the worker is another process: no shared pools
        reqs: list[Req] = []
        LAG.start()
        t0 = time.perf_counter()
        for i in range(0, len(order), tasks.ANALYSIS_BATCH_DEFAULT):
            chunk = order[i : i + tasks.ANALYSIS_BATCH_DEFAULT]
            rec = Req(f"{name}/{i // tasks.ANALYSIS_BATCH_DEFAULT}", "worker", len(chunk))
            PROBE.rec = rec
            cpu0 = time.process_time()
            rec.t_start = time.perf_counter()
            res = await tasks.analyze_batch({"job_try": 1, "queue": "default"}, chunk)
            rec.t_end = time.perf_counter()
            rec.wall = rec.t_end - rec.t_start
            rec.cpu = time.process_time() - cpu0
            PROBE.rec = None
            rec.status = 200
            rec.n = res["analyzed"]
            reqs.append(rec)
        wall = time.perf_counter() - t0
        lag = await LAG.stop()
        s = summarize(name, reqs, [], lag, args.top_statements, wall)
        s.pop("time_to_first_analysed_listing_ms", None)
        out[name] = s
        log(f"[worker_batch/{name}] {s['listings']} listings -> {s['listings_per_minute']}/min")
    a, g = (
        out["arbitrary_order"]["listings_per_minute"],
        out["grouped_by_brand_category"]["listings_per_minute"],
    )
    out["grouped_vs_arbitrary_speedup"] = round(g / a, 2) if a else None
    _ = uuid
    return out


def install_fake_llm(latency: float, calls: list[dict[str, Any]]) -> Any:
    """``LLMClient`` whose ``structured`` waits a fixed time and answers minimally: no network, no cost."""
    from pydantic import SecretStr

    from app.ai import llm as llm_mod
    from app.core.config import get_settings

    settings = get_settings()
    settings.ai_api_key = SecretStr("fake-key-for-the-bench")

    class FakeLLM(llm_mod.LLMClient):
        @property
        def enabled(self) -> bool:  # type: ignore[override]
            return True

        async def structured(self, *a: Any, **k: Any) -> Any:
            content = k.get("content") or []
            calls.append(
                {
                    "purpose": k.get("purpose"),
                    "images": sum(1 for c in content if isinstance(c, dict) and c.get("type") == "image"),
                    "t": time.time(),
                }
            )
            await asyncio.sleep(latency)
            if k.get("purpose") == "vision":
                return {
                    "has_label_photo": False,
                    "defects": [],
                    "labels": [],
                    "photo_roles": [],
                    "photo_findings": [],
                }
            return {"verdict": "CONSIDER", "summary": "fake", "pros": [], "cons": [], "risks": []}

    fake = FakeLLM()
    llm_mod._client = fake
    return fake


def remove_fake_llm() -> None:
    from app.ai import llm as llm_mod
    from app.core.config import get_settings

    llm_mod._client = None
    get_settings().ai_api_key = None


def jpeg_bytes(seed: int, w: int = 200, h: int = 260) -> bytes:
    """A deterministic noise photo (a real JPEG: the server validates what it stores)."""
    from io import BytesIO

    from PIL import Image

    img = Image.frombytes("RGB", (w, h), random.Random(seed).randbytes(w * h * 3))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=70)
    return buf.getvalue()


async def upload_photos(item: dict[str, Any], wanted: list[dict[str, Any]], salt: int = 0) -> int:
    vid = item["url"].split("/items/")[1].split("-")[0]
    n = 0
    for ph in wanted:
        data = jpeg_bytes(int(vid[-6:]) * 100 + ph["position"] + salt)
        r = await APP.client.put(
            f"/api/v1/capture/photos/{vid}/{ph['image_key']}",
            content=data,
            headers={"Authorization": APP.headers["Authorization"], "Content-Type": "image/jpeg"},
        )
        if r.status_code != 200:
            log("photo upload failed", r.status_code, r.text[:200])
        n += r.status_code == 200
    return n


# ------------------------------------------------------------------------------------- vision repeat
async def pass_vision_repeat(args: argparse.Namespace, w: Workload) -> dict[str, Any]:
    """Photos analysed again when nothing about them changed? Four item pages are captured, their photos
    uploaded like the extension does and checked (the vision job of the worker, called directly, with a
    fixed-latency fake model); then the same photos are checked again, the item is captured again unchanged
    and with a lower price, and one photo is swapped. Counted: model calls and images sent, local photo
    measures (decode, hash, OCR), analysis jobs queued after the photo check."""
    import shutil
    import tempfile

    from app.core.config import get_settings
    from app.vision import analyzer as van
    from app.workers import tasks

    settings = get_settings()
    media = tempfile.mkdtemp(prefix="ff_bench_media_")
    settings.image_archive_enabled = True
    settings.media_dir = media
    settings.ai_vision_enabled = True
    calls: list[dict[str, Any]] = []
    install_fake_llm(args.ai_latency, calls)
    local_runs = {"n": 0}
    orig_heur = van.HeuristicImageAnalyzer.analyze

    async def counted_heur(self: Any, photos: Any, context: Any) -> Any:
        local_runs["n"] += 1
        return await orig_heur(self, photos, context)

    van.HeuristicImageAnalyzer.analyze = counted_heur  # type: ignore[method-assign]
    queued_vision: list[list[str]] = []
    orig_qvs = APP.ext.queue_vision_safely

    async def spy_qvs(ids: list[str], *a: Any, **k: Any) -> int:
        if ids:
            queued_vision.append(list(ids))
        return 0  # no worker here: the vision job is run below by hand

    APP.ext.queue_vision_safely = spy_qvs
    enq: list[tuple[str, tuple[Any, ...]]] = []
    orig_enqueue = tasks.enqueue

    async def spy_enqueue(function: str, *a: Any, **k: Any) -> bool:
        enq.append((function, a))
        return True

    tasks.enqueue = spy_enqueue  # type: ignore[assignment]
    await purge_bench_rows()
    items = [dict(it) for it in w.items[:4]]
    out: dict[str, Any] = {"items": len(items), "photos_per_item": [len(i["image_urls"]) for i in items]}
    try:
        lids: list[str] = []
        uploaded = 0
        for it in items:
            r = await post("item", it, "vision/capture", 1)
            wanted = (r.body or {}).get("photos_wanted") or []
            uploaded += await upload_photos(it, wanted)
            lids.append(((r.body or {}).get("evaluation") or {}).get("listing_id"))
        out["photos_uploaded"] = uploaded
        out["vision_jobs_queued_by_last_upload"] = sum(len(x) for x in queued_vision)

        async def vision_runs() -> dict[str, Any]:
            c0, l0, e0 = len(calls), local_runs["n"], len(enq)
            t0 = time.perf_counter()
            for lid in lids:
                await tasks.vision_task({"job_try": 1, "job_id": f"v:{lid}", "redis": None}, lid)
            dt = time.perf_counter() - t0
            return {
                "seconds": round(dt, 2),
                "model_calls": len(calls) - c0,
                "images_sent_to_model": sum(c["images"] for c in calls[c0:]),
                "local_photo_analysis_runs": local_runs["n"] - l0,
                "analysis_jobs_queued_after": sum(1 for f, _ in enq[e0:] if f == "analyze_listing"),
            }

        out["first_check"] = await vision_runs()
        out["second_check_same_photos"] = await vision_runs()

        async def opp_state(lid: str) -> dict[str, Any]:
            from sqlalchemy import text

            async with session_scope() as ss:
                row = (
                    await ss.execute(
                        text(
                            "SELECT l.identification->'vision'->>'analyzer' AS vision, "
                            "l.identification->>'photos_reused_by_other_seller' AS reused, "
                            "o.data_completeness_score AS completeness, o.analysis_coverage_score AS coverage, "
                            "o.risk_score AS risk, o.authenticity_probability AS p_auth, o.flip_score AS flip, "
                            "o.decision_verdict AS verdict FROM listings l JOIN opportunities o "
                            "ON o.listing_id = l.id WHERE l.id = :i"
                        ),
                        {"i": lid},
                    )
                ).one()
            return {k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in row._mapping.items()}

        from app.db.session import session_scope

        # the follow-up analysis the vision job queues (run by hand), then one listing gets the reused-photos signal
        for lid in lids:
            await tasks.analyze_listing({"job_try": 1, "queue": "default"}, lid, True)
        await sql_exec(
            "UPDATE listings SET identification = identification || '{\"photos_reused_by_other_seller\": true}'::jsonb WHERE id = :i",
            i=lids[1],
        )
        await tasks.analyze_listing({"job_try": 1, "queue": "default"}, lids[1], True)
        state_before = [await opp_state(lid) for lid in lids]
        q0 = len(queued_vision)
        again = [await post("item", it, "vision/recapture", 1) for it in items]
        state_after = [await opp_state(lid) for lid in lids]
        out["recapture_unchanged_effect_on_stored_evidence"] = {
            "before": state_before,
            "after_the_item_page_is_opened_again_unchanged": state_after,
            "photo_analysis_lost": sum(
                1 for a, b in zip(state_before, state_after, strict=True) if a["vision"] and not b["vision"]
            ),
            "reused_photos_signal_lost": sum(
                1 for a, b in zip(state_before, state_after, strict=True) if a["reused"] and not b["reused"]
            ),
        }
        out["recapture_unchanged"] = {
            "photos_wanted": sum(len((r.body or {}).get("photos_wanted") or []) for r in again),
            "vision_jobs_queued_by_capture": sum(len(x) for x in queued_vision[q0:]),
            "analysis_identical_recomputed": int(
                sum(r.counters.get("identical_recomputed", 0) for r in again)
            ),
            "request_ms": [round(r.wall * 1000, 1) for r in again],
        }
        q0 = len(queued_vision)
        cheaper = []
        for it in items:
            cheaper.append(
                await post("item", dict(it, price=round(it["price"] * 0.85, 2)), "vision/price", 1)
            )
        out["recapture_price_drop"] = {
            "vision_jobs_queued_by_capture": sum(len(x) for x in queued_vision[q0:]),
            "analysis_records_written": int(
                sum(r.counters.get("analysis_records_written", 0) for r in cheaper)
            ),
        }
        it3 = dict(items[0])
        it3["image_urls"] = ["https://images1.vinted.net/t/01_swapped/f800/9.webp", *it3["image_urls"][1:]]
        r3 = await post("item", it3, "vision/swap", 1)
        wanted3 = (r3.body or {}).get("photos_wanted") or []
        await upload_photos(it3, wanted3, salt=7)
        c0, l0 = len(calls), local_runs["n"]
        await tasks.vision_task({"job_try": 1, "job_id": "v:swap", "redis": None}, lids[0])
        out["one_photo_swapped"] = {
            "photos_changed": len(wanted3),
            "photos_in_gallery": len(it3["image_urls"]),
            "model_calls": len(calls) - c0,
            "images_sent_to_model": sum(c["images"] for c in calls[c0:]),
            "local_photo_analysis_runs": local_runs["n"] - l0,
        }
        out["vision_cache"] = {
            "entries": int(await sql_scalar("SELECT count(*) FROM vision_cache")),
            "hits": int(await sql_scalar("SELECT coalesce(sum(hits),0) FROM vision_cache")),
        }
    finally:
        van.HeuristicImageAnalyzer.analyze = orig_heur  # type: ignore[method-assign]
        APP.ext.queue_vision_safely = orig_qvs
        tasks.enqueue = orig_enqueue  # type: ignore[assignment]
        remove_fake_llm()
        settings.image_archive_enabled = False
        shutil.rmtree(media, ignore_errors=True)
    out["fake_model_latency_s"] = args.ai_latency
    return out


# ------------------------------------------------------------------------------------------- AI queue
async def pass_ai_queue(args: argparse.Namespace) -> dict[str, Any]:
    """Capture items while a real arq worker (high + default queue, the minute cron for the AI sweep) runs in
    this process with a fixed-latency fake instead of the model. Measures the wait between the analysis being
    saved by the capture and the review being written: the capture path never calls ``ai.queue.kick`` (only the
    worker's ``analyze_batch`` does), so it waits for the sweep, which takes ``AI_SWEEP_BATCH`` rows a minute."""
    from arq import cron
    from arq.worker import Worker
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.db.models import Listing, Opportunity
    from app.db.session import session_scope
    from app.workers import tasks
    from app.workers.main import _functions
    from app.workers.queue import QUEUE_DEFAULT, QUEUE_HIGH, redis_settings

    settings = get_settings()
    settings.ai_auto_analyze_min_flip_score = (
        0.0  # every analysed row qualifies: the wait is what is measured
    )
    calls: list[dict[str, Any]] = []
    install_fake_llm(args.ai_latency, calls)
    latency = args.ai_latency
    await purge_bench_rows()
    await reset_caches()

    fns = _functions()
    jobs = [cron(tasks.ai_sweep_task, second={17}, timeout=120, unique=True)]
    kw = dict(redis_settings=redis_settings(), handle_signals=False, max_jobs=4)
    high = Worker(fns, queue_name=QUEUE_HIGH, health_check_interval=3600, **kw)  # type: ignore[arg-type]
    default = Worker(fns, queue_name=QUEUE_DEFAULT, cron_jobs=jobs, health_check_interval=3600, **kw)  # type: ignore[arg-type]
    tasks_run = [asyncio.create_task(high.async_run()), asyncio.create_task(default.async_run())]
    await asyncio.sleep(1.0)

    # a stream of single item captures, spread over the window (the user opens one item every few seconds)
    from tests.bench import seed_market as sm  # noqa: F401  (workload items are reused)

    w = build_workload(args)
    items = w.items[: max(6, min(len(w.items), args.queue_seconds // 6))]
    t_end = time.time() + args.queue_seconds
    captured: dict[str, float] = {}
    gap = max(2.0, (args.queue_seconds - 20) / max(1, len(items)))
    for it in items:
        r = await post("item", it, "queue/item", 1)
        vid = it["url"].split("/items/")[1].split("-")[0]
        captured[vid] = time.time()
        await asyncio.sleep(max(0.0, gap - r.wall))
    await asyncio.sleep(max(0.0, t_end - time.time()) + latency + 3)

    async with session_scope() as s:
        rows = (
            await s.execute(
                select(
                    Listing.external_id,
                    Opportunity.analyzed_at,
                    Opportunity.ai_analyzed_at,
                    Opportunity.ai_for_analysis_id,
                    Opportunity.analysis_id,
                )
                .join(Opportunity, Opportunity.listing_id == Listing.id)
                .where(
                    Listing.provider == "vinted",
                    Listing.external_id >= BENCH_ID_LO,
                    Listing.external_id < BENCH_ID_HI,
                )
            )
        ).all()
    waits = []
    pending_waits = []
    pending = 0
    now_ts = datetime.now(UTC)
    for r in rows:
        if r.external_id in captured:
            if r.ai_for_analysis_id == r.analysis_id and r.ai_analyzed_at is not None:
                waits.append((r.ai_analyzed_at - r.analyzed_at).total_seconds())
            else:
                pending += 1
                pending_waits.append((now_ts - r.analyzed_at).total_seconds())
    for t in tasks_run:
        t.cancel()
    for t in tasks_run:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t
    for wk in (high, default):
        with contextlib.suppress(Exception):
            await wk.close()
    remove_fake_llm()
    from app.core.redis import get_redis

    await get_redis().flushdb()  # jobs still queued must not leak into the next run
    return {
        "fake_model_latency_s": latency,
        "captures": len(items),
        "reviews_done_of_captured": len(waits),
        "reviews_pending_at_end": pending,
        "capture_to_review_s": dist(waits, 1),
        "still_waiting_after_s": dist(pending_waits, 1),
        "sweep_batch_per_minute": settings.ai_sweep_batch,
        "sweep_cron": "second 17 of every minute (workers/main.py _cron_jobs)",
        "model_calls": len(calls),
        "note": "capture_to_review = review written - analysis saved by the capture; the capture path does not "
        "call ai.queue.kick, only the worker's analyze_batch does",
    }


# ------------------------------------------------------------------------------------------------ alerts
async def pass_alerts(args: argparse.Namespace, w: Workload) -> dict[str, Any]:
    """Item pages captured through the API with a catch-all watchlist: how many alerts exist right after the
    captures, and how many after the worker's ``analyze_batch`` runs on the same listings (the only caller of
    ``evaluate_alerts``)."""
    from app.alerts.service import reset_audience_cache
    from app.db.models import Watchlist
    from app.db.session import session_scope
    from app.workers import tasks

    await purge_bench_rows()
    await sql_exec("DELETE FROM alerts")
    await sql_exec("DELETE FROM watchlists WHERE name = 'bench-all'")
    async with session_scope() as s:
        s.add(Watchlist(user_id=APP.user_id, name="bench-all", min_flip_score=0, min_roi=0, min_profit=0))
    reset_audience_cache()
    n = min(len(w.items), 40)
    for it in w.items[:n]:
        await post("item", it, "alerts/item", 1)
    verdicts = await sql_scalar(
        "SELECT string_agg(v || ':' || c, ', ') FROM (SELECT coalesce(decision_verdict, '-') v, count(*) c "
        "FROM opportunities o JOIN listings l ON l.id = o.listing_id "
        "WHERE l.external_id >= :lo AND l.external_id < :hi GROUP BY 1) t",
        lo=BENCH_ID_LO,
        hi=BENCH_ID_HI,
    )
    after_capture = int(await sql_scalar("SELECT count(*) FROM alerts") or 0)
    ids = await sql_scalar(
        "SELECT array_agg(o.listing_id::text) FROM opportunities o JOIN listings l ON l.id = o.listing_id "
        "WHERE l.external_id >= :lo AND l.external_id < :hi",
        lo=BENCH_ID_LO,
        hi=BENCH_ID_HI,
    )
    t0 = time.perf_counter()
    await tasks.analyze_batch({"job_try": 1, "queue": "default"}, list(ids or []))
    batch_s = time.perf_counter() - t0
    after_worker = int(await sql_scalar("SELECT count(*) FROM alerts") or 0)
    await sql_exec("DELETE FROM alerts")
    await sql_exec("DELETE FROM watchlists WHERE name = 'bench-all'")
    reset_audience_cache()
    return {
        "item_captures": n,
        "decision_verdicts": verdicts,
        "alerts_right_after_the_captures": after_capture,
        "alerts_after_worker_analyze_batch_on_the_same_listings": after_worker,
        "worker_batch_seconds": round(batch_s, 2),
        "note": "evaluate_alerts is called only from workers/tasks.py analyze_batch; the API capture path "
        "(persist_observations) never calls it, so an extension-only deployment (MARKETPLACE_PROVIDER=none) "
        "raises alerts only after a photo follow-up or the 05:23 calibration re-analysis",
    }


# ------------------------------------------------------------------------------------- worker queue wait
async def pass_worker_queue(args: argparse.Namespace) -> dict[str, Any]:
    """Queue wait with the real arq workers (high + default, one process like ``workers/main.py``) while the
    bulk re-analysis is queued, as ``fit_price_calibration_task`` does after every start and at 05:23: how long a
    single follow-up analysis (queued on the default queue by ``vision_task``) and a high-queue batch wait."""
    from arq.worker import Worker
    from sqlalchemy import select

    from app.db.models import Opportunity
    from app.db.session import session_scope
    from app.workers import tasks
    from app.workers.main import _functions
    from app.workers.queue import QUEUE_DEFAULT, QUEUE_HIGH, enqueue, redis_settings

    async with session_scope() as s:
        ids = [
            str(x)
            for x in (
                await s.execute(select(Opportunity.listing_id).where(Opportunity.is_active.is_(True)))
            ).scalars()
        ]
    if len(ids) < 400:
        return {"skipped": f"{len(ids)} active opportunities (use --populate)"}
    rng = random.Random(args.seed + 3)
    rng.shuffle(ids)
    n_jobs = 24
    bulk = [ids[i * 40 : (i + 1) * 40] for i in range(n_jobs)]
    probes = ids[n_jobs * 40 : n_jobs * 40 + 8] or ids[:8]
    await reset_caches()
    started: dict[str, float] = {}
    finished: dict[str, float] = {}
    all_bulk_done = asyncio.Event()
    orig_batch, orig_single = tasks.analyze_batch, tasks.analyze_listing

    @functools.wraps(orig_batch)
    async def spy_batch(ctx: dict[str, Any], listing_ids: list[str], after_vision: bool = False) -> Any:
        key = ctx.get("job_id", "?")
        started[key] = time.time()
        try:
            return await orig_batch(ctx, listing_ids, after_vision)
        finally:
            finished[key] = time.time()
            if all(f"bulk{i}" in finished for i in range(n_jobs)):
                all_bulk_done.set()

    @functools.wraps(orig_single)
    async def spy_single(ctx: dict[str, Any], listing_id: str, after_vision: bool = False) -> Any:
        key = ctx.get("job_id", "?")
        started[key] = time.time()
        try:
            return await orig_single(ctx, listing_id, after_vision)
        finally:
            finished[key] = time.time()

    tasks.analyze_batch, tasks.analyze_listing = spy_batch, spy_single  # type: ignore[assignment]
    try:
        fns = _functions()
        kw = dict(
            redis_settings=redis_settings(), handle_signals=False, max_jobs=4, health_check_interval=3600
        )
        high = Worker(fns, queue_name=QUEUE_HIGH, **kw)  # type: ignore[arg-type]
        default = Worker(fns, queue_name=QUEUE_DEFAULT, **kw)  # type: ignore[arg-type]
        runners = [asyncio.create_task(high.async_run()), asyncio.create_task(default.async_run())]
        await asyncio.sleep(0.5)
        t_bulk = time.time()
        for i, chunk in enumerate(bulk):
            await enqueue("analyze_batch", chunk, high=False, job_id=f"bulk{i}")
        await asyncio.sleep(1.0)  # the bulk has started; now the follow-ups arrive
        t_follow = time.time()
        await enqueue("analyze_listing", probes[0], True, job_id="follow-up")  # what vision_task does
        await enqueue("analyze_batch", probes[1:5], high=True, job_id="high-probe")  # a high-queue job
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(all_bulk_done.wait(), 300)
        t_done = time.time()
        for t in runners:
            t.cancel()
        for t in runners:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        for wk in (high, default):
            with contextlib.suppress(Exception):
                await wk.close()
    finally:
        tasks.analyze_batch, tasks.analyze_listing = orig_batch, orig_single  # type: ignore[assignment]
        from app.core.redis import get_redis

        await get_redis().flushdb()

    def wait(key: str, t_enq: float) -> float | None:
        return round(started[key] - t_enq, 2) if key in started else None

    bulk_starts = sorted(started[f"bulk{i}"] - t_bulk for i in range(n_jobs) if f"bulk{i}" in started)
    return {
        "bulk_jobs": n_jobs,
        "listings_per_bulk_job": 40,
        "bulk_total_seconds": round(t_done - t_bulk, 1),
        "bulk_listings_per_minute": round(n_jobs * 40 / (t_done - t_bulk) * 60, 0),
        "bulk_job_start_delay_s": dist(bulk_starts, 2),
        "follow_up_analyze_listing_default_queue": {
            "wait_to_start_s": wait("follow-up", t_follow),
            "finished_s_after_enqueue": round(finished["follow-up"] - t_follow, 2)
            if "follow-up" in finished
            else None,
        },
        "high_queue_batch_of_4": {
            "wait_to_start_s": wait("high-probe", t_follow),
            "finished_s_after_enqueue": round(finished["high-probe"] - t_follow, 2)
            if "high-probe" in finished
            else None,
        },
        "note": "one process runs both workers, max_jobs=4 each, on one event loop: CPU-bound analyses share "
        "the GIL, so a high-queue job also waits for them",
    }


# --------------------------------------------------------------------------------------- read side
async def pass_read_side(args: argparse.Namespace) -> dict[str, Any]:
    """The endpoints the dashboard and the extension read: first call (cache miss) against the repeat (hit), and
    the cost of the invalidation every capture does (``cache.bump(NS_FEED)``)."""
    from sqlalchemy import select

    from app.core.cache import NS_FEED, cache
    from app.core.security import create_access_token
    from app.db.models import Opportunity
    from app.db.session import session_scope

    token = create_access_token(APP.user_id, APP.token_version)
    APP.read_headers = {"Authorization": f"Bearer {token}"}
    cap_user = APP.headers
    out: dict[str, Any] = {}
    cases: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]] = [
        ("feed_default", "/opportunities", {"page_size": 24}, None),
        (
            "feed_sort_profit_min_profit",
            "/opportunities",
            {"page_size": 24, "sort": "profit", "min_profit": 5},
            None,
        ),
        ("feed_preset_strong_buy", "/opportunities", {"page_size": 24, "preset": "strong_buy"}, None),
        ("feed_search_text", "/opportunities", {"page_size": 24, "q": "polo"}, None),
        ("feed_brand_filter", "/opportunities", {"page_size": 24, "brands": "ralph-lauren"}, None),
        ("quick_stats", "/opportunities/stats", None, None),
        ("market_cache_extension", "/extension/market-cache", None, cap_user),
        ("plan", "/plan", None, None),
        ("analytics_brands", "/analytics/brands", None, None),
        ("analytics_categories", "/analytics/categories", None, None),
        ("analytics_market", "/analytics/market", {"page_size": 50}, None),
        ("analytics_insights", "/analytics/insights", None, None),
        ("system_status", "/system/status", None, None),
    ]
    for name, path, params, headers in cases:
        await _get(path, params, headers)  # first use: imports, statement compilation (not what is measured)
        misses, hits = [], []
        for _ in range(3):
            await cache.bump(NS_FEED)
            misses.append(await _get(path, params, headers))
            hits.append(await _get(path, params, headers))
        miss = sorted(misses, key=lambda r: r.wall)[1]
        out[name] = {
            "miss_ms": round(miss.wall * 1000, 1),
            "miss_statements": miss.stmts,
            "miss_db_ms": round(miss.db * 1000, 1),
            "miss_kb": round(miss.resp_bytes / 1024, 1),
            "hit_ms": round(sorted(h.wall for h in hits)[1] * 1000, 1),
            "status": miss.status,
        }
    # an opportunity detail (comparables + history)
    async with session_scope() as s:
        oid = (
            await s.execute(select(Opportunity.id).where(Opportunity.is_active.is_(True)).limit(1))
        ).scalar()
    if oid:
        d = await _get(f"/opportunities/{oid}", None, None)
        d2 = await _get(f"/opportunities/{oid}", None, None)
        out["opportunity_detail"] = {
            "first_ms": round(d.wall * 1000, 1),
            "second_ms": round(d2.wall * 1000, 1),
            "statements": d.stmts,
            "kb": round(d.resp_bytes / 1024, 1),
        }
    vid = await sql_scalar(
        "SELECT external_id FROM listings WHERE external_id >= :lo AND external_id < :hi LIMIT 1",
        lo=BENCH_ID_LO,
        hi=BENCH_ID_HI,
    )
    if vid:
        await _get(f"/capture/items/{vid}", None, APP.headers)
        runs = [await _get(f"/capture/items/{vid}", None, APP.headers) for _ in range(3)]
        out["capture_item_detail_live_panel"] = {
            "ms": round(sorted(r.wall for r in runs)[1] * 1000, 1),
            "statements": runs[0].stmts,
            "kb": round(runs[0].resp_bytes / 1024, 1),
        }
    # page-stats / evaluations as the extension calls them
    out["page_stats_96_cards"] = await _page_stats()
    out["evaluations_200_known"] = await _evaluations()
    # invalidation: how a capture's cache.bump empties the feed cache for everybody
    out["feed_cache_after_capture_bump"] = await _bump_effect()
    return out


async def _get(path: str, params: dict[str, Any] | None, headers: dict[str, str] | None) -> Req:
    rec = Req(path, "get", 0)
    PROBE.rec = rec
    t0 = time.perf_counter()
    resp = await APP.client.get("/api/v1" + path, params=params, headers=headers or APP.read_headers)
    rec.wall = time.perf_counter() - t0
    PROBE.rec = None
    rec.status = resp.status_code
    rec.resp_bytes = len(resp.content)
    if resp.status_code != 200:
        log("GET failed", path, resp.status_code, resp.text[:200])
    return rec


async def _page_stats() -> dict[str, Any]:
    w = APP.workload
    items = [
        {
            "vinted_id": c["url"].split("/items/")[1].split("-")[0],
            "title": c["title"],
            "brand": c["brand"],
            "size": c["size"],
            "condition": c["condition"],
        }
        for c in w.pages[0][:96]
    ]
    rec = Req("page-stats", "post", len(items))
    PROBE.rec = rec
    t0 = time.perf_counter()
    resp = await APP.client.post("/api/v1/extension/page-stats", json={"items": items}, headers=APP.headers)
    rec.wall = time.perf_counter() - t0
    PROBE.rec = None
    return {
        "ms": round(rec.wall * 1000, 1),
        "statements": rec.stmts,
        "status": resp.status_code,
        "kb": round(len(resp.content) / 1024, 1),
    }


async def _evaluations() -> dict[str, Any]:
    from sqlalchemy import select

    from app.db.models import Listing
    from app.db.session import session_scope

    async with session_scope() as s:
        ids = list(
            (
                await s.execute(
                    select(Listing.external_id)
                    .where(
                        Listing.provider == "vinted",
                        Listing.external_id >= BENCH_ID_LO,
                        Listing.external_id < BENCH_ID_HI,
                    )
                    .limit(200)
                )
            ).scalars()
        )
    if not ids:
        return {"skipped": "no bench listings"}
    rec = Req("evaluations", "post", len(ids))
    PROBE.rec = rec
    t0 = time.perf_counter()
    resp = await APP.client.post("/api/v1/capture/evaluations", json={"vinted_ids": ids}, headers=APP.headers)
    rec.wall = time.perf_counter() - t0
    PROBE.rec = None
    return {
        "n": len(ids),
        "ms": round(rec.wall * 1000, 1),
        "statements": rec.stmts,
        "status": resp.status_code,
        "kb": round(len(resp.content) / 1024, 1),
    }


async def _bump_effect() -> dict[str, Any]:
    from app.core.cache import NS_FEED, cache

    await cache.bump(NS_FEED)
    first = await _get("/opportunities", {"page_size": 24}, None)
    second = await _get("/opportunities", {"page_size": 24}, None)
    # a capture happens between two reads of the dashboard
    w = APP.workload
    await post("cards", w.after_closet[:2], "bump/capture", 2)
    third = await _get("/opportunities", {"page_size": 24}, None)
    return {
        "first_ms": round(first.wall * 1000, 1),
        "second_ms_cached": round(second.wall * 1000, 1),
        "after_one_capture_ms": round(third.wall * 1000, 1),
        "note": "every /capture/cards and /capture/item calls cache.bump(NS_FEED): the feed, stats and the "
        "extension's market-cache (same namespace) are all recomputed on their next read",
    }


async def pg_settings() -> dict[str, Any]:
    from sqlalchemy import text

    from app.db.session import session_scope

    out: dict[str, Any] = {}
    async with session_scope() as s:
        for name in (
            "shared_buffers",
            "work_mem",
            "max_connections",
            "effective_cache_size",
            "plan_cache_mode",
        ):
            out[name] = (await s.execute(text(f"SHOW {name}"))).scalar()
        out["version"] = (await s.execute(text("SHOW server_version"))).scalar()
    return out


async def pg_report() -> dict[str, Any]:
    """What PostgreSQL saw during the run: scans per table, dead rows (churn), indexes never used."""
    from sqlalchemy import text

    from app.db.session import session_scope

    out: dict[str, Any] = {}
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT relname, seq_scan, seq_tup_read, idx_scan, n_tup_ins, n_tup_upd, n_tup_del, "
                    "n_live_tup, n_dead_tup FROM pg_stat_user_tables "
                    "WHERE relname IN ('listings','opportunities','analyses','market_comparables',"
                    "'listing_snapshots','listing_images','opportunity_scores','sellers','analysis_jobs',"
                    "'external_searches','sold_sales') ORDER BY seq_tup_read DESC"
                )
            )
        ).all()
        out["tables"] = [dict(r._mapping) for r in rows]
        unused = (
            await s.execute(
                text(
                    "SELECT s.relname AS table, s.indexrelname AS index, s.idx_scan, "
                    "pg_size_pretty(pg_relation_size(s.indexrelid)) AS size, "
                    "pg_relation_size(s.indexrelid) AS bytes FROM pg_stat_user_indexes s "
                    "JOIN pg_index i ON i.indexrelid = s.indexrelid WHERE s.idx_scan = 0 AND NOT i.indisunique "
                    "AND NOT i.indisprimary ORDER BY pg_relation_size(s.indexrelid) DESC LIMIT 12"
                )
            )
        ).all()
        out["indexes_never_used_in_this_run"] = [dict(r._mapping) for r in unused]
    return out


async def row_sizes() -> dict[str, Any]:
    """Bytes written per captured listing (heap + toast, without indexes) and rows per listing."""
    where = "l.provider='vinted' AND l.external_id >= :lo AND l.external_id < :hi"
    p = {"lo": BENCH_ID_LO, "hi": BENCH_ID_HI}
    n = int(await sql_scalar(f"SELECT count(*) FROM listings l WHERE {where}", **p) or 0)
    if not n:
        return {}
    out: dict[str, Any] = {"listings": n}
    out["opportunity_row_bytes_avg"] = int(
        await sql_scalar(
            f"SELECT avg(pg_column_size(o.*)) FROM opportunities o JOIN listings l ON l.id=o.listing_id WHERE {where}",
            **p,
        )
        or 0
    )
    out["analysis_row_bytes_avg"] = int(
        await sql_scalar(
            f"SELECT avg(pg_column_size(a.*)) FROM analyses a JOIN listings l ON l.id=a.listing_id WHERE {where}",
            **p,
        )
        or 0
    )
    out["comparable_rows_per_listing"] = float(
        await sql_scalar(
            f"SELECT count(*)::float / {n} FROM market_comparables m JOIN listings l ON l.id=m.listing_id WHERE {where}",
            **p,
        )
        or 0
    )
    out["snapshot_row_bytes_avg"] = int(
        await sql_scalar(
            f"SELECT coalesce(avg(pg_column_size(s.*)),0) FROM listing_snapshots s JOIN listings l ON l.id=s.listing_id WHERE {where}",
            **p,
        )
        or 0
    )
    out["opportunity_json_bytes_avg"] = {
        col: int(
            await sql_scalar(
                f"SELECT coalesce(avg(pg_column_size(o.{col})),0) FROM opportunities o JOIN listings l ON l.id=o.listing_id WHERE {where}",
                **p,
            )
            or 0
        )
        for col in (
            "score_breakdown",
            "dossier",
            "decision",
            "market_snapshot",
            "ai_analysis",
            "explanation",
            "risk_factors",
        )
    }
    out["analysis_json_bytes_avg"] = {
        col: int(
            await sql_scalar(
                f"SELECT coalesce(avg(pg_column_size(a.{col})),0) FROM analyses a JOIN listings l ON l.id=a.listing_id WHERE {where}",
                **p,
            )
            or 0
        )
        for col in ("inputs", "product", "visual", "economic", "market", "decision")
    }
    return out


# -------------------------------------------------------------------------------------------- main
async def run(args: argparse.Namespace) -> dict[str, Any]:
    load_application()
    from app.core.config import get_settings

    settings = get_settings()
    meta: dict[str, Any] = {
        "database": throwaway_name(args.database_url),
        "redis": args.redis_url,
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "algorithm_version": settings.algorithm_version,
        "load_average_at_start": [round(x, 2) for x in os.getloadavg()],
        "tunables": {
            "candidate_limit": 400,
            "comparables_window_days": settings.comparables_window_days,
            "pool_cache_ttl_s": 60,
            "reuse_analysis_for_h": 2,
            "snapshot_heartbeat_hours": settings.snapshot_heartbeat_hours,
            "db_pool_size": settings.db_pool_size,
            "uvicorn_workers_in_production": "WEB_CONCURRENCY default 2 (docker/entrypoint.sh)",
        },
        "snapshot": {},
    }
    with contextlib.suppress(Exception):
        meta["git"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(__file__),
        ).stdout.strip()
    meta["market"] = await ensure_market(args)
    await ensure_user()
    if args.populate:
        meta["populated"] = await populate_opportunities()
        log("populated:", meta["populated"])
    for table in ("listings", "opportunities", "market_comparables", "analyses", "listing_snapshots"):
        meta["snapshot"][table] = int(await sql_scalar(f"SELECT count(*) FROM {table}"))
    await sql_exec("ANALYZE")
    meta["postgres"] = await pg_settings()
    try:  # index and scan counters for the measured part only (needs the privilege; skipped otherwise)
        await sql_exec("SELECT pg_stat_reset()")
        meta["pg_stats_reset"] = True
    except Exception as exc:
        meta["pg_stats_reset"] = f"no ({type(exc).__name__})"
    w = build_workload(args)
    APP.workload = w
    meta["workload"] = {
        "seed": args.seed,
        "pages": len(w.pages),
        "cards_per_page": CARDS_PER_PAGE,
        "chunks": [FIRST_CHUNK, NEXT_CHUNK],
        "items": len(w.items),
        "distinct_listings_generated": w.n_listings,
        "card_payload_example": {k: v for k, v in w.pages[0][0].items()},
    }
    want = set((args.only or "").split(",")) - {""} or {
        "cold_new",
        "warm_unchanged",
        "warm_new",
        "price_drop",
        "size_curve",
        "market_change",
        "concurrent",
        "diverse_new",
        "worker_batch",
        "worker_queue",
        "read_side",
        "alerts",
        "vision_repeat",
        "ai_queue",
    }
    result: dict[str, Any] = {"meta": meta}
    result["passes"] = await pass_main(args, w, want)
    if "worker_batch" in want:
        result["passes"]["worker_batch"] = await pass_worker_batch(args)
    if "worker_queue" in want:
        result["passes"]["worker_queue"] = await pass_worker_queue(args)
    if "read_side" in want:
        result["passes"]["read_side"] = await pass_read_side(args)
    if "alerts" in want:
        result["passes"]["alerts"] = await pass_alerts(args, w)
    if "vision_repeat" in want:
        result["passes"]["vision_repeat"] = await pass_vision_repeat(args, w)
    if "ai_queue" in want and args.queue_seconds > 0:
        result["passes"]["ai_queue"] = await pass_ai_queue(args)
    for table in (
        "listings",
        "opportunities",
        "market_comparables",
        "analyses",
        "listing_snapshots",
        "opportunity_scores",
    ):
        meta["snapshot"][f"{table}_after"] = int(await sql_scalar(f"SELECT count(*) FROM {table}"))
    meta["table_sizes_mb"] = {
        t: round(int(await sql_scalar(f"SELECT pg_total_relation_size('{t}')")) / 1e6, 1)
        for t in (
            "listings",
            "opportunities",
            "market_comparables",
            "analyses",
            "listing_snapshots",
            "opportunity_scores",
        )
    }
    meta["pg_activity"] = await pg_report()
    meta["load_average_at_end"] = [round(x, 2) for x in os.getloadavg()]
    meta["max_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    await purge_bench_rows()
    await APP.client.aclose()
    from app.core.redis import close_redis
    from app.db.session import dispose_engine

    await close_redis()
    await dispose_engine()
    return result


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    prepare_environment(args)
    FULL_OUTPUT[0] = bool(args.full)
    result = asyncio.run(run(args))
    text = json.dumps(result, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
