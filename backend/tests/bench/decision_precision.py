"""BENCHMARK SINTETICO - precision of the DECISIONS (BUY / STRONG BUY), not only of the price.

EVERY NUMBER PRINTED HERE COMES FROM A SIMULATED MARKET ("benchmark sintetico"), NEVER FROM REAL VINTED
DATA. The world is made up (``LINES`` below), its parameters are assumptions of this file, and a
change that improves the figures here proves nothing about real Vinted until it is measured on real
sales (``app.tools.price_eval`` / the backtest). What it does show, reproducibly, is whether the
decision logic falls for the traps that the synthetic market is built to contain.

It is an *as-of* backtest (no look-ahead): for each fresh listing the engine sees only what a
user could have seen when it was published (sold / removed / on-sale listings as of that moment, the
120-day window, the candidate quotas of ``app.opportunities.pipeline``), runs the REAL
``run_analysis`` and the verdict is compared with what the item was really worth.

Nothing touches a database or the network: the engine is pure, the pools are built in memory with the
same limits as the SQL (the constants of ``pipeline`` are read from the code under test, so the same
file measures the baseline checkout and the working tree)::

    # baseline (a git worktree of the committed code)
    cd /tmp/wt_base/backend && PYTHONPATH=. python /path/to/decision_precision.py --out before.json
    # working tree
    cd backend && PYTHONPATH=. python -m tests.bench.decision_precision --out after.json

The simulated market (seeded, deterministic):

* lines of products, each with its own true value; condition multipliers, a mild size effect (the edges
  of the size run sell for less) and an individual log-normal noise; asks are inflated (+18%) and noisy,
  cheaper asks sell more and faster; 12% of the sellers are "bargain" sellers (ask 50-80% of the
  true value) so that real deals exist;
* groups of subjects, reported separately:
    ``identified``      lines the model catalog knows (the model is extracted);
    ``identified_rare`` identified lines with 4-9 listings in the whole year;
    ``model_less``      lines the catalog does not know (model None; the title still carries the
                        line words, except for 25% of the listings whose title is generic);
    ``kids``            children's versions, some of them of the SAME model as the adult line
                        (adult and kids pool together unless the engine separates them);
    ``variant_merge``   High/Low (or Classic/Custom) versions that the model catalog merges into
                        one model string but that are worth very different amounts;
    ``regime_change``   lines whose market price fell (or rose) ~85 days before the end of the
                        world: sales older than that are at the old price;
* truth: the realised resale is 0.96 x the item's true value (buyers haggle ~8% below the last ask
  seen); the buyer pays the asking price + the engine's own cost model; a "true deal" is a purchase
  with a real profit >= EUR 10.

Per group it reports: BUY/STRONG BUY count, precision (share with a real profit >= 10), share of
loss-making BUYs, STRONG BUY count and its losses, recall of true deals, the FMV error against the
realised resale (MAPE, median APE, signed bias), the verdicts, "BUY with zero same-model comparables"
(extracted model) and "BUY with zero same-line comparables" (true line, known only to the benchmark),
and the coverage KPI "share of analyses with >= 3 same-line comparables".
"""

# ruff: noqa: S311 - a seeded simulation, not security
from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

for _k, _v in {
    "ENVIRONMENT": "test",
    "LOG_JSON": "false",
    "LOG_LEVEL": "ERROR",
    "AI_API_KEY": "",
    "JWT_SECRET": "bench-secret-bench-secret-bench-secret-1",
}.items():
    os.environ.setdefault(_k, _v)

from app.analytics import market_stats as MS  # noqa: E402
from app.opportunities import pipeline as P  # noqa: E402
from app.opportunities.engine import EconomicTargets, SubjectContext, run_analysis  # noqa: E402
from app.pricing.comparables import ItemProfile  # noqa: E402
from app.pricing.evidence import PriceEvidence  # noqa: E402
from app.pricing.market_value import SegmentPrior  # noqa: E402
from app.profit.calculator import CostProfile, profit_for  # noqa: E402
from app.scoring.seller import SellerProfile  # noqa: E402

