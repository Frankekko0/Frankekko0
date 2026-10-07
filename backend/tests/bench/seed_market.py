"""Synthetic Vinted-like market for speed measurements (benchmark sintetico).

Fills a THROWAWAY database with sold, active and removed listings for every brand, category
and product line of the taxonomy, over the last 120 days, at plausible second-hand price levels,
so the instant verdict (market summary: at least 5 sales per brand x category) and the server
analysis find comparables like they would on a real, well-populated FlipFinder. It exists only to
measure speed: no number measured on it is a real market price.

Every row is labelled: ``raw = {"synthetic": true, "label": "benchmark sintetico", ...}``, Vinted
IDs from ``SYNTHETIC_ID_BASE`` up (far above real ones), the description says so. Running it again
replaces the previous synthetic rows (same seed = same market; dates are relative to ``--now``).

Usage (from backend/):

    python -m tests.bench.seed_market \\
        --database-url postgresql+asyncpg://flipfinder:flipfinder@localhost:5432/flipfinder_baseline \\
        [--per-segment 40] [--seed 1] [--redis-url redis://localhost:6379/9]

The schema must be migrated (``alembic upgrade head``) and the catalog synced (``python -m app.seed``,
done here too). After the insert it recomputes the market statistics and, when the code provides
them, the concluded sales and the per-model statistics, then invalidates the cached summaries.
Refuses a database whose name does not look like a throwaway one, or one holding purchases/sales.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import random
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

SYNTHETIC_ID_BASE = 9_000_000_000_000_000  # real Vinted IDs are around 10^10
WINDOW_DAYS = 120
THROWAWAY_MARKERS = ("bench", "baseline", "speed", "test", "e2e", "tmp", "throwaway", "synthetic")

# Status mix of a segment: sold listings carry the realized prices, active ones the asks,
# removed ones (never sold) the overpriced tail.
STATUS_MIX = (("sold", 0.45), ("active", 0.40), ("removed", 0.15))
# (normalized condition, label shown by Vinted Italy, share)
CONDITION_MIX = (
    ("new_with_tags", "Nuovo con cartellino", 0.10),
    ("new_without_tags", "Nuovo senza cartellino", 0.12),
    ("very_good", "Ottime condizioni", 0.43),
    ("good", "Buone condizioni", 0.28),
    ("satisfactory", "Discrete condizioni", 0.07),
)
# Same multipliers as app.pricing.comparables.CONDITION_MULTIPLIER (kept in sync by the import
# check in ``_condition_multipliers``).
COLORS = (
    ("black", "nero"),
    ("white", "bianco"),
    ("navy", "blu navy"),
    ("blue", "blu"),
    ("grey", "grigio"),
    ("green", "verde"),
    ("red", "rosso"),
    ("beige", "beige"),
    ("brown", "marrone"),
    ("burgundy", "bordeaux"),
)
# Italian nouns sellers use in titles, each a keyword of its category in the taxonomy.
CATEGORY_WORDS: dict[str, tuple[str, ...]] = {
    "hoodies": ("Felpa con cappuccio", "Hoodie"),
    "sweatshirts": ("Felpa", "Felpa girocollo", "Sweatshirt"),
    "polo-shirts": ("Polo",),
    "t-shirts": ("T-shirt", "Maglietta"),
    "shirts": ("Camicia",),
    "knitwear": ("Maglione", "Cardigan", "Pullover"),
    "football-shirts": ("Maglia da calcio", "Maglia calcio"),
    "jackets": ("Giacca", "Giubbotto", "Bomber"),
    "coats": ("Cappotto", "Parka"),
    "puffer-jackets": ("Piumino", "Puffer"),
    "fleece": ("Pile", "Fleece"),
    "jeans": ("Jeans",),
    "trousers": ("Pantaloni", "Chino", "Cargo"),
    "tracksuits": ("Tuta", "Pantaloni tuta", "Track jacket"),
    "sneakers": ("Sneakers", "Scarpe"),
    "bags": ("Borsa", "Zaino"),
    "belts": ("Cintura",),
    "scarves": ("Sciarpa",),
    "caps": ("Cappellino", "Berretto", "Beanie"),
}
# fmt: off
LETTERS = (("XS", 0.05), ("S", 0.18), ("M", 0.32), ("L", 0.27), ("XL", 0.13), ("XXL", 0.05))
WAISTS = tuple((f"W{w}", wgt) for w, wgt in ((28, 0.08), (29, 0.08), (30, 0.16), (31, 0.14), (32, 0.2), (33, 0.1), (34, 0.14), (36, 0.1)))
SHOES = tuple((f"EU{s}", wgt) for s, wgt in ((38, 0.05), (39, 0.08), (40, 0.11), (41, 0.13), (42, 0.16), (43, 0.15), (44, 0.13), (45, 0.11), (46, 0.08)))
ONE_SIZE = (("ONESIZE", 1.0),)
SIZE_SYSTEM: dict[str, tuple[tuple[str, float], ...]] = {
    "jeans": WAISTS,
    "trousers": WAISTS,
    "sneakers": SHOES,
    "bags": ONE_SIZE,
    "belts": ONE_SIZE,
    "scarves": ONE_SIZE,
    "caps": ONE_SIZE,
}

# Median used price (EUR, "ottime condizioni") per brand and category: the categories each
# brand actually sells on Vinted, at second-hand price levels.
BRAND_PRICES: dict[str, dict[str, float]] = {
    "ralph-lauren": {"polo-shirts": 24, "shirts": 22, "knitwear": 32, "sweatshirts": 28, "hoodies": 30, "t-shirts": 15, "jackets": 55, "trousers": 22, "caps": 15},
    "nike": {"sneakers": 50, "hoodies": 25, "sweatshirts": 22, "t-shirts": 12, "tracksuits": 35, "jackets": 30, "trousers": 20, "caps": 10, "bags": 15, "football-shirts": 30},
    "adidas": {"sneakers": 40, "tracksuits": 30, "sweatshirts": 22, "hoodies": 22, "t-shirts": 12, "jackets": 28, "trousers": 18, "football-shirts": 28, "caps": 10, "bags": 15},
    "the-north-face": {"puffer-jackets": 120, "jackets": 60, "fleece": 35, "hoodies": 28, "sweatshirts": 25, "t-shirts": 14, "bags": 30, "caps": 14, "coats": 80},
    "carhartt": {"jackets": 62, "trousers": 35, "sweatshirts": 30, "hoodies": 35, "t-shirts": 15, "caps": 15, "shirts": 30, "coats": 70},
    "stone-island": {"jackets": 140, "sweatshirts": 85, "hoodies": 95, "knitwear": 90, "shirts": 70, "trousers": 60, "t-shirts": 35, "caps": 35, "puffer-jackets": 190},
    "patagonia": {"fleece": 45, "jackets": 70, "puffer-jackets": 80, "sweatshirts": 30, "t-shirts": 18, "caps": 15, "bags": 35},
    "levis": {"jeans": 24, "jackets": 30, "t-shirts": 10, "shirts": 18, "sweatshirts": 18, "trousers": 20},
    "tommy-hilfiger": {"polo-shirts": 15, "shirts": 15, "sweatshirts": 18, "hoodies": 20, "knitwear": 18, "jackets": 35, "jeans": 18, "t-shirts": 10, "caps": 10, "puffer-jackets": 45},
    "lacoste": {"polo-shirts": 25, "sweatshirts": 30, "hoodies": 32, "knitwear": 30, "t-shirts": 15, "tracksuits": 40, "jackets": 50, "sneakers": 30, "caps": 15},
    "arcteryx": {"jackets": 180, "fleece": 90, "puffer-jackets": 170, "t-shirts": 30, "caps": 25, "bags": 70, "hoodies": 70},
    "moncler": {"puffer-jackets": 290, "jackets": 220, "coats": 300, "hoodies": 120, "sweatshirts": 110, "polo-shirts": 70, "t-shirts": 60, "knitwear": 150, "caps": 60},
    "stussy": {"t-shirts": 25, "hoodies": 55, "sweatshirts": 45, "caps": 25, "jackets": 70, "shirts": 40, "trousers": 40},
    "new-balance": {"sneakers": 55, "sweatshirts": 25, "hoodies": 28, "t-shirts": 12, "tracksuits": 30},
    "burberry": {"coats": 220, "scarves": 90, "shirts": 60, "polo-shirts": 50, "jackets": 160, "knitwear": 90, "t-shirts": 45, "caps": 50, "bags": 150, "belts": 70},
    "cp-company": {"jackets": 140, "sweatshirts": 75, "hoodies": 85, "shirts": 60, "trousers": 60, "t-shirts": 35, "caps": 35, "knitwear": 70, "polo-shirts": 45},
    "champion": {"sweatshirts": 20, "hoodies": 22, "t-shirts": 10, "tracksuits": 25, "jackets": 25},
    "dickies": {"trousers": 22, "jackets": 32, "shirts": 18, "t-shirts": 10, "sweatshirts": 20, "hoodies": 22},
    "barbour": {"jackets": 110, "coats": 120, "knitwear": 40, "shirts": 30, "polo-shirts": 25, "puffer-jackets": 70, "caps": 25, "scarves": 25},
    "napapijri": {"jackets": 45, "puffer-jackets": 60, "sweatshirts": 25, "hoodies": 28, "polo-shirts": 18, "t-shirts": 12, "fleece": 25},
    "fred-perry": {"polo-shirts": 25, "t-shirts": 15, "sweatshirts": 30, "jackets": 45, "knitwear": 35, "shirts": 25, "tracksuits": 40},
    "supreme": {"hoodies": 120, "t-shirts": 45, "sweatshirts": 100, "caps": 40, "jackets": 140, "bags": 60, "trousers": 60},
    "gucci": {"belts": 180, "sneakers": 200, "bags": 450, "t-shirts": 120, "caps": 110, "scarves": 120, "shirts": 150, "jackets": 400},
    "puma": {"sneakers": 30, "tracksuits": 25, "sweatshirts": 18, "hoodies": 20, "t-shirts": 10, "football-shirts": 25, "jackets": 22},
    "umbro": {"football-shirts": 30, "tracksuits": 25, "jackets": 25, "sweatshirts": 18, "t-shirts": 10},
    "kappa": {"tracksuits": 28, "football-shirts": 30, "jackets": 25, "sweatshirts": 18, "t-shirts": 10},
    "zara": {"jackets": 18, "coats": 22, "shirts": 8, "knitwear": 10, "t-shirts": 5, "trousers": 10, "jeans": 10, "sweatshirts": 9, "puffer-jackets": 20, "sneakers": 12, "bags": 10},
    "h-m": {"jackets": 12, "coats": 15, "shirts": 6, "knitwear": 7, "t-shirts": 4, "trousers": 7, "jeans": 8, "sweatshirts": 7, "hoodies": 8, "puffer-jackets": 14},
}
# Median used price of the product lines (models) of the taxonomy.
LINE_PRICES: dict[tuple[str, str], float] = {
    ("ralph-lauren", "Custom Slim Fit"): 25, ("ralph-lauren", "Classic Fit"): 24, ("ralph-lauren", "Big Pony"): 30,
    ("ralph-lauren", "Polo Bear"): 90, ("ralph-lauren", "Cable Knit"): 45, ("ralph-lauren", "Oxford"): 25,
    ("ralph-lauren", "Harrington"): 70, ("ralph-lauren", "Half Zip"): 35,
    ("nike", "Air Force 1"): 55, ("nike", "Dunk Low"): 70, ("nike", "Air Max 90"): 58, ("nike", "Air Max 95"): 75,
    ("nike", "Air Jordan 1"): 110, ("nike", "Tech Fleece"): 55, ("nike", "Center Swoosh"): 35, ("nike", "ACG"): 70,
    ("adidas", "Samba"): 55, ("adidas", "Gazelle"): 50, ("adidas", "Spezial"): 55, ("adidas", "Superstar"): 35,
    ("adidas", "Firebird"): 35, ("adidas", "Trefoil"): 25,
    ("the-north-face", "Nuptse 700"): 130, ("the-north-face", "Himalayan"): 170, ("the-north-face", "Denali"): 55,
    ("the-north-face", "Mountain Jacket"): 85,
    ("carhartt", "Detroit Jacket"): 85, ("carhartt", "Active Jacket"): 70, ("carhartt", "Double Knee"): 45,
    ("carhartt", "Chase"): 35, ("carhartt", "Michigan Coat"): 80,
    ("stone-island", "Ghost Piece"): 230, ("stone-island", "Shadow Project"): 300, ("stone-island", "Soft Shell-R"): 190,
    ("stone-island", "Overshirt"): 110,
    ("patagonia", "Retro-X"): 75, ("patagonia", "Synchilla"): 45, ("patagonia", "Better Sweater"): 50,
    ("patagonia", "Torrentshell"): 60, ("patagonia", "Down Sweater"): 90,
    ("levis", "501"): 24, ("levis", "505"): 22, ("levis", "511"): 20, ("levis", "Trucker Jacket"): 35,
    ("lacoste", "L.12.12"): 30,
    ("arcteryx", "Beta"): 230, ("arcteryx", "Atom"): 150, ("arcteryx", "Alpha SV"): 300,
    ("moncler", "Maya"): 320, ("moncler", "Grenoble"): 280,
    ("stussy", "8 Ball"): 70,
    ("new-balance", "550"): 60, ("new-balance", "2002R"): 70, ("new-balance", "990"): 85, ("new-balance", "9060"): 85,
    ("new-balance", "574"): 35,
    ("burberry", "Trench"): 260, ("burberry", "Nova Check"): 110,
    ("cp-company", "Goggle"): 170, ("cp-company", "Lens"): 90,
    ("champion", "Reverse Weave"): 28,
    ("dickies", "874"): 22,
    ("barbour", "Bedale"): 120, ("barbour", "Beaufort"): 125, ("barbour", "International"): 130,
    ("napapijri", "Skidoo"): 55,
    ("fred-perry", "M12"): 30,
    ("supreme", "Box Logo"): 220,
    ("gucci", "GG Marmont"): 200, ("gucci", "Ace"): 210,
}
# Generic price level by brand tier for a category missing from BRAND_PRICES (a line's category).
TIER_FACTOR = {"fast_fashion": 0.45, "mid": 0.9, "sport": 1.0, "streetwear": 1.2, "outdoor": 1.4, "premium": 1.6, "luxury": 5.0}
CATEGORY_BASE = {
    "t-shirts": 12, "polo-shirts": 18, "shirts": 18, "sweatshirts": 25, "hoodies": 28, "knitwear": 25,
    "football-shirts": 30, "jackets": 45, "coats": 55, "puffer-jackets": 60, "fleece": 30, "jeans": 22,
    "trousers": 22, "tracksuits": 30, "sneakers": 45, "bags": 30, "belts": 18, "scarves": 18, "caps": 12,
}
# fmt: on


SYNTHETIC = "raw->>'synthetic' = 'true'"
DELETE_SYNTHETIC = "DELETE FROM listings WHERE raw->>'synthetic' = 'true'"
DELETE_SYNTHETIC_SALES = (
    "DELETE FROM sold_sales WHERE listing_id IN (SELECT id FROM listings WHERE raw->>'synthetic' = 'true')"
)
TIMELINE_COLUMNS = (
    "published_at",
    "first_seen_at",
    "last_seen_at",
    "status_changed_at",
    "sold_at",
    "sold_detected_at",
    "last_active_at",
    "last_active_price",
    "days_to_sell",
    "removed_at",
)


@dataclass(frozen=True)
class Segment:
    brand_slug: str
    brand_name: str
    category: str
    category_name: str  # as Vinted Italy shows it
    line: str | None
    median: float
    baseline_days: int


def _weighted(
    rng: random.Random, options: tuple[tuple[Any, float], ...] | tuple[tuple[Any, ...], ...]
) -> Any:
    """Pick from (value, ..., weight) tuples: the weight is the last element."""
    total = sum(o[-1] for o in options)
    x = rng.random() * total
    for o in options:
        x -= o[-1]
        if x <= 0:
            return o
    return options[-1]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def _round_price(value: float) -> Decimal:
    """Prices as sellers set them: whole euros, half euros below 10, never under 3."""
    value = max(3.0, value)
    step = Decimal("0.5") if value < 10 else Decimal("1")
    return (Decimal(str(value)) / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step


def segments() -> list[Segment]:
    """Every brand x category of BRAND_PRICES plus every product line of the taxonomy."""
    from app.identification.taxonomy import BRANDS, CATEGORIES

    cats = {c.slug: c for c in CATEGORIES}
    out: list[Segment] = []
    for brand in BRANDS:
        prices = BRAND_PRICES.get(brand.slug, {})
        for slug, median in prices.items():
            cat = cats[slug]
            out.append(
                Segment(
                    brand.slug, brand.name, slug, cat.name_it, None, float(median), cat.baseline_days_to_sell
                )
            )
        for line in brand.lines:
            cat = cats[line.category]
            median = LINE_PRICES.get((brand.slug, line.name))
            if median is None:
                median = CATEGORY_BASE[line.category] * TIER_FACTOR.get(brand.tier, 1.0)
            out.append(
                Segment(
                    brand.slug,
                    brand.name,
                    line.category,
                    cat.name_it,
                    line.name,
                    float(median),
                    cat.baseline_days_to_sell,
                )
            )
    return out


def _condition_multipliers() -> dict[str, float]:
    from app.pricing.comparables import CONDITION_MULTIPLIER

    return {str(k): float(v) for k, v in CONDITION_MULTIPLIER.items()}


def _title(rng: random.Random, seg: Segment, color_it: str) -> str:
    """Titles as sellers write them: "Polo Ralph Lauren Custom Slim Fit blu", "Nike Air Max 90
    bianco" (line names contain their taxonomy keyword, nouns are category keywords)."""
    model = f" {seg.line}" if seg.line else ""
    extra = rng.choice(("", "", "", " vintage", " originale", " uomo", " slim"))
    if seg.category == "sneakers" and seg.line and rng.random() < 0.5:
        return f"{seg.brand_name}{model} {color_it}{extra}"[:300]
    return f"{rng.choice(CATEGORY_WORDS[seg.category])} {seg.brand_name}{model} {color_it}{extra}"[:300]


