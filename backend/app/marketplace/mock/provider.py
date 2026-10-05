"""Mock Marketplace Data Provider (demo mode).

Serves the deterministic simulated market from :mod:`app.marketplace.mock.generator` through
the standard :class:`MarketplaceProvider` interface, so the whole application (scanner,
pipeline, alerts, dashboard) runs end-to-end without any external integration. Swapping in a
real, authorized adapter requires no change outside ``app/marketplace``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.identification.taxonomy import fold
from app.marketplace.base import (
    ComparableQuery,
    MarketplaceProvider,
    PricePoint,
    ProviderCapabilities,
    ProviderListing,
    ProviderSeller,
    SearchPage,
    SearchQuery,
)
from app.marketplace.mock.generator import EXTERNAL_ID_BASE, MarketSimulator


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MockMarketplaceProvider(MarketplaceProvider):
    name = "mock"
    capabilities = ProviderCapabilities(
        search=True, sold_data=True, price_history=True, seller_details=True, comparables=True, realtime=True
    )

    def __init__(
        self,
        seed: int,
        epoch: datetime,
        history_days: int = 60,
        history_minutes_per_listing: float = 8.0,
        live_seconds_per_listing: float = 20.0,
        now_fn: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.sim = MarketSimulator(
            seed=seed,
            epoch=epoch,
            history_days=history_days,
            history_minutes_per_listing=history_minutes_per_listing,
            live_seconds_per_listing=live_seconds_per_listing,
        )
        self.now = now_fn

    def _k(self, external_id: str) -> int | None:
        try:
            k = int(external_id) - EXTERNAL_ID_BASE
        except ValueError:
            return None
        return k if k >= 0 else None

    async def search_listings(self, query: SearchQuery) -> SearchPage:
        now = self.now()
        until = min(query.until or now, now)
        since = query.since or self.sim.history_start
        start_k = int(query.cursor) + 1 if query.cursor else None
        brands = {fold(b) for b in query.brands}
        categories = set(query.categories)

        out: list[ProviderListing] = []
        last_k: int | None = None
        indices = self.sim.indices_between(since, until)
        for k in indices:
            if start_k is not None and k < start_k:
                continue
            sim = self.sim.listing(k)
            if sim.published_at <= since or sim.published_at > until:
                continue
            last_k = k
            if brands and fold(sim.brand_field or "") not in brands:
                continue
            if categories and sim.category_slug not in categories:
                continue
            if query.max_price is not None and sim.price_at(now) > query.max_price:
                continue
            listing = sim.to_provider(now)
            if listing is not None:
                out.append(listing)
            if len(out) >= query.page_size:
                break
        has_more = bool(last_k is not None and last_k < indices.stop - 1 and len(out) >= query.page_size)
        return SearchPage(listings=out, next_cursor=str(last_k) if has_more else None, has_more=has_more)

    async def get_listing(self, external_id: str) -> ProviderListing | None:
        k = self._k(external_id)
        if k is None:
            return None
        return self.sim.listing(k).to_provider(self.now())

    async def get_seller(self, external_id: str) -> ProviderSeller | None:
        try:
            index = int(external_id) - 9_000_000
        except ValueError:
            return None
        if index < 0:
            return None
        return self.sim.seller(index).to_provider()

    async def get_comparable_listings(self, query: ComparableQuery) -> list[ProviderListing]:
        now = self.now()
        since = query.since or (now - timedelta(days=90))
        brand = fold(query.brand)
        out: list[ProviderListing] = []
        for k in reversed(self.sim.indices_between(since, now)):
            sim = self.sim.listing(k)
            if sim.published_at > now or sim.published_at < since:
                continue
            if fold(sim.archetype.brand) != brand and fold(sim.brand_field or "") != brand:
                continue
            if query.category and sim.category_slug != query.category:
                continue
            if query.model_name and (sim.archetype.line or "") != query.model_name:
                continue
            listing = sim.to_provider(now)
            if listing is None or (not query.include_sold and listing.status != "active"):
                continue
            out.append(listing)
            if len(out) >= query.limit:
                break
        return out

    async def get_listing_history(self, external_id: str) -> list[PricePoint]:
        k = self._k(external_id)
        if k is None:
            return []
        sim = self.sim.listing(k)
        return [PricePoint(price=p, observed_at=t) for t, p in sim.price_history(self.now())]