try:  # the extraction of audience / variant exists only in the code after the pricing fixes
    from app.pricing.lines import profile_extras as _profile_extras  # noqa: E402
except ImportError:  # the baseline checkout

    def _profile_extras(title: str, gender: str | None, category: str | None, parent: str | None) -> dict[str, Any]:
        return {}


LABEL = "BENCHMARK SINTETICO (mercato simulato, non dati reali)"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
DAYS = 330  # simulated history
SUBJECT_DAYS = 200  # subjects are listings published in the last SUBJECT_DAYS days
ASK_INFL, NEG = 1.18, 0.08
MIN_PROFIT = 10.0
VISION_OK = {"analyzer": "claude_vision", "photo_quality": {"has_label_photo": True}, "defects": []}

COND = [
    ("new_with_tags", 1.15, 0.08),
    ("new_without_tags", 1.08, 0.10),
    ("very_good", 1.0, 0.47),
    ("good", 0.88, 0.30),
    ("satisfactory", 0.70, 0.05),
]
SZ_S = ["S", "M", "L", "XL"]
SZ_SH = [f"EU {s}" for s in range(38, 46)]
SZ_KID = [f"EU {s}" for s in range(28, 36)]
SZ_KID_TOP = ["4", "6", "8", "10"]
# Mild size effect on the value: the edges of the size run are harder to sell.
SIZE_EFFECT = {"EU 38": 0.88, "EU 39": 0.94, "EU 44": 0.96, "EU 45": 0.90, "XL": 0.94}
COLORS = ["bianche", "nere", "blu", "grigie", "rosse"]
GENERIC_TITLE_RATE = 0.25  # share of titles of model-less lines that carry no line word at all


@dataclass(frozen=True)
class Line:
    brand: str
    brand_name: str
    category: str
    parent: str
    label: str  # the line, as sellers write it in the title
    model: str | None  # what the model catalog extracts from the title (None: unknown line)
    value: float  # true value in "very good" condition
    count: int  # listings over the whole history
    sizes: list[str]
    word: str  # category word in the title
    group: str
    audience: str = "adult"
    # (days before NOW, factor): items published after that moment are worth value x factor
    regime: tuple[int, float] | None = None


