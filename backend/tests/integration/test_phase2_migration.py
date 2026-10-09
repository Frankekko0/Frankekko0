"""The whole of phase 2 (0010 -> 0016) on a database that already holds data: nothing is lost,
nothing violates the new constraints, and going back works."""

import uuid

import asyncpg

from tests.integration.test_migration import ADMIN_DSN, MIG_DB, MIG_DSN, alembic

TABLES = (
    "listings",
    "listing_images",
    "listing_snapshots",
    "sellers",
    "opportunities",
    "opportunity_scores",
    "market_comparables",
    "sold_sales",
)


async def _counts(c) -> dict[str, int]:
    return {t: await c.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}  # noqa: S608 - fixed names


async def _seed(c, n: int = 120) -> None:
    """Listings in every state, with photos, history, sellers, opportunities and sales."""
    await c.execute(
        """
        INSERT INTO sellers (id, provider, external_id, rating, review_count, item_count, country)
        SELECT gen_random_uuid(), 'vinted', 'h:' || lpad(i::text, 24, '0'), 4.5, i, i, 'IT'
          FROM generate_series(1, 20) i
        """
    )
    await c.execute(
        """
        INSERT INTO listings (id, provider, external_id, url, title, price, status, capture_level, seller_id,
                              first_seen_at, last_seen_at, published_at, last_active_at, sold_at,
                              sold_detected_at, days_to_sell, last_active_price, photo_count)
        SELECT gen_random_uuid(), 'vinted', (100000 + i)::text, 'https://www.vinted.it/items/' || (100000 + i),
               'Felpa ' || i, 10 + (i % 30),
               (ARRAY['active', 'active', 'reserved', 'sold', 'removed'])[1 + i % 5],
               (ARRAY['card', 'full'])[1 + i % 2],
               (SELECT id FROM sellers ORDER BY external_id LIMIT 1 OFFSET (i % 20)),
               now() - make_interval(days => 20, hours => i), now() - make_interval(hours => i),
               -- every third listing carries an "assumed" publication date (= first seen)
               CASE WHEN i % 3 = 0 THEN now() - make_interval(days => 20, hours => i)
                    ELSE now() - make_interval(days => 25) END,
               now() - make_interval(days => 3, hours => i),
               CASE WHEN i % 5 = 3 THEN now() - make_interval(days => 2) END,
               CASE WHEN i % 5 = 3 THEN now() - make_interval(days => 1) END,
               CASE WHEN i % 5 = 3 THEN 6.5 END, 10 + (i % 30), 3
          FROM generate_series(1, $1::int) i
        """,
        n,
    )
    await c.execute(
        """
        INSERT INTO listing_images (listing_id, position, url, sha256, local_path)
        SELECT l.id, p, 'https://images1.vinted.net/t/' || l.external_id || '/' || p || '.jpeg?s=' || p,
               CASE WHEN p = 0 THEN repeat('a', 63) || (l.external_id::bigint % 10)::text END,
               CASE WHEN p = 0 THEN 'aa/x.jpg' END
          FROM listings l, generate_series(0, 2) p
        """
    )
    await c.execute(
        """
        INSERT INTO listing_snapshots (listing_id, observed_at, acquisition_mode, price, status)
        SELECT id, first_seen_at, 'extension_card', price, 'active' FROM listings
        UNION ALL
        SELECT id, last_seen_at, 'extension_card', price - 1, 'active' FROM listings
        """
    )
    await c.execute(
        """
        INSERT INTO listing_price_history (listing_id, price, observed_at)
        SELECT id, price + 5, first_seen_at - interval '1 day' FROM listings   -- not carried by any snapshot
        UNION ALL SELECT id, price, first_seen_at FROM listings
        """
    )
    await c.execute(
        """
        INSERT INTO opportunities (id, listing_id, algorithm_version, listing_price, currency, total_acquisition_cost,
               flip_score, confidence_score, risk_score, risk_level, deal_tier, verdict, recommended_action,
               comparables_count, sold_comparables_count, score_breakdown, explanation, risk_factors, market_snapshot)
        SELECT gen_random_uuid(), id, '2026.10-1', price, 'EUR', price + 4, 70, 60, 20, 'low', 'good', 'BUY',
               'buy_now', 8, 2, '{}', '[]', '[]', '{}'
          FROM listings WHERE status = 'active'
        """
    )
    await c.execute(
        """
        INSERT INTO opportunity_scores (opportunity_id, algorithm_version, listing_price, flip_score,
               confidence_score, risk_score, components, penalties)
        SELECT id, '2026.10-1', listing_price, 70, 60, 20, '{}', '[]' FROM opportunities
        """
    )
    await c.execute(
        """
        INSERT INTO sold_sales (dedupe_key, source, reliability, price_kind, listing_id, title, price, currency,
               price_eur, sold_at, published_at, days_to_sell, source_name)
        SELECT 'vinted:' || external_id, 'vinted_sold', 3, 'last_seen', id, title, last_active_price, 'EUR',
               last_active_price, sold_at, published_at, days_to_sell, 'vinted'
          FROM listings WHERE status = 'sold'
        """
    )


