"""0024 repairs a database that reached 0012 through another line of development: it adds what this line's
0010-0012 would have created (here the photo keys) without touching data, and changes nothing where nothing is missing."""

import asyncpg

from tests.integration.test_migration import ADMIN_DSN, MIG_DB, MIG_DSN, alembic

DROP_0012 = """
DROP INDEX IF EXISTS uq_listing_images_current_key;
DROP INDEX IF EXISTS ix_listing_images_listing_position;
ALTER TABLE listing_images DROP COLUMN image_key, DROP COLUMN first_seen_at, DROP COLUMN last_seen_at,
      DROP COLUMN removed_at, DROP COLUMN source;
ALTER TABLE listings DROP COLUMN duplicate_evidence;
ALTER TABLE listing_images ADD CONSTRAINT uq_listing_images_listing_id_position UNIQUE (listing_id, position);
"""


async def test_the_missing_photo_columns_are_added_and_the_data_is_kept() -> None:
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
    await admin.execute(f"CREATE DATABASE {MIG_DB}")
    await admin.close()
    c = None
    try:
        alembic("upgrade", "0023")
        c = await asyncpg.connect(MIG_DSN)
        await c.execute(DROP_0012)  # the state of a database that skipped this line's 0012
        await c.execute(
            """
            INSERT INTO listings (id, provider, external_id, url, title, price, status, capture_level,
                                  first_seen_at, last_seen_at, photo_count)
            VALUES ('00000000-0000-0000-0000-000000000001', 'vinted', '300001', 'https://www.vinted.it/items/300001',
                    'Felpa', 20, 'active', 'full', now(), now(), 3)
            """
        )
        await c.execute(
            """
            INSERT INTO listing_images (listing_id, position, url) VALUES
              ('00000000-0000-0000-0000-000000000001', 0, 'https://images1.vinted.net/t/a/f800.jpeg?s=1'),
              ('00000000-0000-0000-0000-000000000001', 1, 'https://images1.vinted.net/t/b/f800.jpeg'),
              ('00000000-0000-0000-0000-000000000001', 2, 'https://images1.vinted.net/t/a/f800.jpeg?s=2')
            """
        )
        await c.close()
        c = None
        alembic("downgrade", "0023")  # a repair is not undone: going back changes nothing
        alembic("upgrade", "head")
        c = await asyncpg.connect(MIG_DSN)
        rows = await c.fetch("SELECT position, image_key, removed_at FROM listing_images ORDER BY position")
        assert len(rows) == 3 and all(r["image_key"] and len(r["image_key"]) == 16 for r in rows)
        assert rows[0]["image_key"] == rows[2]["image_key"]  # the same file listed twice (same path)
        assert (
            rows[0]["removed_at"] is None and rows[2]["removed_at"] is not None
        )  # the second one is retired
        has = await c.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'listings' AND column_name = 'duplicate_evidence'"
        )
        assert has == 1
        assert await c.fetchval("SELECT count(*) FROM listings") == 1  # nothing was lost
        await c.close()
        c = None
        alembic("downgrade", "0023")
        alembic("upgrade", "head")  # on a complete database it changes nothing
    finally:
        if c is not None:
            await c.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
        await admin.close()
