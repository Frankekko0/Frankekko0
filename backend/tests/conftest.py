"""Test configuration.

Tests run against a real PostgreSQL database (``flipfinder_test`` by default, override with
``TEST_DATABASE_URL``) and Redis database 15. The schema is created by running the Alembic
migrations, so migrations are tested too.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://flipfinder:flipfinder@localhost:5432/flipfinder_test"
)
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": TEST_DB,
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15"),
        "LOG_JSON": "false",
        "LOG_LEVEL": "WARNING",
        "SEED_DEMO_USER": "false",
        "AI_API_KEY": "",
        "JWT_SECRET": "test-secret-test-secret-test-secret-123",
    }
)

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.marketplace.base import ProviderImage, ProviderListing, ProviderSeller  # noqa: E402


def _alembic(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        check=True,
        env=os.environ.copy(),
        capture_output=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")


@pytest.fixture(scope="session", autouse=True)
async def _seeded(_migrated_database: None) -> AsyncIterator[None]:
    from app.core.redis import get_redis
    from app.db.session import session_scope
    from app.seed import sync_catalog

    await get_redis().flushdb()
    async with session_scope() as s:
        await sync_catalog(s)
    yield
    from app.core.redis import close_redis
    from app.db.session import dispose_engine

    await close_redis()
    await dispose_engine()


DATA_TABLES = (
    "alert_deliveries, alerts, favorites, inventory, sales, purchases, user_affinities, market_comparables, "
    "opportunity_scores, opportunities, analysis_jobs, listing_snapshots, "
    "acquisition_attempts, listing_images, listings, products, "
    "sellers, market_statistics, watchlists, push_subscriptions, notification_settings, user_preferences, users, "
    "system_state, sold_sales, external_prices, external_searches, model_price_stats, ai_usage, agent_runs, events, vision_cache"
)


@pytest.fixture
async def clean_db() -> AsyncIterator[None]:
    from app.alerts.service import reset_audience_cache
    from app.core.redis import get_redis
    from app.db.session import session_scope

    async with session_scope() as s:
        await s.execute(text(f"TRUNCATE {DATA_TABLES} RESTART IDENTITY CASCADE"))
    await get_redis().flushdb()
    reset_audience_cache()
    yield


@pytest.fixture
async def session(clean_db: None) -> AsyncIterator[Any]:
    from app.db.session import get_sessionmaker

    s = get_sessionmaker()()
    try:
        yield s
    finally:
        await s.rollback()
        await s.close()


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def make_listing() -> Callable[..., ProviderListing]:
    counter = {"n": 0}

    def _make(
        title: str = "Polo Ralph Lauren Custom Slim Fit blu navy M",
        price: float | str = 28,
        *,
        brand: str | None = "Ralph Lauren",
        category: str | None = "Polo",
        size: str | None = "M / 38 / 10",
        condition: str | None = "Ottime condizioni",
        status: str = "active",
        published_days_ago: float = 3,
        sold_after_days: float | None = None,
        seller_id: str | None = None,
        seller_reviews: int = 120,
        seller_rating: str = "4.9",
        description: str = "Polo originale in ottime condizioni, indossata poche volte. Spedisco subito.",
        photos: int = 4,
        phash_seed: int | None = None,
        external_id: str | None = None,
        country: str = "IT",
        now: datetime = NOW,
    ) -> ProviderListing:
        counter["n"] += 1
        n = counter["n"]
        published = now - timedelta(days=published_days_ago)
        sold_at = (
            published + timedelta(days=sold_after_days)
            if status == "sold" and sold_after_days is not None
            else None
        )
        seed = phash_seed if phash_seed is not None else n * 7919
        return ProviderListing(
            external_id=external_id or str(1_000_000 + n),
            url=f"https://www.vinted.it/items/{external_id or 1_000_000 + n}-test",
            title=title,
            description=description,
            price=Decimal(str(price)),
            brand=brand,
            category=category,
            size=size,
            condition=condition,
            images=[
                ProviderImage(url=f"/img/{n}/{i}.jpg", phash=f"{(seed * 31 + i) & 0xFFFFFFFFFFFFFFFF:016x}")
                for i in range(photos)
            ],
            seller=ProviderSeller(
                external_id=seller_id or f"s{n % 40}",
                rating=Decimal(seller_rating) if seller_reviews else None,
                review_count=seller_reviews,
                account_created_at=now - timedelta(days=700),
                item_count=30,
                sold_count=seller_reviews,
                country=country,
            ),
            country=country,
            published_at=published,
            status=status,  # type: ignore[arg-type]
            sold_at=sold_at,
            shipping_fee=Decimal("3.49"),
            favourite_count=4,
        )

    return _make


@pytest.fixture
async def client(clean_db: None) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def register(
    client: httpx.AsyncClient, email: str = "user@example.com", password: str = "s3cure-pass!"
) -> dict[str, Any]:
    r = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password, "display_name": "Tester"}
    )
    assert r.status_code == 201, r.text
    data = r.json()
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    return data


@pytest.fixture
async def auth_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    await register(client)
    return client