LINES: list[Line] = [
    # identified, common
    Line("nike", "Nike", "sneakers", "footwear", "Air Force 1", "Air Force 1", 55, 230, SZ_SH, "sneakers", "identified"),
    Line("nike", "Nike", "sneakers", "footwear", "Dunk Low", "Dunk Low", 85, 200, SZ_SH, "sneakers", "identified"),
    Line("nike", "Nike", "sneakers", "footwear", "Air Max 90", "Air Max 90", 65, 90, SZ_SH, "sneakers", "identified"),
    Line("adidas", "Adidas", "sneakers", "footwear", "Samba", "Samba", 62, 220, SZ_SH, "sneakers", "identified"),
    Line("adidas", "Adidas", "sneakers", "footwear", "Superstar", "Superstar", 34, 80, SZ_SH, "sneakers", "identified"),
    Line("ralph-lauren", "Ralph Lauren", "polo-shirts", "tops", "Custom Slim Fit", "Custom Slim Fit", 30, 220, SZ_S, "polo", "identified"),
    Line("ralph-lauren", "Ralph Lauren", "polo-shirts", "tops", "Classic Fit", "Classic Fit", 27, 80, SZ_S, "polo", "identified"),
    Line("the-north-face", "The North Face", "puffer-jackets", "outerwear", "Nuptse 700", "Nuptse 700", 145, 90, SZ_S, "piumino", "identified"),
    # identified, rare (~8 listings in the whole year)
    Line("nike", "Nike", "sneakers", "footwear", "Air Max 95", "Air Max 95", 105, 9, SZ_SH, "sneakers", "identified_rare"),
    Line("adidas", "Adidas", "sneakers", "footwear", "Spezial", "Spezial", 70, 9, SZ_SH, "sneakers", "identified_rare"),
    Line("ralph-lauren", "Ralph Lauren", "polo-shirts", "tops", "Big Pony", "Big Pony", 40, 8, SZ_S, "polo", "identified_rare"),
    Line("the-north-face", "The North Face", "puffer-jackets", "outerwear", "Himalayan", "Himalayan", 210, 8, SZ_S, "piumino", "identified_rare"),
    # ultra rare: a handful of listings in the whole year, so most of the time NO sale of the same model is known
    # (a cheap model next to dearer ones of its brand, and the other way round)
    Line("adidas", "Adidas", "sneakers", "footwear", "Campus", "Campus", 38, 4, SZ_SH, "sneakers", "identified_rare"),
    Line("nike", "Nike", "sneakers", "footwear", "Air Jordan 4", "Air Jordan 4", 190, 4, SZ_SH, "sneakers", "identified_rare"),
    Line("ralph-lauren", "Ralph Lauren", "knitwear", "tops", "Cable Knit", "Cable Knit", 120, 4, SZ_S, "maglione", "identified_rare"),
    Line("the-north-face", "The North Face", "jackets", "outerwear", "Denali", "Denali", 130, 4, SZ_S, "giacca", "identified_rare"),
    # lines the model catalog does not know: the model is None, the title still carries the line
    Line("nike", "Nike", "sneakers", "footwear", "Revolution 6", None, 22, 70, SZ_SH, "scarpe running", "model_less"),
    Line("nike", "Nike", "sneakers", "footwear", "Blazer Mid", None, 60, 50, SZ_SH, "sneakers", "model_less"),
    Line("nike", "Nike", "sneakers", "footwear", "Cortez", None, 40, 40, SZ_SH, "sneakers", "model_less"),
    Line("nike", "Nike", "sneakers", "footwear", "Air Max 97", None, 100, 30, SZ_SH, "sneakers", "model_less"),
    Line("adidas", "Adidas", "sneakers", "footwear", "Forum", None, 55, 40, SZ_SH, "sneakers", "model_less"),
    Line("adidas", "Adidas", "sneakers", "footwear", "Ultraboost", None, 60, 60, SZ_SH, "scarpe running", "model_less"),
    Line("ralph-lauren", "Ralph Lauren", "polo-shirts", "tops", "Basic Pony", None, 25, 60, SZ_S, "polo", "model_less"),
    Line("ralph-lauren", "Ralph Lauren", "t-shirts", "tops", "T-shirt", None, 15, 60, SZ_S, "t-shirt", "model_less"),
    Line("ralph-lauren", "Ralph Lauren", "knitwear", "tops", "Maglione", None, 55, 60, SZ_S, "maglione", "model_less"),
    Line("the-north-face", "The North Face", "puffer-jackets", "outerwear", "Thermoball", None, 90, 30, SZ_S, "piumino", "model_less"),
    Line("the-north-face", "The North Face", "jackets", "outerwear", "Antipioggia", None, 55, 40, SZ_S, "giacca", "model_less"),
    # children's versions: separate lines...
    Line("nike", "Nike", "sneakers", "footwear", "Bambino", None, 18, 60, SZ_KID, "scarpe bambino", "kids", "kids"),
    Line("adidas", "Adidas", "sneakers", "footwear", "Bambino", None, 18, 50, SZ_KID, "scarpe bambino", "kids", "kids"),
    Line("ralph-lauren", "Ralph Lauren", "polo-shirts", "tops", "Bambino", None, 12, 40, SZ_KID_TOP, "polo bambino", "kids", "kids"),
    Line("the-north-face", "The North Face", "puffer-jackets", "outerwear", "Bambino", None, 40, 30, SZ_KID_TOP, "piumino bambino", "kids", "kids"),
    # ... and the SAME model as the adult line (the title says "bambino"; the model is extracted all the same)
    Line("nike", "Nike", "sneakers", "footwear", "Air Force 1", "Air Force 1", 24, 90, SZ_KID, "sneakers bambino", "kids", "kids"),
    Line("the-north-face", "The North Face", "puffer-jackets", "outerwear", "Nuptse 700", "Nuptse 700", 60, 40, SZ_KID_TOP, "piumino bambino", "kids", "kids"),
    # versions the model catalog merges into one model string, worth very different amounts
    Line("nike", "Nike", "sneakers", "footwear", "Dunk High", "Dunk Low", 125, 70, SZ_SH, "sneakers", "variant_merge"),
    Line("nike", "Nike", "sneakers", "footwear", "Air Jordan 1 Low", "Air Jordan 1", 80, 40, SZ_SH, "sneakers", "variant_merge"),
    Line("nike", "Nike", "sneakers", "footwear", "Air Jordan 1 High", "Air Jordan 1", 150, 30, SZ_SH, "sneakers", "variant_merge"),
    # price regime changes ~85 days before the end of the world
    Line("adidas", "Adidas", "sneakers", "footwear", "Gazelle", "Gazelle", 48, 110, SZ_SH, "sneakers", "regime_change", regime=(85, 0.60)),
    Line("nike", "Nike", "sneakers", "footwear", "Pegasus", None, 45, 80, SZ_SH, "scarpe running", "regime_change", regime=(85, 0.60)),
    Line("adidas", "Adidas", "sneakers", "footwear", "Stan Smith", None, 45, 90, SZ_SH, "sneakers", "regime_change", regime=(85, 1.35)),
]
GROUPS = ["identified", "identified_rare", "model_less", "kids", "variant_merge", "regime_change"]


