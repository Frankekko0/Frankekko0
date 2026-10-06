"""Idempotent bootstrap: catalog sync (brands and categories).

Run with ``python -m app.seed`` (the Docker entrypoint does it after migrations).
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.models import Brand, Category, NotificationSettings, User, UserPreferences
from app.db.session import dispose_engine, session_scope
from app.identification.taxonomy import BRANDS, CATEGORIES
from app.ingestion.catalog import reset_catalog_cache

log = get_logger(__name__)


async def sync_catalog(session: AsyncSession) -> None:
    brand_rows = [
        {
            "name": b.name,
            "slug": b.slug,
            "aliases": list(b.aliases),
            "tier": b.tier,
            "counterfeit_risk": b.counterfeit_risk,
        }
        for b in BRANDS
    ]
    stmt = pg_insert(Brand).values(brand_rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["slug"],
            set_={
                "name": stmt.excluded.name,
                "aliases": stmt.excluded.aliases,
                "tier": stmt.excluded.tier,
                "counterfeit_risk": stmt.excluded.counterfeit_risk,
            },
        )
    )
    # Parents first so children can reference them.
    for level in (True, False):
        specs = [c for c in CATEGORIES if (c.parent is None) == level]
        existing = {c.slug: c.id for c in (await session.execute(select(Category))).scalars().all()}
        rows: list[dict[str, Any]] = [
            {
                "slug": c.slug,
                "name": c.name,
                "name_it": c.name_it,
                "parent_id": existing.get(c.parent) if c.parent else None,
                "baseline_days_to_sell": c.baseline_days_to_sell,
            }
            for c in specs
        ]
        stmt2 = pg_insert(Category).values(rows)
        await session.execute(
            stmt2.on_conflict_do_update(
                index_elements=["slug"],
                set_={
                    "name": stmt2.excluded.name,
                    "name_it": stmt2.excluded.name_it,
                    "parent_id": stmt2.excluded.parent_id,
                    "baseline_days_to_sell": stmt2.excluded.baseline_days_to_sell,
                },
            )
        )
    reset_catalog_cache()


async def ensure_user_defaults(session: AsyncSession, user: User) -> None:
    if await session.get(UserPreferences, user.id) is None:
        session.add(UserPreferences(user_id=user.id))
    if await session.get(NotificationSettings, user.id) is None:
        session.add(NotificationSettings(user_id=user.id, email_address=user.email))
    await session.flush()


async def run_seed() -> None:
    async with session_scope() as session:
        await sync_catalog(session)
    log.info("seed.completed")


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    try:
        await run_seed()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
