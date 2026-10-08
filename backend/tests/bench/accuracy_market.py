"""BENCHMARK SINTETICO - accuracy of the price estimates with and without the extra evidence.

Every number printed by this script comes from a SIMULATED market ("benchmark sintetico"), never
from real Vinted data. It runs only against the throwaway database ``flipfinder_bench``, which it
wipes and rebuilds with the Alembic migrations; it never touches the user's database.

    python -m tests.bench.accuracy_market                 # both scenarios, text report
    python -m tests.bench.accuracy_market --scenario bad  # biased external data only
    python -m tests.bench.accuracy_market --json

The simulated market (reproducible, seeded):

* 14 models of 4 brands in 3 categories, each with its own true value; some models are common
  on Vinted, some rare (thin evidence: their comparables are mostly other models);
* an item's true value = model value x condition x individual noise; Vinted asks are inflated
  (+18% on average) and noisy; cheaper asks sell more and faster; the price seen when a listing
  is sold is the asked one, while buyers really pay ~8% less (negotiation);
* the user buys underpriced items (paying ~9% below the ask) and resells most of them, receiving
  about the true value; own purchases and resales are in the tracking tables;
* another marketplace (eBay-like) publishes concluded sales of every model with its own noise
  and its own level, dated over the whole year, plus asking prices (inflated) and new prices;
* scenario "bad": the other marketplace's prices are biased (+75%: wrong variants, new items),
  to show that the backtest gate switches external data off.

The report gives concluded sales before/after the sync per source, models resting on >= 5 real
sales, and the error of the estimates (newer half of the sales, no look-ahead) per variant.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

BENCH_DB = os.environ.get(
    "BENCH_DATABASE_URL", "postgresql+asyncpg://flipfinder:flipfinder@localhost:5432/flipfinder_bench"
)
if not BENCH_DB.rsplit("/", 1)[-1].startswith("flipfinder_bench"):
    raise SystemExit("The benchmark runs only on a flipfinder_bench* database.")
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": BENCH_DB,
        "REDIS_URL": os.environ.get("BENCH_REDIS_URL", "redis://localhost:6379/12"),
        "LOG_JSON": "false",
        "LOG_LEVEL": "WARNING",
        "AI_API_KEY": "",
        "JWT_SECRET": "bench-secret-bench-secret-bench-secret-1",
    }
)

from sqlalchemy import select, update  # noqa: E402

from app.analytics.accuracy import fit_price_calibration  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import ExternalPrice, InventoryItem, Listing, Purchase, Sale, User  # noqa: E402
from app.db.session import dispose_engine, get_sessionmaker, session_scope  # noqa: E402
from app.domain.enums import AcquisitionMode  # noqa: E402
from app.external.keys import model_key  # noqa: E402
from app.ingestion.catalog import load_catalog, reset_catalog_cache  # noqa: E402
from app.ingestion.service import IngestionService  # noqa: E402
from app.marketplace.base import ProviderImage, ProviderListing, ProviderSeller  # noqa: E402
from app.seed import sync_catalog  # noqa: E402
from app.tools.price_eval import evaluate_prices, format_report  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LABEL = "BENCHMARK SINTETICO (mercato simulato, non dati reali)"
DAYS = 330  # simulated history

# (brand field, brand slug, model, category field, category title word, value, listings, sizes)
MODELS: list[tuple[str, str, str, str, str, float, int, list[str]]] = [
    ("Nike", "nike", "Air Force 1", "Sneakers", "sneakers", 55, 230, [f"EU {s}" for s in range(38, 46)]),
    ("Nike", "nike", "Dunk Low", "Sneakers", "sneakers", 85, 200, [f"EU {s}" for s in range(38, 46)]),
    ("Nike", "nike", "Air Max 90", "Sneakers", "sneakers", 65, 90, [f"EU {s}" for s in range(38, 46)]),
    ("Nike", "nike", "Air Max 95", "Sneakers", "sneakers", 105, 9, [f"EU {s}" for s in range(38, 46)]),
    ("Nike", "nike", "Air Jordan 1", "Sneakers", "sneakers", 135, 8, [f"EU {s}" for s in range(38, 46)]),
    ("Adidas", "adidas", "Samba", "Sneakers", "sneakers", 62, 220, [f"EU {s}" for s in range(37, 45)]),
    ("Adidas", "adidas", "Gazelle", "Sneakers", "sneakers", 48, 90, [f"EU {s}" for s in range(37, 45)]),
    ("Adidas", "adidas", "Spezial", "Sneakers", "sneakers", 70, 9, [f"EU {s}" for s in range(37, 45)]),
    ("Adidas", "adidas", "Superstar", "Sneakers", "sneakers", 34, 80, [f"EU {s}" for s in range(37, 45)]),
    ("Ralph Lauren", "ralph-lauren", "Custom Slim Fit", "Polo", "polo", 30, 220, ["S", "M", "L", "XL"]),
    ("Ralph Lauren", "ralph-lauren", "Classic Fit", "Polo", "polo", 27, 80, ["S", "M", "L", "XL"]),
    ("Ralph Lauren", "ralph-lauren", "Big Pony", "Polo", "polo", 40, 8, ["S", "M", "L", "XL"]),
    ("The North Face", "the-north-face", "Nuptse 700", "Piumini", "piumino", 145, 90, ["S", "M", "L", "XL"]),
    ("The North Face", "the-north-face", "Himalayan", "Piumini", "piumino", 210, 8, ["S", "M", "L", "XL"]),
]
CONDITIONS = [
    ("Nuovo con cartellino", 1.15, 0.08),
    ("Nuovo senza cartellino", 1.08, 0.10),
    ("Ottime condizioni", 1.00, 0.47),
    ("Buone condizioni", 0.88, 0.30),
    ("Discrete condizioni", 0.70, 0.05),
]
ASK_INFLATION = 1.18
NEGOTIATION = 0.08  # buyers pay ~8% below the exposed price
USER_NEGOTIATION = (0.03, 0.15)  # the user negotiates 3-15% (mean 9%)


@dataclass
class Scenario:
    name: str
    external_level: float  # other marketplace's sale level vs the true value
    external_noise: float
    title: str


SCENARIOS = {
    "good": Scenario("good", 0.96, 0.16, "dati esterni corretti (rumore proprio, livello -4%)"),
    "bad": Scenario("bad", 1.75, 0.30, "dati esterni distorti (+75%: varianti sbagliate, articoli nuovi)"),
}


def _alembic(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        check=True,
        env=os.environ.copy(),
        capture_output=True,
    )


def _lognormal(rng: random.Random, sigma: float) -> float:
    return math.exp(rng.gauss(-sigma * sigma / 2, sigma))


def _condition(rng: random.Random) -> tuple[str, float]:
    x = rng.random()
    for label, mult, p in CONDITIONS:
        x -= p
        if x <= 0:
            return label, mult
    return CONDITIONS[2][0], CONDITIONS[2][1]


async def build_market(scenario: Scenario, seed: int, now: datetime) -> dict[str, Any]:
    """Wipe the bench database and fill it with the simulated market of ``scenario``."""
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")
    reset_catalog_cache()
    rng = random.Random(seed)
    async with session_scope() as s:
        await sync_catalog(s)
    async with session_scope() as s:
        catalog = await load_catalog(s, force=True)
        user = User(email="bench@example.com", password_hash=hash_password("bench-password-1"))
        s.add(user)
        await s.flush()

        listings: list[ProviderListing] = []
        truth: dict[str, dict[str, Any]] = {}
        removed_at: dict[str, datetime] = {}
        n = 0
        for brand, slug, model, cat_field, cat_word, value, count, sizes in MODELS:
            for _ in range(count):
                n += 1
                ext = f"b{n}"
                published = now - timedelta(days=rng.uniform(0, DAYS))
                cond, mult = _condition(rng)
                true = value * mult * _lognormal(rng, 0.12)
                ask = max(5.0, round(true * ASK_INFLATION * _lognormal(rng, 0.14)))
                ratio = ask / true
                p_sell = min(0.9, max(0.05, 0.9 - 1.6 * (ratio - 1.05)))
                status, sold_at = "active", None
                if rng.random() < p_sell:
                    days = rng.expovariate(1 / (6 * ratio * ratio))
                    if published + timedelta(days=days) < now:
                        status, sold_at = "sold", published + timedelta(days=days)
                if status == "active" and (now - published).days > 50:
                    gone = published + timedelta(days=rng.uniform(40, 90))
                    if gone < now:
                        status = "removed"
                        removed_at[ext] = gone
                size = rng.choice(sizes)
                color = rng.choice(["bianche", "nere", "blu", "grigie", "rosse"])
                listings.append(
                    ProviderListing(
                        external_id=ext,
                        url=f"https://www.vinted.it/items/{ext}-bench",
                        title=f"{brand} {model} {cat_word} {color} {size.replace('EU ', '')}",
                        description=f"{brand} {model} originale, {cond.lower()}. Spedizione veloce.",
                        price=Decimal(str(ask)),
                        brand=brand,
                        category=cat_field,
                        size=size,
                        condition=cond,
                        images=[ProviderImage(url=f"/bench/{ext}.jpg", phash=f"{rng.getrandbits(64):016x}")],
                        seller=ProviderSeller(
                            external_id=f"seller{n}", rating=Decimal("4.8"), review_count=40
                        ),
                        country="IT",
                        published_at=published,
                        status=status,  # type: ignore[arg-type]
                        sold_at=sold_at,
                        shipping_fee=Decimal("3.49"),
                        favourite_count=rng.randrange(0, 15),
                    )
                )
                truth[ext] = {
                    "slug": slug,
                    "model": model,
                    "value": value,
                    "true": true,
                    "ask": ask,
                    "status": status,
                    "sold_at": sold_at,
                    "cond": cond,
                    "size": size,
                    "brand": brand,
                    "cat": cat_field,
                }
        listings.sort(key=lambda pl: pl.published_at or now)
        for start in range(0, len(listings), 250):
            await IngestionService(s, "vinted", AcquisitionMode.EXTENSION_CARD, track=False).ingest(
                listings[start : start + 250], now=now
            )
        await s.flush()
        ids = {ext: lid for ext, lid in (await s.execute(select(Listing.external_id, Listing.id))).all()}
        if removed_at:
            await s.execute(
                update(Listing),
                [{"id": ids[ext], "removed_at": when} for ext, when in removed_at.items() if ext in ids],
            )

        # The user's purchases (underpriced items, paid below the ask) and resales.
        purchases = sales = 0
        for ext, t in truth.items():
            if t["status"] != "sold" or t["ask"] >= t["true"] or rng.random() > 0.5:
                continue
            paid = round(t["ask"] * (1 - rng.uniform(*USER_NEGOTIATION)), 2)
            bought = t["sold_at"]
            p = Purchase(
                user_id=user.id,
                listing_id=ids[ext],
                title=f"{t['brand']} {t['model']} {t['size']}",
                brand_id=catalog.brand_id(t["slug"]),
                brand_name=t["brand"],
                size=t["size"],
                condition=t["cond"],
                purchase_price=Decimal(str(paid)),
                total_cost=Decimal(str(paid + 5)),
                purchase_date=bought.date(),
            )
            s.add(p)
            await s.flush()
            purchases += 1
            sold_on = bought + timedelta(days=rng.uniform(6, 40))
            if sold_on < now - timedelta(days=1) and rng.random() < 0.85:
                received = round(t["true"] * (1 - NEGOTIATION / 2) * _lognormal(rng, 0.08), 2)
                s.add(InventoryItem(user_id=user.id, purchase_id=p.id, listed_at=bought + timedelta(days=2)))
                s.add(
                    Sale(
                        user_id=user.id,
                        purchase_id=p.id,
                        sale_price=Decimal(str(received)),
                        net_revenue=Decimal(str(received)),
                        profit=Decimal(str(round(received - paid - 5, 2))),
                        roi=Decimal("0"),
                        holding_days=(sold_on.date() - bought.date()).days,
                        sale_date=sold_on.date(),
                    )
                )
                sales += 1

        # The other marketplace: concluded sales, asks and new prices of every model, over the year.
        ext_rows = 0
        for brand, slug, model, _cat, cat_word, value, count, sizes in MODELS:
            key = model_key(slug, model)
            brand_id = catalog.brand_id(slug)
            n_sold = 40 + count // 5
            for i in range(n_sold + n_sold // 2 + 3):
                kind = "sold" if i < n_sold else "asking" if i < n_sold + n_sold // 2 else "new"
                when = now - timedelta(days=rng.uniform(0, DAYS))
                if kind == "new":
                    price = round(value * 1.9 * _lognormal(rng, 0.05), 2)
                else:
                    level = scenario.external_level * (1.2 if kind == "asking" else 1.0)
                    price = round(value * 0.95 * level * _lognormal(rng, scenario.external_noise), 2)
                url = f"https://www.ebay.it/itm/{slug}-{i}-{rng.getrandbits(32)}"
                dated = rng.random() < 0.7
                s.add(
                    ExternalPrice(
                        dedupe_key=f"bench-{key}-{i}"[:80],
                        model_key=key,
                        brand_id=brand_id,
                        model_name=model,
                        kind=kind,
                        price=Decimal(str(price)),
                        currency="EUR",
                        price_eur=Decimal(str(price)),
                        condition="new" if kind == "new" else "used",
                        size=rng.choice(sizes) if kind != "new" and rng.random() < 0.3 else None,
                        source="zalando.it" if kind == "new" else "ebay.it",
                        source_url=url,
                        title=f"{brand} {model} {cat_word}",
                        provider="serper",
                        query=f"{brand} {model}",
                        observed_at=when,
                        source_date=when if dated else None,
                        match_score=Decimal("0.9"),
                    )
                )
                ext_rows += 1
    return {
        "listings": len(listings),
        "sold_listings": sum(1 for t in truth.values() if t["status"] == "sold"),
        "purchases": purchases,
        "resales": sales,
        "external_rows": ext_rows,
    }


async def run(scenario_name: str, seed: int, max_subjects: int, calibrate: bool) -> dict[str, Any]:
    scenario = SCENARIOS[scenario_name]
    now = datetime.now(UTC).replace(microsecond=0)
    market = await build_market(scenario, seed, now)
    session = get_sessionmaker()()
    try:
        result = await evaluate_prices(session, sync=True, store=True, max_subjects=max_subjects)
        await session.commit()
        if calibrate:
            # The daily job: same backtest, gate, then calibration of the variant now in use.
            cal = await fit_price_calibration(session, max_subjects=max_subjects)
            await session.commit()
            result["calibration"] = {
                k: cal.get(k) for k in ("test_sales", "before", "after_uncalibrated", "after")
            }
    finally:
        await session.close()
    return {
        "label": LABEL,
        "scenario": scenario.name,
        "description": scenario.title,
        "market": market,
        **result,
    }


async def main(scenarios: list[str], seed: int, as_json: bool, max_subjects: int, calibrate: bool) -> None:
    results = []
    try:
        for name in scenarios:
            results.append(await run(name, seed, max_subjects, calibrate))
            await dispose_engine()
    finally:
        await dispose_engine()
    if as_json:
        print(json.dumps(results, indent=1, ensure_ascii=False, default=str))
        return
    for r in results:
        m = r["market"]
        text = format_report(r, f"{LABEL} - scenario '{r['scenario']}': {r['description']}")
        text += (
            f"\n  mercato simulato: {m['listings']} annunci ({m['sold_listings']} venduti), "
            f"{m['purchases']} tuoi acquisti, {m['resales']} tue vendite, {m['external_rows']} prezzi esterni; "
            f"{r['duration_s']} s"
        )
        if cal := r.get("calibration"):

            def mae(d: dict[str, Any]) -> str:
                return f"€{d['mae_eur']:.2f}" if d and d.get("mae_eur") is not None else "n.d."

            text += (
                f"\n  con la calibrazione giornaliera ({cal['test_sales']} vendite Vinted di prova): "
                f"stima precedente {mae(cal['before'])}, variante in uso non calibrata "
                f"{mae(cal['after_uncalibrated'])}, calibrata {mae(cal['after'])} "
                f"(nell'intervallo {round((cal['after'] or {}).get('in_range', 0) * 100)}%)"
            )
        print(text + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scenario", choices=[*SCENARIOS, "both"], default="both")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-subjects", type=int, default=1200)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-calibration", action="store_true", help="skip the daily calibration step")
    args = parser.parse_args()
    chosen = list(SCENARIOS) if args.scenario == "both" else [args.scenario]
    asyncio.run(main(chosen, args.seed, args.json, args.max_subjects, not args.no_calibration))