def generate(
    seed: int, per_segment: int, now: datetime, brand_ids: dict[str, int], category_ids: dict[str, int]
) -> list[dict[str, Any]]:
    """The synthetic listings as rows of the ``listings`` table (deterministic for a seed)."""
    rng = random.Random(seed)
    mult = _condition_multipliers()
    rows: list[dict[str, Any]] = []
    seq = 0
    for seg in segments():
        brand_id = brand_ids.get(seg.brand_slug)
        category_id = category_ids.get(seg.category)
        if brand_id is None or category_id is None:
            continue  # catalog not synced for this brand/category
        # A line is a slice of its brand x category: 80% as many listings.
        n = per_segment if seg.line is None else max(5, round(per_segment * 0.8))
        for _ in range(n):
            seq += 1
            status = _weighted(rng, STATUS_MIX)[0]
            condition, condition_label, _w = _weighted(rng, CONDITION_MIX)
            fair = seg.median * mult[condition]
            if status == "sold":
                price_f = fair * math.exp(rng.gauss(0, 0.22))
            elif status == "active":
                price_f = fair / 0.88 * math.exp(rng.gauss(0, 0.30))  # asks sit above realized prices
            else:
                price_f = fair / 0.80 * math.exp(rng.gauss(0, 0.30))  # the overpriced tail never sells
            price = _round_price(price_f)
            color, color_it = rng.choice(COLORS)
            size = _weighted(rng, SIZE_SYSTEM.get(seg.category, LETTERS))[0]
            title = _title(rng, seg, color_it)
            size_raw = "Taglia unica" if size == "ONESIZE" else size.removeprefix("EU").removeprefix("W")
            external_id = str(SYNTHETIC_ID_BASE + seq)
            row: dict[str, Any] = {
                "id": uuid.UUID(int=rng.getrandbits(128), version=4),
                "provider": "vinted",
                "external_id": external_id,
                "url": f"https://www.vinted.it/items/{external_id}-{_slug(title)}",
                "title": title,
                "description": f"{condition_label}, taglia {size}. Annuncio sintetico (benchmark sintetico): "
                "generato solo per misurare la velocità, non è un annuncio reale.",
                "price": price,
                "currency": "EUR",
                "brand_raw": seg.brand_name,
                "brand_id": brand_id,
                "category_raw": seg.category_name,
                "category_id": category_id,
                "size_raw": size_raw,
                "size_normalized": size,
                "condition_raw": condition_label,
                "condition": condition,
                "color_raw": color_it,
                "color": color,
                "country": "IT",
                "model_name": seg.line,
                "gender": "women" if rng.random() < 0.25 else "men",
                "identification_confidence": 80,
                "favourite_count": 0,
                "view_count": 0,
                "photo_count": 1,
                "status": status,
                "acquisition_mode": "provider_scan",
                "capture_level": "card",
                "raw": {
                    "synthetic": True,
                    "label": "benchmark sintetico",
                    "generator": "tests.bench.seed_market",
                    "seed": seed,
                },
                # Every row carries every column of the timeline (one executemany statement).
                **dict.fromkeys(TIMELINE_COLUMNS),
            }
            _timeline(rng, row, seg, fair, float(price), now)
            rows.append(row)
    return rows