@dataclass
class Truth:
    line: str  # key of the line (label + audience), true "same line"
    group: str
    true: float
    model: str | None


def build_world(rng: random.Random, bargain: float) -> list[tuple[ItemProfile, Truth]]:
    def ln(sig: float) -> float:
        return math.exp(rng.gauss(-sig * sig / 2, sig))

    def cond() -> tuple[str, float]:
        x = rng.random()
        for c, m, p in COND:
            x -= p
            if x <= 0:
                return c, m
        return "very_good", 1.0

    out: list[tuple[ItemProfile, Truth]] = []
    for ln_ in LINES:
        for _ in range(ln_.count):
            pub = NOW - timedelta(days=rng.uniform(0, DAYS))
            c, m = cond()
            regime = 1.0
            if ln_.regime and pub >= NOW - timedelta(days=ln_.regime[0]):
                regime = ln_.regime[1]
            size = rng.choice(ln_.sizes)
            true = ln_.value * m * ln(0.12) * regime * SIZE_EFFECT.get(size, 1.0)
            ask = max(5, round(true * ASK_INFL * ln(0.14)))
            if rng.random() < bargain:
                ask = max(5, round(true * rng.uniform(0.5, 0.8)))
            ratio = ask / true
            p_sell = min(0.9, max(0.05, 0.9 - 1.6 * (ratio - 1.05)))
            status, sold_at, removed_at = "active", None, None
            if rng.random() < p_sell:
                d = rng.expovariate(1 / (6 * max(ratio, 0.5) ** 2))
                if pub + timedelta(days=d) < NOW:
                    status, sold_at = "sold", pub + timedelta(days=d)
            if status == "active" and (NOW - pub).days > 50:
                gone = pub + timedelta(days=rng.uniform(40, 90))
                if gone < NOW:
                    status, removed_at = "removed", gone
            color = rng.choice(COLORS)
            seen = sold_at or removed_at or (NOW - timedelta(hours=rng.uniform(1, 40)))
            generic = ln_.model is None and ln_.audience == "adult" and rng.random() < GENERIC_TITLE_RATE
            title = (
                f"{ln_.brand_name} {ln_.word} {color} {size}"
                if generic
                else f"{ln_.brand_name} {ln_.label} {ln_.word} {color} {size}"
            )
            gender = "kids" if ln_.audience == "kids" and rng.random() < 0.7 else (
                "men" if ln_.audience == "adult" and rng.random() < 0.6 else None
            )
            out.append(
                (
                    ItemProfile(
                        id=uuid.UUID(int=rng.getrandbits(128)),
                        title=title,
                        price=D(str(ask)),
                        brand=ln_.brand,
                        category=ln_.category,
                        parent_category=ln_.parent,
                        model=ln_.model,
                        condition=c,
                        size=size,
                        gender=gender,
                        color=color,
                        status=status,
                        published_at=pub,
                        sold_at=sold_at,
                        last_seen_at=seen,
                        removed_at=removed_at,
                        **_profile_extras(title, gender, ln_.category, ln_.parent),
                    ),
                    Truth(f"{ln_.label}|{ln_.audience}", ln_.group, true, ln_.model),
                )
            )
    return out


