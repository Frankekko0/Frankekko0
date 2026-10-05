"""Idempotent bootstrap: catalog sync, demo user and demo-market epoch.

Run with ``python -m app.seed`` (the Docker entrypoint does it after migrations).
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime
from decimal import Decimal as D
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.models import Brand, Category, NotificationSettings, SystemState, User, UserPreferences, Watchlist
from app.db.session import dispose_engine, session_scope
from app.identification.taxonomy import BRANDS, CATEGORIES
from app.ingestion.catalog import reset_catalog_cache

log = get_logger(__name__)
MOCK_EPOCH_KEY = "mock_market_epoch"


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


async def get_or_create_mock_epoch(session: AsyncSession) -> datetime:
    state = await session.get(SystemState, MOCK_EPOCH_KEY)
    if state is None:
        epoch = datetime.now(UTC).replace(microsecond=0)
        await session.execute(
            pg_insert(SystemState)
            .values(key=MOCK_EPOCH_KEY, value={"epoch": epoch.isoformat()})
            .on_conflict_do_nothing(index_elements=["key"])
        )
        state = await session.get(SystemState, MOCK_EPOCH_KEY)
    assert state is not None
    return datetime.fromisoformat(state.value["epoch"])


async def ensure_user_defaults(session: AsyncSession, user: User) -> None:
    if await session.get(UserPreferences, user.id) is None:
        session.add(UserPreferences(user_id=user.id))
    if await session.get(NotificationSettings, user.id) is None:
        session.add(NotificationSettings(user_id=user.id, email_address=user.email))
    await session.flush()


async def seed_demo_user(session: AsyncSession) -> None:
    settings = get_settings()
    email = settings.demo_user_email.lower()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is not None:
        return
    configured = settings.demo_user_password
    password = configured.get_secret_value() if configured else secrets.token_urlsafe(32)
    user = User(email=email, password_hash=hash_password(password), display_name="Demo Reseller")
    session.add(user)
    await session.flush()
    await ensure_user_defaults(session, user)
    session.add_all(
        [
            Watchlist(
                user_id=user.id,
                name="Ralph Lauren",
                brand_slugs=["ralph-lauren"],
                max_buy_price=D("25"),
                min_profit=D("15"),
                min_roi=D("0.5"),
            ),
            Watchlist(user_id=user.id, name="Nike", brand_slugs=["nike"], max_buy_price=D("20")),
            Watchlist(
                user_id=user.id,
                name="Football shirts",
                category_slugs=["football-shirts"],
                min_profit=D("12"),
                min_roi=D("0.6"),
            ),
            Watchlist(user_id=user.id, name="Vintage", vintage_only=True, min_flip_score=80),
        ]
    )
    log.info("seed.demo_user_created", email=email)


async def run_seed() -> None:
    settings = get_settings()
    async with session_scope() as session:
        await sync_catalog(session)
        if settings.marketplace_provider == "mock":
            await get_or_create_mock_epoch(session)
        if settings.seed_demo_user:
            await seed_demo_user(session)
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
