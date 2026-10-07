"""Analysis of the listings captured by the browser extension, tuned for latency.

A search page sends its cards in several requests a few hundred milliseconds apart, and the same
brands come back request after request (and page after page). Two things made every request pay
again for work the previous one had just done:

* the comparables pool of each brand/category family (three bounded queries plus a few thousand
  rows turned into profiles: well over half of the SQL of a capture) was reloaded per request.
  ``CapturePipeline`` keeps the pools for ``POOL_TTL_SECONDS`` in the API process. A pool is the
  recent market of a segment: a minute-old copy only misses the asks captured in that minute,
  and any capture that brings a sold or removed listing drops the copies of every API process
  (a generation shared in Redis, ``sync_pools``);
* cards seen again (reload, back navigation, overlapping infinite scroll) were analysed again
  although nothing had changed. ``analysis_needed`` keeps only new listings, listings whose
  analysis inputs (price, status, identification, text, photos count...) changed with this
  capture (``inputs_before`` reads them before it is stored), and listings without a recent
  analysis of the current algorithm version (the worker re-analyses every active opportunity
  daily anyway).
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.identity import listing_identity
from app.core.config import get_settings
from app.core.redis import get_redis
from app.db.models import Listing, Opportunity
from app.ingestion.catalog import Catalog
from app.ingestion.service import IngestResult
from app.marketplace.base import ProviderListing
from app.opportunities.pipeline import AnalysisPipeline, CandidatePool

POOL_TTL_SECONDS = 60.0
POOL_CACHE_MAX = 256
# A card seen again within this time, unchanged, keeps its analysis.
REUSE_ANALYSIS_FOR = timedelta(hours=2)

GENERATION_KEY = "ff:capture:pools-gen"

_pools: OrderedDict[tuple[int, tuple[int, ...]], tuple[float, CandidatePool]] = OrderedDict()
_generation: str | None = None


def forget_pools() -> None:
    global _generation
    _pools.clear()
    _generation = None


async def sync_pools(drop: bool = False) -> bool:
    """Align this process's pools with the generation shared in Redis; ``drop`` starts a new one
    (every API process then drops its copies). Returns whether the pools may be used: without
    Redis they are not (a database reset, as in the tests, would go unnoticed)."""
    global _generation
    redis = get_redis()
    try:
        if drop:
            gen: str | None = uuid.uuid4().hex
            await redis.set(GENERATION_KEY, gen)
        else:
            gen = await redis.get(GENERATION_KEY)
            if gen is None:
                await redis.set(GENERATION_KEY, uuid.uuid4().hex, nx=True)
                gen = await redis.get(GENERATION_KEY)
    except RedisError:
        forget_pools()
        return False
    if gen != _generation:
        _pools.clear()
        _generation = gen
    return gen is not None


class CapturePipeline(AnalysisPipeline):
    """``AnalysisPipeline`` with the comparables pools shared across requests for a minute
    (``share_pools=False``: a plain pipeline)."""

    def __init__(self, session: AsyncSession, share_pools: bool = True) -> None:
        super().__init__(session)
        self.share_pools = share_pools

    async def candidate_pool(
        self, brand_id: int, category_ids: list[int] | None, catalog: Catalog, now: datetime
    ) -> CandidatePool:
        if not self.share_pools:
            return await super().candidate_pool(brand_id, category_ids, catalog, now)
        key = (brand_id, tuple(sorted(category_ids or ())))
        mono = time.monotonic()
        hit = _pools.get(key)
        if hit is not None and mono - hit[0] < POOL_TTL_SECONDS:
            _pools.move_to_end(key)
            return hit[1]
        pool = await super().candidate_pool(brand_id, category_ids, catalog, now)
        _pools[key] = (mono, pool)
        _pools.move_to_end(key)
        while len(_pools) > POOL_CACHE_MAX:
            _pools.popitem(last=False)
        return pool


MARKET_CHANGES = ("sold", "removed")


def changes_market(result: IngestResult, statuses: list[str]) -> bool:
    """A capture that brings sold/removed listings (or turns one sold/removed) changes the pools."""
    return any(s in MARKET_CHANGES for s in statuses) or any(
        new in MARKET_CHANGES for _, _, new in result.status_changes
    )


# What an analysis reads from the listing itself: unchanged values -> same analysis inputs.
_INPUTS = (
    Listing.price,
    Listing.currency,
    Listing.status,
    Listing.title,
    func.md5(Listing.description),
    Listing.brand_id,
    Listing.category_id,
    Listing.model_name,
    Listing.size_normalized,
    Listing.condition,
    Listing.gender,
    Listing.color,
    Listing.material,
    Listing.is_vintage,
    Listing.capture_level,
    Listing.photo_count,
    Listing.seller_id,
    Listing.shipping_fee,
    Listing.buyer_protection_fee,
    Listing.duplicate_of_id,
)
_N_INPUTS = len(_INPUTS)


async def inputs_before(
    session: AsyncSession, listings: list[ProviderListing]
) -> dict[tuple[str, str], tuple]:
    """Analysis inputs of the already known listings of a capture, read before it is stored."""
    by_provider: dict[str, list[str]] = {}
    for pl in listings:
        by_provider.setdefault(listing_identity(pl.url)[0], []).append(pl.external_id)
    out: dict[tuple[str, str], tuple] = {}
    for provider, external_ids in by_provider.items():
        rows = await session.execute(
            select(Listing.provider, Listing.external_id, *_INPUTS).where(
                Listing.provider == provider, Listing.external_id.in_(external_ids)
            )
        )
        out |= {(r[0], r[1]): tuple(r[2:]) for r in rows}
    return out


async def analysis_needed(
    session: AsyncSession,
    result: IngestResult,
    before: dict[tuple[str, str], tuple],
    reuse_for: timedelta = REUSE_ANALYSIS_FOR,
) -> list[uuid.UUID]:
    """Listings of a capture that need a new analysis (input order kept): new ones, ones whose
    analysis inputs changed with this capture, and ones without a recent, active analysis of
    the current algorithm version."""
    ids = list(dict.fromkeys([*result.new_ids, *result.updated_ids]))
    new = set(result.new_ids)
    seen_again = [i for i in ids if i not in new]
    if not seen_again or not before:
        return ids
    since = datetime.now(UTC) - reuse_for
    version = get_settings().algorithm_version
    rows = await session.execute(
        select(
            Listing.id,
            Listing.provider,
            Listing.external_id,
            *_INPUTS,
            Opportunity.is_active,
            Opportunity.algorithm_version,
            Opportunity.analyzed_at,
        )
        .outerjoin(Opportunity, Opportunity.listing_id == Listing.id)
        .where(Listing.id.in_(seen_again))
    )
    keep: set[Any] = set()
    for r in rows:
        inputs, (active, algo, analyzed_at) = tuple(r[3 : 3 + _N_INPUTS]), r[3 + _N_INPUTS :]
        if (
            before.get((r.provider, r.external_id)) == inputs
            and active
            and algo == version
            and analyzed_at is not None
            and analyzed_at >= since
        ):
            keep.add(r.id)
    return [i for i in ids if i not in keep]