# ------------------------------------------------------------------------------- the as-of market
def _asof(x: ItemProfile, t: datetime) -> ItemProfile | None:
    """The listing as the engine would have seen it at time t (None if not yet published)."""
    if x.published_at is None or x.published_at > t:
        return None
    if x.sold_at and x.sold_at <= t:
        return x
    if x.removed_at and x.removed_at <= t:
        return x
    if x.status == "active" and not x.sold_at and not x.removed_at:
        return replace(x, last_seen_at=t - timedelta(hours=12))
    return replace(x, status="active", sold_at=None, removed_at=None, last_seen_at=t - timedelta(hours=12))


def _event(x: ItemProfile) -> datetime:
    return x.sold_at or x.removed_at or x.last_seen_at or x.published_at  # type: ignore[return-value]


def pool_asof(subject: ItemProfile, t: datetime, family: list[ItemProfile]) -> tuple[list[ItemProfile], list[ItemProfile]]:
    """Candidates of the subject at time t: the SQL's quotas of recent sold / on-sale / removed listings of
    the brand and category family, plus (when the code under test has them) the per-model strata."""
    since = t - timedelta(days=120)
    fam = [y for x in family if x.id != subject.id and (y := _asof(x, t)) is not None]
    limit = P.CANDIDATE_LIMIT // 2
    sold = sorted([x for x in fam if x.status == "sold" and _event(x) >= since], key=_event, reverse=True)
    act = sorted(
        [x for x in fam if x.status == "active" and x.published_at >= since],  # type: ignore[operator]
        key=lambda x: x.published_at,  # type: ignore[arg-type,return-value]
        reverse=True,
    )
    rem = sorted([x for x in fam if x.status == "removed" and x.removed_at >= since], key=lambda x: x.removed_at, reverse=True)  # type: ignore[operator,arg-type,return-value]
    # model-first / line strata of the code under test (absent in the baseline: 0)
    m_sold, m_act = getattr(P, "MODEL_SOLD_LIMIT", 0), getattr(P, "MODEL_ACTIVE_LIMIT", 0)
    l_sold, l_act = getattr(P, "LINE_SOLD_LIMIT", 0), getattr(P, "LINE_ACTIVE_LIMIT", 0)
    line = getattr(P, "_line_key", lambda _s: None)(subject)
    ms: list[ItemProfile] = []
    ma: list[ItemProfile] = []
    if (m_sold or m_act) and subject.model:
        ms = [x for x in sold if x.model == subject.model][:m_sold]
        ma = [x for x in act if x.model == subject.model][:m_act]
    elif (l_sold or l_act) and line:
        ms = [x for x in sold if P._is_own_line(x, None, line)][:l_sold]
        ma = [x for x in act if P._is_own_line(x, None, line)][:l_act]
    pool = P.CandidatePool(
        _dedupe([*sold[: limit + 1], *ms]),
        _dedupe([*act[: limit + 1], *ma]),
        rem[: P.REMOVED_LIMIT + 1],
    )
    params = inspect.signature(pool.for_subject).parameters
    if "line" in params:
        cands = pool.for_subject(subject.id, subject.model, line)
    elif "model" in params:
        cands = pool.for_subject(subject.id, subject.model)
    else:
        cands = pool.for_subject(subject.id)
    return cands, fam


