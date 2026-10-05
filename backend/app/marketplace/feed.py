"""Authorized JSON feed provider.

Consumes listings from an HTTP endpoint you are authorized to use (a partner/affiliate data
feed, an official integration, or your own export) that returns the FlipFinder listing schema.
It is a polite client: it identifies itself, throttles requests to the configured rate, honours
``Retry-After`` on 429/503 and never attempts to bypass access controls.

Expected contract::

    GET {base}?since=<iso8601>&cursor=<opaque>&page_size=<n>
        -> {"listings": [ProviderListing...], "next_cursor": "...", "has_more": true}
    GET {base}/{external_id}           -> ProviderListing | 404
    GET {base}/sellers/{external_id}   -> ProviderSeller | 404   (optional)
    GET {base}/{external_id}/history   -> [PricePoint]           (optional)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.marketplace.base import (
    MarketplaceProvider,
    PricePoint,
    ProviderCapabilities,
    ProviderListing,
    ProviderSeller,
    SearchPage,
    SearchQuery,
)

log = get_logger(__name__)
USER_AGENT = "FlipFinder/0.1 (+authorized-feed-client)"


class JsonFeedProvider(MarketplaceProvider):
    name = "feed"
    capabilities = ProviderCapabilities(search=True, sold_data=True, price_history=True, seller_details=True)

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        requests_per_minute: int = 30,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )
        self._min_interval = 60.0 / max(1, requests_per_minute)
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any | None:
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
        try:
            resp = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            log.warning("feed.request_failed", path=path, error=type(exc).__name__)
            raise ProviderUnavailableError() from exc
        if resp.status_code == 404:
            return None
        if resp.status_code in (429, 503):
            retry_after = resp.headers.get("Retry-After", "60")
            log.warning("feed.throttled", status=resp.status_code, retry_after=retry_after)
            raise ProviderUnavailableError(details={"retry_after": retry_after})
        if resp.status_code >= 400:
            log.warning("feed.http_error", status=resp.status_code, path=path)
            raise ProviderUnavailableError()
        return resp.json()

    async def search_listings(self, query: SearchQuery) -> SearchPage:
        params: dict[str, Any] = {"page_size": query.page_size}
        if query.since:
            params["since"] = query.since.isoformat()
        if query.cursor:
            params["cursor"] = query.cursor
        if query.brands:
            params["brands"] = ",".join(query.brands)
        if query.categories:
            params["categories"] = ",".join(query.categories)
        data = await self._get("", params) or {}
        listings: list[ProviderListing] = []
        for item in data.get("listings", []):
            try:
                listings.append(ProviderListing.model_validate(item))
            except ValueError as exc:
                log.warning("feed.invalid_listing", error=str(exc)[:200])
        return SearchPage(
            listings=listings, next_cursor=data.get("next_cursor"), has_more=bool(data.get("has_more"))
        )

    async def get_listing(self, external_id: str) -> ProviderListing | None:
        data = await self._get(f"/{external_id}")
        return ProviderListing.model_validate(data) if data else None

    async def get_seller(self, external_id: str) -> ProviderSeller | None:
        data = await self._get(f"/sellers/{external_id}")
        return ProviderSeller.model_validate(data) if data else None

    async def get_listing_history(self, external_id: str) -> list[PricePoint]:
        data = await self._get(f"/{external_id}/history")
        return [PricePoint.model_validate(p) for p in data or []]

    async def close(self) -> None:
        await self._client.aclose()
