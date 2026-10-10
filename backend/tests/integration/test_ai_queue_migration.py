"""0023 (the model-analysis queue on the opportunity) on a database that already holds data: rows the model had
reviewed stay reviewed (they must not be queued again), the others wait, and going back works."""

import asyncpg

from tests.integration.test_migration import ADMIN_DSN, MIG_DB, MIG_DSN, alembic


async def test_rows_already_reviewed_by_the_model_are_not_queued_again_after_the_upgrade() -> None:
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
    await admin.execute(f"CREATE DATABASE {MIG_DB}")
    await admin.close()
    c = None
    try:
        alembic("upgrade", "0022")
        c = await asyncpg.connect(MIG_DSN)
        await c.execute(
            """
            INSERT INTO listings (id, provider, external_id, url, title, price, status, capture_level,
                                  first_seen_at, last_seen_at, photo_count)
            SELECT gen_random_uuid(), 'vinted', (200000 + i)::text, 'https://www.vinted.it/items/' || (200000 + i),
                   'Felpa ' || i, 20, 'active', 'full', now(), now(), 3
              FROM generate_series(1, 4) i
            """
        )
        await c.execute(
            """
            INSERT INTO analyses (id, listing_id, provider, url, schema_version, algorithm_version, trigger,
                                  input_hash, result_hash, inputs, product, visual, economic, market, decision)
            SELECT gen_random_uuid(), id, 'vinted', url, 1, '2026.10-1', 'new', 'a', 'b',
                   '{}', '{}', '{}', '{}', '{}', '{}'
              FROM listings
            """
        )
        await c.execute(
            """
            INSERT INTO opportunities (id, listing_id, analysis_id, algorithm_version, listing_price, currency,
                   total_acquisition_cost, flip_score, confidence_score, risk_score, risk_level, deal_tier,
                   verdict, recommended_action, comparables_count, sold_comparables_count, score_breakdown,
                   explanation, risk_factors, market_snapshot, ai_provider)
            SELECT gen_random_uuid(), l.id, a.id, '2026.10-1', 20, 'EUR', 24, 85, 60, 20, 'low', 'good', 'BUY',
                   'buy_now', 8, 2, '{}', '[]', '[]', '{}',
                   (ARRAY['claude', 'rules', 'gemini', NULL])[1 + (l.external_id::int % 4)]
              FROM listings l JOIN analyses a ON a.listing_id = l.id
            """
        )
        alembic("upgrade", "0023")
        rows = await c.fetch(
            "SELECT ai_provider, ai_for_analysis_id = analysis_id AS reviewed, ai_for_analysis_id IS NULL AS empty,"
            " ai_attempts, ai_next_attempt_at, ai_last_error FROM opportunities"
        )
        by_provider = {r["ai_provider"]: r for r in rows}
        assert by_provider["claude"]["reviewed"] and by_provider["gemini"]["reviewed"]
        assert by_provider["rules"]["empty"] and by_provider[None]["empty"]  # these wait for their review
        assert all(
            (r["ai_attempts"], r["ai_next_attempt_at"], r["ai_last_error"]) == (0, None, None) for r in rows
        )
        waiting = await c.fetchval(
            "SELECT count(*) FROM opportunities WHERE is_active AND ai_for_analysis_id IS DISTINCT FROM analysis_id"
        )
        assert waiting == 2

        alembic("downgrade", "0022")  # reversible
        assert await c.fetchval("SELECT count(*) FROM opportunities") == 4
        assert not await c.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'opportunities'"
            " AND column_name LIKE 'ai\\_%' ESCAPE '\\' AND column_name NOT IN ('ai_analysis', 'ai_provider', 'ai_analyzed_at')"
        )
    finally:
        if c is not None:
            await c.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
        await admin.close()