def _dedupe(items: list[ItemProfile]) -> list[ItemProfile]:
    seen: set[Any] = set()
    out = []
    for x in items:
        if x.id not in seen:
            seen.add(x.id)
            out.append(x)
    return out


def prior_asof(subject: ItemProfile, fam: list[ItemProfile], t: datetime) -> SegmentPrior | None:
    """Mirror of ``analytics.market_stats`` (segment prior) on what was known at t: the model segment when it
    has >= 3 listings, else the brand x category one; the code under test says whether removed listings count."""
    win = t - timedelta(days=90)
    rows = [x for x in fam if x.category == subject.category and (x.sold_at or x.published_at) >= win]  # type: ignore[operator]
    statuses = getattr(MS, "PRIOR_STATUSES", None)  # absent in the baseline: every status counts

    def make(rs: list[ItemProfile], level: str) -> SegmentPrior | None:
        if statuses is not None:
            rs = [x for x in rs if x.status in statuses]
        if len(rs) < 3:
            return None
        sold = [float(x.price) for x in rs if x.status == "sold"]
        if statuses is not None and not sold:
            return None
        base = sold if len(sold) >= 5 else [float(x.price) for x in rs]
        k = 1.0 if len(sold) >= 5 else 0.88

        def q(v: list[float], f: float) -> float:
            return sorted(v)[min(len(v) - 1, int(f * (len(v) - 1) + 0.5))]

        extra = {"sold_count": len(sold)} if "sold_count" in SegmentPrior.__dataclass_fields__ else {}
        return SegmentPrior(
            median_price=statistics.median(base) * k,
            p25_price=q(base, 0.25) * k,
            p75_price=q(base, 0.75) * k,
            sample_size=len(rs),
            sell_through_rate=len(sold) / len(rs),
            level=level,
            **extra,
        )

    if subject.model:
        p = make([x for x in rows if x.model == subject.model], "model")
        if p:
            return p
    return make(rows, "brand_category")


def subject_ctx(p: ItemProfile, vision: dict[str, Any] | None) -> SubjectContext:
    return SubjectContext(
        profile=p,
        description_length=160,
        photo_count=6,
        favourite_count=4,
        listing_age_hours=6,
        shipping_fee=D("3.49"),
        brand_name=p.brand.title() if p.brand else None,
        brand_counterfeit_risk=0.1,
        category_baseline_days=14,
        identification={},
        identification_confidence=88,
        seller=SellerProfile(D("4.9"), 80),
        description="Scarpe in ottime condizioni, indossate poche volte, suola pulita, spedizione tracciata.",
        vision=vision,
    )


# ------------------------------------------------------------------------------- the run
def default_haircut() -> float | None:
    """The assumed haircut on Vinted sold prices of the code under test (None in the baseline: it has none)."""
    from app.core.config import get_settings

    return getattr(get_settings(), "vinted_sold_haircut", None)


