"""Builds the configured marketplace provider (an authorized feed), or none.

Without a configured source there is no scanner: the data come only from what you capture
(browser extension, links, email). Nothing is ever generated to fill the gap."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.marketplace.base import MarketplaceProvider

_provider: MarketplaceProvider | None = None


async def get_provider(session: AsyncSession, settings: Settings | None = None) -> MarketplaceProvider | None:
    """Return the process-wide provider instance (created lazily), None when not configured."""
    global _provider
    if _provider is not None:
        return _provider
    settings = settings or get_settings()
    if settings.marketplace_provider == "feed":
        from app.marketplace.feed import JsonFeedProvider

        if not settings.feed_url:
            raise RuntimeError("MARKETPLACE_PROVIDER=feed requires FEED_URL")
        _provider = JsonFeedProvider(
            settings.feed_url,
            api_key=settings.feed_api_key.get_secret_value() if settings.feed_api_key else None,
            requests_per_minute=settings.feed_requests_per_minute,
        )
    return _provider


def set_provider(provider: MarketplaceProvider | None) -> None:
    """Override the provider (tests, scripts)."""
    global _provider
    _provider = provider


async def close_provider() -> None:
    global _provider
    if _provider is not None:
        await _provider.close()
    _provider = None