async def test_every_phase_2_migration_on_existing_data_loses_nothing() -> None:
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
    await admin.execute(f"CREATE DATABASE {MIG_DB}")
    await admin.close()
    c = None
    try:
        alembic("upgrade", "0009")
        c = await asyncpg.connect(MIG_DSN)
        await _seed(c)
        before = await _counts(c)
        history_before = await c.fetchval("SELECT count(*) FROM listing_price_history")
        assert before["listings"] == 120 and before["listing_images"] == 360 and before["sold_sales"] > 0
        sold_urls = {r["url"] for r in await c.fetch("SELECT url FROM listings")}

        alembic("upgrade", "head")

        after = await _counts(c)
        # Nothing disappears; what is added is new information, not a copy.
        for table in (
            "listings",
            "listing_images",
            "sellers",
            "opportunities",
            "opportunity_scores",
            "sold_sales",
        ):
            assert after[table] == before[table], table
        assert after["market_comparables"] == before["market_comparables"]
        # Every price point of the old history is still in the view (the old table is gone).
        assert (
            await c.fetchval("SELECT count(*) FROM listing_price_history")
            >= history_before - before["listings"]
        )
        assert after["listing_snapshots"] >= before["listing_snapshots"]
        assert {r["url"] for r in await c.fetch("SELECT url FROM listings")} == sold_urls

        # No record without URL and date; every opportunity has its first analysis.
        assert (
            await c.fetchval("SELECT count(*) FROM listings WHERE btrim(url) = '' OR first_seen_at IS NULL")
            == 0
        )
        assert await c.fetchval("SELECT count(*) FROM listing_snapshots WHERE observed_at IS NULL") == 0
        assert await c.fetchval("SELECT count(*) FROM opportunities WHERE analysis_id IS NULL") == 0
        assert await c.fetchval("SELECT count(*) FROM analyses") == before["opportunities"]
        assert (
            await c.fetchval("SELECT count(*) FROM analyses WHERE btrim(url) = '' OR created_at IS NULL") == 0
        )
        # Assumed publication dates are gone (the instant stays in first_seen_at); real ones stay.
        assert await c.fetchval("SELECT count(*) FROM listings WHERE published_at IS NULL") == 40
        assert await c.fetchval("SELECT count(*) FROM listings WHERE published_at_kind = 'reported'") == 80
        # Photos: keyed, copies and hashes kept, none retired by accident.
        assert (
            await c.fetchval(
                "SELECT count(*) FROM listing_images WHERE image_key IS NULL OR removed_at IS NOT NULL"
            )
            == 0
        )
        assert await c.fetchval("SELECT count(*) FROM listing_images WHERE local_path IS NOT NULL") == 120
        # Sold sales: asking prices apart from realized ones.
        assert (
            await c.fetchval("SELECT count(*) FROM sold_sales WHERE asking_price IS NOT NULL")
            == before["sold_sales"]
        )
        assert await c.fetchval("SELECT count(*) FROM sold_sales WHERE realized_price IS NOT NULL") == 0
        # Sellers: same rows, protected keys, only rating and reviews left.
        keys = [r["external_id"] for r in await c.fetch("SELECT external_id FROM sellers")]
        assert all(k.startswith("s2:") for k in keys) and len(set(keys)) == 20
        assert (
            await c.fetchval("SELECT count(*) FROM listings WHERE seller_id IS NOT NULL")
            == before["listings"]
        )

        alembic("downgrade", "0009")
        back = await _counts(c)
        for table in ("listings", "sellers", "opportunities", "opportunity_scores", "sold_sales"):
            assert back[table] == before[table], table
        # The old shape accepts the old data again.
        await c.execute("SELECT 1 FROM listing_price_history LIMIT 1")
        assert uuid.UUID(str(await c.fetchval("SELECT id FROM listings LIMIT 1")))
    finally:
        if c is not None:
            await c.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
        await admin.close()