def _timeline(
    rng: random.Random, row: dict[str, Any], seg: Segment, fair: float, price: float, now: datetime
) -> None:
    """Publication, sightings, sale or removal consistent with each other and with the price:
    cheaper than the segment sells faster, every date in the past, sold_at = midpoint of the
    window between the last time seen on sale and the first time seen sold."""
    day = timedelta(days=1)
    status = row["status"]
    if status == "sold":
        speed = (price / fair) ** 1.5 if fair > 0 else 1.0
        days = min(90.0, max(0.2, seg.baseline_days * speed * math.exp(rng.gauss(0, 0.6))))
        sold_at = now - rng.uniform(0.3, WINDOW_DAYS - 2) * day
        published = sold_at - days * day
        gap = rng.uniform(0.05, 0.5) * day
        last_active = sold_at - gap
        detected = min(now, sold_at + gap)
        first_seen = published + min(rng.uniform(0.05, 1.5), days / 2) * day
        favs = int(rng.expovariate(1 / 9))
        row.update(
            published_at=published,
            first_seen_at=first_seen,
            last_seen_at=detected,
            status_changed_at=detected,
            sold_at=sold_at,
            sold_detected_at=detected,
            last_active_at=max(first_seen, last_active),
            last_active_price=Decimal(str(price)),
            days_to_sell=Decimal(str(round((sold_at - published) / day, 1))),
        )
    elif status == "active":
        age = rng.uniform(0.1, 30) if rng.random() < 0.6 else rng.uniform(30, WINDOW_DAYS)
        published = now - age * day
        first_seen = published + min(rng.uniform(0.05, 1.0), age / 2) * day
        favs = int(rng.expovariate(1 / 4))
        row.update(
            published_at=published,
            first_seen_at=first_seen,
            last_seen_at=max(first_seen, now - rng.uniform(0, 2) * day),
            last_active_at=max(first_seen, now - rng.uniform(0, 2) * day),
            last_active_price=Decimal(str(price)),
        )
    else:
        published = now - rng.uniform(15, WINDOW_DAYS) * day
        removed = min(now - 0.5 * day, published + rng.uniform(10, 60) * day)
        first_seen = published + rng.uniform(0.05, 1.0) * day
        favs = int(rng.expovariate(1 / 2))
        row.update(
            published_at=published,
            first_seen_at=first_seen,
            last_seen_at=removed,
            status_changed_at=removed,
            removed_at=removed,
            last_active_at=removed - rng.uniform(0.1, 1.0) * day,
            last_active_price=Decimal(str(price)),
        )
    row["favourite_count"] = favs
    row["view_count"] = favs * rng.randint(8, 20) + rng.randint(10, 60)