def run_seed(
    seed: int, per_group: int, bargain: float, vision: bool, haircut: float | None = None
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    world = build_world(rng, bargain)
    truth_of = {x.id: t for x, t in world}
    by_family: dict[tuple[str | None, str | None], list[ItemProfile]] = defaultdict(list)
    for x, _ in world:
        by_family[(x.brand, x.parent_category)].append(x)
    costs, targets = CostProfile(), EconomicTargets()

    regime_start = {ln_.label: ln_.regime[0] for ln_ in LINES if ln_.regime}
    eligible: dict[str, list[tuple[ItemProfile, Truth]]] = defaultdict(list)
    for x, t in world:
        age = (NOW - x.published_at).total_seconds()  # type: ignore[operator]
        if age < 3600 or age > SUBJECT_DAYS * 86400:
            continue
        label = t.line.split("|")[0]
        if t.group == "regime_change" and (NOW - x.published_at) > timedelta(days=regime_start[label] - 5):  # type: ignore[operator]
            continue  # subjects of a regime change are the listings of the NEW regime
        eligible[t.group].append((x, t))
    pick = random.Random(seed + 1)
    chosen: list[tuple[ItemProfile, Truth]] = []
    for g in GROUPS:
        pool = eligible[g]
        pick.shuffle(pool)
        chosen += pool[:per_group]

    rows: list[dict[str, Any]] = []
    for x, t in chosen:
        at = x.published_at + timedelta(hours=2)  # type: ignore[operator]
        subj = replace(x, status="active", sold_at=None, removed_at=None, last_seen_at=at)
        cands, fam = pool_asof(subj, at, by_family[(x.brand, x.parent_category)])
        r = run_analysis(
            subject_ctx(subj, VISION_OK if vision else None),
            cands,
            at,
            costs,
            targets,
            prior=prior_asof(subj, fam, at),
            evidence=PriceEvidence(negotiation_discount=haircut or None),
        )
        realised = round(t.true * (1 - NEG / 2), 2)
        truth_profit = float(profit_for(subj.price, D(str(realised)), costs, D("3.49")).net_profit)
        used = [c for c in r.market.comparables if c.included]
        n_same_line = sum(1 for c in used if c.item.id in truth_of and truth_of[c.item.id].line == t.line)
        fmv = float(r.market.fair_market_value) if r.market.fair_market_value is not None else None
        rows.append(
            {
                "group": t.group,
                "line": t.line,
                "verdict": r.decision.verdict.value,
                "candidate": r.decision.candidate.value,
                "price": float(subj.price),
                "true": round(t.true, 2),
                "realised": realised,
                "fmv": fmv,
                "fmv_err": (fmv - realised) / realised if fmv is not None else None,
                "exp_profit": float(r.expected_profit) if r.expected_profit is not None else None,
                "truth_profit": round(truth_profit, 2),
                "n_used": r.market.n_used,
                "n_same_model": sum(1 for c in used if subj.model and c.item.model == subj.model),
                "n_same_line": n_same_line,
                "model_known": subj.model is not None,
                "condition": subj.condition,
                "size": subj.size,
                "days_before_end": round((NOW - x.published_at).total_seconds() / 86400, 1),  # type: ignore[operator]
                "tier": r.market.tier,
                "mixed": r.market.mixed,
                "trend": r.market.trend,
                "conf": r.confidence.score,
                "mkt_conf": r.market.confidence,
                "quality": r.data_quality,
                "discount": float(r.discount_vs_market) if r.discount_vs_market is not None else None,
            }
        )
    return rows


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deals = [r for r in rows if r["truth_profit"] >= MIN_PROFIT]
    buy = [r for r in rows if r["verdict"] in ("BUY", "STRONG_BUY")]
    sb = [r for r in rows if r["verdict"] == "STRONG_BUY"]
    good = [r for r in buy if r["truth_profit"] >= MIN_PROFIT]
    loss = [r for r in buy if r["truth_profit"] < 0]
    errs = [r["fmv_err"] for r in rows if r["fmv_err"] is not None]
    known = [r for r in rows if r["model_known"]]
    out: dict[str, Any] = {
        "n": len(rows),
        "true_deals": len(deals),
        "verdicts": dict(Counter(r["verdict"] for r in rows)),
        "buy_n": len(buy),
        "buy_precision_ge10": round(len(good) / len(buy), 3) if buy else None,
        "buy_loss_making_n": len(loss),
        "buy_loss_making_share": round(len(loss) / len(buy), 3) if buy else None,
        "buy_mean_true_profit": round(statistics.mean(r["truth_profit"] for r in buy), 2) if buy else None,
        "strong_buy_n": len(sb),
        "strong_buy_loss_making": sum(1 for r in sb if r["truth_profit"] < 0),
        "recall_true_deals": f"{sum(1 for r in deals if r['verdict'] in ('BUY', 'STRONG_BUY'))}/{len(deals)}",
        "fmv_n": len(errs),
        "fmv_coverage": round(len(errs) / len(rows), 3) if rows else None,
        "fmv_mape": round(statistics.mean(abs(e) for e in errs), 3) if errs else None,
        "fmv_median_ape": round(statistics.median(abs(e) for e in errs), 3) if errs else None,
        "fmv_bias_mean": round(statistics.mean(errs), 3) if errs else None,
        "fmv_bias_median": round(statistics.median(errs), 3) if errs else None,
        "buy_zero_same_model": sum(1 for r in buy if r["model_known"] and r["n_same_model"] == 0),
        "buy_model_known": sum(1 for r in buy if r["model_known"]),
        "buy_zero_same_line": sum(1 for r in buy if r["n_same_line"] == 0),
        "share_ge3_same_line": round(sum(1 for r in rows if r["n_same_line"] >= 3) / len(rows), 3) if rows else None,
        "share_ge3_same_model_of_known": round(sum(1 for r in known if r["n_same_model"] >= 3) / len(known), 3)
        if known
        else None,
    }
    return out


def git_info() -> dict[str, str]:
    root = Path(P.__file__).resolve().parents[3]
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()  # noqa: S603, S607
    except OSError:
        head = "?"
    return {"root": str(root), "head": head}


def report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {g: summarise([r for r in rows if r["group"] == g]) for g in GROUPS}
    out["ALL"] = summarise(rows)
    return out


def print_report(res: dict[str, Any]) -> None:
    print(f"== {LABEL}")
    print(f"   code under test: {res['code']}  seeds={res['args']['seeds']}  subjects/group/seed={res['args']['per_group']}")
    keys = [
        "n",
        "true_deals",
        "buy_n",
        "buy_precision_ge10",
        "buy_loss_making_share",
        "strong_buy_n",
        "strong_buy_loss_making",
        "recall_true_deals",
        "fmv_coverage",
        "fmv_mape",
        "fmv_bias_mean",
        "buy_zero_same_model",
        "buy_zero_same_line",
        "share_ge3_same_line",
    ]
    for g, m in res["groups"].items():
        print(f"-- {g}: verdicts {m['verdicts']}")
        print("   " + " | ".join(f"{k}={m[k]}" for k in keys))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", default="7,8,9", help="comma separated seeds")
    ap.add_argument("--per-group", type=int, default=120, help="subjects per group per seed")
    ap.add_argument("--bargain", type=float, default=0.12)
    ap.add_argument("--no-vision", action="store_true", help="no photo analysis (STRONG BUY unreachable)")
    ap.add_argument(
        "--haircut",
        default="auto",
        help="share cut from Vinted sold prices (the last ask seen is not what was paid): 'auto' = the code's "
        "default (none in the baseline), or a number such as 0 / 0.05 / 0.10",
    )
    ap.add_argument("--out", help="write the JSON report here")
    ap.add_argument("--rows", action="store_true", help="include every row in the JSON")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    haircut = default_haircut() if args.haircut == "auto" else float(args.haircut)
    t0 = time.time()
    rows: list[dict[str, Any]] = []
    for s in seeds:
        rows += run_seed(s, args.per_group, args.bargain, not args.no_vision, haircut)
    res: dict[str, Any] = {
        "label": LABEL,
        "synthetic": True,
        "args": {
            "seeds": seeds,
            "per_group": args.per_group,
            "bargain": args.bargain,
            "vision": not args.no_vision,
            "haircut": haircut,
        },
        "code": git_info(),
        "constants": {
            "CANDIDATE_LIMIT": P.CANDIDATE_LIMIT,
            "MODEL_SOLD_LIMIT": getattr(P, "MODEL_SOLD_LIMIT", None),
            "MODEL_ACTIVE_LIMIT": getattr(P, "MODEL_ACTIVE_LIMIT", None),
        },
        "elapsed_s": round(time.time() - t0, 1),
        "groups": report(rows),
    }
    if args.rows:
        res["rows"] = rows
    print_report(res)
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=1, default=str))
        print(f"written {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
