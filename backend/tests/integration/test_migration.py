"""Migrations on a database with pre-existing data: nothing real is lost (0001 -> 0006), and the
generated demo market goes away (0007)."""

import json
import os
import subprocess
import sys
import uuid

import asyncpg
import pytest

from tests.conftest import ROOT, TEST_DB

ADMIN_DSN = TEST_DB.replace("postgresql+asyncpg://", "postgresql://")
MIG_DB = ADMIN_DSN.rsplit("/", 1)[1] + "_migration"  # one per test database (parallel runs)
MIG_DSN = ADMIN_DSN.rsplit("/", 1)[0] + f"/{MIG_DB}"


def alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": MIG_DSN.replace("postgresql://", "postgresql+asyncpg://")}
    subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, check=True, capture_output=True
    )


@pytest.fixture
async def legacy_db():
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
    await admin.execute(f"CREATE DATABASE {MIG_DB}")
    await admin.close()
    alembic("upgrade", "0001")
    conn = await asyncpg.connect(MIG_DSN)
    yield conn
    await conn.close()
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
    await admin.close()


async def test_upgrade_keeps_every_listing_price_and_analysis(legacy_db) -> None:
    c = legacy_db
    seller = uuid.uuid4()
    await c.execute(
        "INSERT INTO sellers (id, provider, external_id, username, rating, review_count, country)"
        " VALUES ($1, 'manual', 'manual:armadio_8832', 'armadio_8832', 4.8, 31, 'IT')",
        seller,
    )
    ids = {k: uuid.uuid4() for k in ("vinted", "gone", "other", "mock")}
    rows = [
        (
            ids["vinted"],
            "manual",
            "4242",
            "https://www.vinted.it/items/4242-felpa",
            "active",
            {"source": "manual_import"},
        ),
        (
            ids["gone"],
            "manual",
            "4343",
            "https://www.vinted.fr/items/4343-pull",
            "possibly_sold",
            {"source": "vinted_search_import"},
        ),
        (
            ids["other"],
            "manual",
            "abc",
            "https://example.com/items/abc",
            "active",
            {"source": "manual_import"},
        ),
        (ids["mock"], "mock", "77", "https://demo.local/items/77", "sold", None),
    ]
    for lid, provider, ext, url, status, raw in rows:
        await c.execute(
            "INSERT INTO listings (id, provider, external_id, url, title, price, status, raw, seller_id,"
            " published_at, sold_at, status_changed_at)"
            " VALUES ($1, $2, $3, $4, 'Felpa', 20, $5::varchar, $6::jsonb, $7, now() - interval '5 days',"
            " CASE WHEN $5::varchar = 'sold' THEN now() - interval '1 day' END, now() - interval '2 days')",
            lid,
            provider,
            ext,
            url,
            status,
            None if raw is None else json.dumps(raw),
            seller,
        )
    for price in (25, 22):
        await c.execute(
            "INSERT INTO listing_price_history (listing_id, price, observed_at) VALUES ($1, $2, now() - interval '3 days')",
            ids["vinted"],
            price,
        )
    await c.execute(
        "INSERT INTO opportunities (id, listing_id, algorithm_version, listing_price, currency, total_acquisition_cost,"
        " flip_score, confidence_score, risk_score, risk_level, deal_tier, verdict, recommended_action,"
        " comparables_count, sold_comparables_count, score_breakdown, explanation, risk_factors, market_snapshot)"
        " VALUES ($1, $2, '2026.10-1', 20, 'EUR', 23, 81, 70, 20, 'low', 'excellent', 'BUY', 'buy_now',"
        " 12, 5, '{}', '[]', '[]', '{}')",
        uuid.uuid4(),
        ids["vinted"],
    )

    alembic("upgrade", "0006")

    assert await c.fetchval("SELECT count(*) FROM listings") == 4
    assert await c.fetchval("SELECT count(*) FROM opportunities") == 1
    # Every past price is still there, as snapshots, plus the state of each listing.
    assert (
        await c.fetchval("SELECT count(*) FROM listing_snapshots WHERE listing_id = $1", ids["vinted"]) == 3
    )
    assert await c.fetchval("SELECT count(*) FROM listing_snapshots") == 2 + 4
    by_id = {r["id"]: r for r in await c.fetch("SELECT * FROM listings")}
    v, gone, other, mock = (by_id[ids[k]] for k in ("vinted", "gone", "other", "mock"))
    # Vinted links -> single provider (dedup by Vinted ID), others untouched.
    assert (v["provider"], gone["provider"], other["provider"]) == ("vinted", "vinted", "manual")
    assert (v["acquisition_mode"], gone["acquisition_mode"], mock["acquisition_mode"]) == (
        "manual_form",
        "batch_import",
        "provider_scan",
    )
    assert gone["capture_level"] == "card" and v["capture_level"] == "full"
    assert v["tracked_at"] is not None and mock["tracked_at"] is None
    # A disappearance is not a sale anymore.
    assert gone["status"] == "removed" and gone["removed_at"] is not None and gone["sold_at"] is None
    assert mock["days_to_sell"] is not None and mock["next_check_at"] is None
    assert v["next_check_at"] is not None
    # Seller: rating and reviews kept, nothing else personal.
    s = await c.fetchrow("SELECT * FROM sellers")
    assert (
        s["provider"] == "vinted" and s["external_id"].startswith("h:") and "armadio" not in s["external_id"]
    )
    assert float(s["rating"]) == 4.8 and s["review_count"] == 31 and s["country"] is None
    assert "username" not in dict(s)  # asyncpg Record: membership tests values, dict() tests keys
    assert await c.fetchval("SELECT acquisition_mode FROM opportunities") == "manual_form"

    # 0007: the demo market and the demo account are purged, real listings and users stay.
    await c.execute(
        "INSERT INTO users (id, email, password_hash, is_active, created_at, updated_at)"
        " VALUES ($1, 'demo@flipfinder.app', 'x', true, now(), now()),"
        " ($2, 'me@example.com', 'x', true, now(), now())",
        uuid.uuid4(),
        uuid.uuid4(),
    )
    await c.execute(
        "INSERT INTO system_state (key, value, updated_at) VALUES ('price_calibration', '{}', now())"
    )
    alembic("upgrade", "head")
    left = {r["id"] for r in await c.fetch("SELECT id FROM listings")}
    assert ids["mock"] not in left and {ids["vinted"], ids["gone"], ids["other"]} <= left
    assert await c.fetchval("SELECT count(*) FROM opportunities") == 1
    assert [r["email"] for r in await c.fetch("SELECT email FROM users")] == ["me@example.com"]
    assert await c.fetchval("SELECT count(*) FROM system_state WHERE key = 'price_calibration'") == 0