def _check_throwaway(url: str, force: bool) -> str:
    from sqlalchemy.engine import make_url

    name = make_url(url).database or ""
    if not force and not any(m in name.lower() for m in THROWAWAY_MARKERS):
        sys.exit(
            f"Rifiutato: il database '{name}' non sembra usa-e-getta (il nome deve contenere uno di "
            f"{', '.join(THROWAWAY_MARKERS)}). Il mercato sintetico non va mai nel database dell'utente."
        )
    return name


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from sqlalchemy import func, select, text
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.analytics.market_stats import recompute_market_statistics
    from app.core.config import get_settings
    from app.db.models import Brand, Category, Listing
    from app.seed import sync_catalog

    engine = create_async_engine(args.database_url, pool_size=2, max_overflow=0)
    make = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    report: dict[str, Any] = {"database": make_url_name(args.database_url), "label": "benchmark sintetico"}
    t0 = time.perf_counter()
    try:
        async with make() as s:
            own = (
                await s.execute(
                    text("SELECT (SELECT count(*) FROM purchases) + (SELECT count(*) FROM sales)")
                )
            ).scalar_one()
            if own and not args.force:
                sys.exit(f"Rifiutato: il database contiene {own} acquisti/vendite (dati reali di un utente).")
            await sync_catalog(s)
            await s.commit()
            brand_ids = {r.slug: r.id for r in (await s.execute(select(Brand.slug, Brand.id))).all()}
            category_ids = {r.slug: r.id for r in (await s.execute(select(Category.slug, Category.id))).all()}

        now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        rows = generate(args.seed, args.per_segment, now, brand_ids, category_ids)

        async with make() as s:
            # Concluded sales derived from a previous synthetic market go with it.
            await s.execute(text(DELETE_SYNTHETIC_SALES))
            removed = (await s.execute(text(DELETE_SYNTHETIC))).rowcount
            table = Listing.__table__
            for start in range(0, len(rows), 1000):
                await s.execute(pg_insert(table), rows[start : start + 1000])
            await s.commit()
            await s.execute(text("ANALYZE listings"))  # fresh planner statistics after a bulk insert
            await s.commit()
            counts = dict(
                (
                    await s.execute(
                        select(Listing.status, func.count()).where(text(SYNTHETIC)).group_by(Listing.status)
                    )
                ).all()
            )
        report.update(replaced=removed, inserted=len(rows), by_status=counts, segments=len(segments()))
        report["insert_s"] = round(time.perf_counter() - t0, 2)

        t1 = time.perf_counter()
        async with make() as s:
            report["market_statistics"] = await recompute_market_statistics(
                s, get_settings().market_stats_window_days
            )
            await s.commit()
        report["market_statistics_s"] = round(time.perf_counter() - t1, 2)
        report["evidence"] = await _price_evidence(make)
        report["cache"] = await _bump_cache()
    finally:
        await engine.dispose()
    report["total_s"] = round(time.perf_counter() - t0, 2)
    return report


