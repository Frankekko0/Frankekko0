"""In-process cache of the brand/category catalog (small, read-mostly tables)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brand, Category
from app.identification.taxonomy import DEFAULT_TAXONOMY, BrandSpec, Taxonomy


@dataclass(frozen=True)
class BrandInfo:
    id: int
    slug: str
    name: str
    tier: str
    counterfeit_risk: float


@dataclass(frozen=True)
class CategoryInfo:
    id: int
    slug: str
    name: str
    name_it: str
    parent_slug: str | None
    baseline_days: int


class Catalog:
    def __init__(self, brands: list[BrandInfo], categories: list[CategoryInfo], taxonomy: Taxonomy) -> None:
        self.brands_by_slug = {b.slug: b for b in brands}
        self.brands_by_id = {b.id: b for b in brands}
        self.categories_by_slug = {c.slug: c for c in categories}
        self.categories_by_id = {c.id: c for c in categories}
        self.taxonomy = taxonomy

    def brand_id(self, slug: str | None) -> int | None:
        return self.brands_by_slug[slug].id if slug and slug in self.brands_by_slug else None

    def category_id(self, slug: str | None) -> int | None:
        return self.categories_by_slug[slug].id if slug and slug in self.categories_by_slug else None

    def brand_slug(self, brand_id: int | None) -> str | None:
        return self.brands_by_id[brand_id].slug if brand_id in self.brands_by_id else None

    def category_slug(self, category_id: int | None) -> str | None:
        return self.categories_by_id[category_id].slug if category_id in self.categories_by_id else None

    def parent_slug(self, category_slug: str | None) -> str | None:
        info = self.categories_by_slug.get(category_slug or "")
        return info.parent_slug if info else None

    def sibling_category_ids(self, category_slug: str | None) -> list[int]:
        """Ids of categories sharing the same parent (comparable pre-filter)."""
        parent = self.parent_slug(category_slug)
        if not parent:
            cid = self.category_id(category_slug)
            return [cid] if cid else []
        return [c.id for c in self.categories_by_slug.values() if c.parent_slug == parent]


_cache: tuple[float, Catalog] | None = None
TTL_SECONDS = 300


async def load_catalog(session: AsyncSession, force: bool = False) -> Catalog:
    global _cache
    if _cache and not force and time.monotonic() - _cache[0] < TTL_SECONDS:
        return _cache[1]
    brand_rows = (await session.execute(select(Brand))).scalars().all()
    cat_rows = (await session.execute(select(Category))).scalars().all()
    by_id = {c.id: c for c in cat_rows}
    brands = [BrandInfo(b.id, b.slug, b.name, b.tier, float(b.counterfeit_risk)) for b in brand_rows]
    categories = [
        CategoryInfo(
            c.id,
            c.slug,
            c.name,
            c.name_it,
            by_id[c.parent_id].slug if c.parent_id in by_id else None,
            c.baseline_days_to_sell,
        )
        for c in cat_rows
    ]
    known = {b.slug for b in DEFAULT_TAXONOMY.brands}
    extra = [
        BrandSpec(b.name, b.slug, tuple(b.aliases or [b.name.lower()]), b.tier, Decimal(b.counterfeit_risk))
        for b in brand_rows
        if b.slug not in known
    ]
    catalog = Catalog(
        brands, categories, DEFAULT_TAXONOMY.with_extra_brands(extra) if extra else DEFAULT_TAXONOMY
    )
    _cache = (time.monotonic(), catalog)
    return catalog


def reset_catalog_cache() -> None:
    global _cache
    _cache = None
