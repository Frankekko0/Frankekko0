"""Never analyse unchanged photos twice.

The result of a photo analysis is kept under a key made of the photos' stable identities (see
``app.media.keys.image_key``), the model, the version of the prompt and schema, and the brand
rules and categories the model was told about. Same photos, same model, same prompt: the stored
answer is returned and no paid call is made. Change one photo (or the prompt, or the model) and the
key changes: the set is analysed again.

The granularity is the photo *set*, not the single photo: the model reasons across the photos
(label on one, logo on another), so a lone photo is not analysed apart. Titles and declared brands
are not part of the key because they are not given to the model: the photos are read on their own.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import VisionCache
from app.db.session import session_scope
from app.media.keys import image_key
from app.vision.analyzer import ImageAnalyzer
from app.vision.types import ImageAnalysis

log = get_logger(__name__)
# Bump when the prompt or the schema of the photo analysis changes: every stored answer is then stale.
VISION_PROMPT_VERSION = "vision-2026.10-2"
RETENTION_DAYS = 90
Scope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def cache_key(urls: list[str], model: str, context: dict[str, Any]) -> str:
    photos = [image_key(u) for u in urls]
    told = {
        "rules": context.get("brand_rules") or {},
        "categories": sorted(context.get("category_slugs") or []),
    }
    raw = json.dumps(
        {"photos": photos, "model": model, "prompt": VISION_PROMPT_VERSION, "told": told},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class VisionCacheStore:
    def __init__(self, scope: Scope = session_scope) -> None:
        self._scope = scope

    async def get(self, key: str) -> dict[str, Any] | None:
        async with self._scope() as s:
            row = (await s.execute(select(VisionCache).where(VisionCache.key == key))).scalar_one_or_none()
            if row is None:
                return None
            await s.execute(
                update(VisionCache)
                .where(VisionCache.key == key)
                .values(hits=VisionCache.hits + 1, last_hit_at=datetime.now(UTC))
            )
            return dict(row.result)

    async def put(self, key: str, model: str, n_photos: int, result: dict[str, Any]) -> None:
        stmt = pg_insert(VisionCache).values(
            key=key, model=model[:64], prompt_version=VISION_PROMPT_VERSION, n_photos=n_photos, result=result
        )
        async with self._scope() as s:
            await s.execute(stmt.on_conflict_do_nothing(index_elements=["key"]))

    async def stats(self) -> dict[str, int]:
        async with self._scope() as s:
            entries, hits = (
                await s.execute(
                    select(func.count(), func.coalesce(func.sum(VisionCache.hits), 0)).select_from(
                        VisionCache
                    )
                )
            ).one()
        return {"entries": int(entries), "hits": int(hits)}

    async def prune(self, days: int = RETENTION_DAYS) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with self._scope() as s:
            res = await s.execute(
                delete(VisionCache).where(
                    func.coalesce(VisionCache.last_hit_at, VisionCache.created_at) < cutoff
                )
            )
            return int(res.rowcount or 0)  # type: ignore[attr-defined]


class CachedImageAnalyzer(ImageAnalyzer):
    """Wraps an analyzer: a stored answer for the same photos is returned without calling it."""

    def __init__(self, inner: ImageAnalyzer, model: str, store: VisionCacheStore | None = None) -> None:
        self.inner = inner
        self.model = model
        self.store = store or VisionCacheStore()
        self.name = inner.name

    async def analyze(
        self, image_urls: list[str], provided_hashes: list[str | None], context: dict[str, Any]
    ) -> ImageAnalysis:
        urls = [u for u in image_urls if u]
        if not urls:
            return await self.inner.analyze(image_urls, provided_hashes, context)
        key = cache_key(urls, self.model, context)
        try:
            hit = await self.store.get(key)
        except Exception as exc:  # a cache that cannot be read costs a call, not a failure
            log.warning("vision.cache_unreadable", error=type(exc).__name__)
            hit = None
        if hit is not None:
            log.info("vision.cache_hit", photos=len(urls))
            return ImageAnalysis.model_validate(hit)
        result = await self.inner.analyze(image_urls, provided_hashes, context)
        try:
            await self.store.put(key, self.model, len(urls), result.model_dump(mode="json"))
        except Exception as exc:
            log.warning("vision.cache_not_stored", error=type(exc).__name__)
        return result