def make_url_name(url: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(url).database or ""


async def _price_evidence(make: Any) -> dict[str, Any]:
    """Concluded sales and per-model statistics, when the code has them (not before 0009 code)."""
    out: dict[str, Any] = {}
    try:
        from app.market.model_stats import recompute_model_stats
        from app.market.sold_sales import sync_sold_sales
    except ImportError:
        return {"available": False}
    for name, call in (
        ("sold_sales", lambda s: sync_sold_sales(s, full=True)),
        ("model_stats", lambda s: recompute_model_stats(s)),
    ):
        t = time.perf_counter()
        try:
            async with make() as s:
                result = await call(s)
                await s.commit()
            out[name] = {"result": result, "s": round(time.perf_counter() - t, 2)}
        except NotImplementedError:
            out[name] = "non disponibile nel codice attuale"
        except Exception as exc:  # reported, the synthetic market itself is in place
            out[name] = f"errore: {type(exc).__name__}: {exc}"[:300]
    return out


async def _bump_cache() -> str:
    """Market summaries are cached in Redis (15 min): drop them so the new market is served."""
    try:
        from app.core.cache import NS_FEED, NS_MARKET, cache
        from app.core.redis import close_redis

        for ns in (NS_FEED, NS_MARKET):
            await cache.bump(ns)
        await close_redis()
        return "invalidata"
    except Exception as exc:
        return f"non invalidata ({type(exc).__name__})"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Synthetic Vinted-like market for speed measurements (benchmark sintetico)."
    )
    p.add_argument("--database-url", required=True, help="postgresql+asyncpg://... of a THROWAWAY database")
    p.add_argument("--per-segment", type=int, default=40, help="listings per brand x category (lines: 80%%)")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--now", default=None, help="reference time (ISO), default now: dates are relative to it")
    p.add_argument(
        "--redis-url", default=None, help="Redis of the API under test (cached summaries invalidated)"
    )
    p.add_argument("--force", action="store_true", help="skip the throwaway-database checks")
    args = p.parse_args(argv)
    if args.per_segment < 5:
        p.error("--per-segment must be at least 5")
    _check_throwaway(args.database_url, args.force)
    # The app reads its settings from the environment: point them at the database under test.
    os.environ["DATABASE_URL"] = args.database_url
    if args.redis_url:
        os.environ["REDIS_URL"] = args.redis_url
    os.environ.setdefault("LOG_JSON", "false")
    report = asyncio.run(run(args))
    for key, value in report.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
