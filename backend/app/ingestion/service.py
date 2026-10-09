"""Listing ingestion: normalize, identify, de-duplicate and persist provider listings in batches.

Duplicate detection layers (cheapest first):

1. ``(provider, external_id)`` unique key - the same listing seen again is an *update*
   (price history, status, last seen), never a new row;
2. normalized URL;
3. same seller + same title fingerprint or fuzzy title match (>= 92) at a similar price -> repost;
4. perceptual hash of photos: same seller -> repost; *different* seller -> photos reused
   (a classic counterfeit/scam signal, surfaced by the risk engine).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Listing, ListingImage, ListingPriceHistory, ListingSnapshot, Product, Seller
from app.domain.enums import CAPTURE_RANK, AcquisitionMode, CaptureLevel, Condition, ListingStatus
from app.identification.engine import IdentificationEngine, IdentificationResult, ListingText
from app.identification.taxonomy import Taxonomy
from app.ingestion.catalog import Catalog, load_catalog
from app.ingestion.normalizer import (
    condition_from_text,
    normalize_condition,
    normalize_country,
    normalize_size,
    title_fingerprint,
)
from app.marketplace.base import ProviderListing, ProviderSeller
from app.scoring.seller import SellerProfile, seller_reliability
from app.tracking.policy import TRACKING_MODES, evidence_for, schedule
from app.tracking.status import Observation, StatusState, StatusUpdate, apply_observation

log = get_logger(__name__)

_engines: dict[int, IdentificationEngine] = {}
REPOST_WINDOW_DAYS = 120
PHASH_MAX_DISTANCE = 4


def get_engine(taxonomy: Taxonomy) -> IdentificationEngine:
    key = id(taxonomy)
    if key not in _engines:
        _engines.clear()
        _engines[key] = IdentificationEngine(taxonomy)
    return _engines[key]


@dataclass
class PriceChange:
    listing_id: uuid.UUID
    old_price: Decimal
    new_price: Decimal


@dataclass
class IngestResult:
    new_ids: list[uuid.UUID] = field(default_factory=list)
    updated_ids: list[uuid.UUID] = field(default_factory=list)
    price_changes: list[PriceChange] = field(default_factory=list)
    status_changes: list[tuple[uuid.UUID, str, str]] = field(default_factory=list)
    duplicates: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    duplicates_of_active: set[uuid.UUID] = field(default_factory=set)
    new_active_ids: set[uuid.UUID] = field(default_factory=set)
    # Existing listings whose descriptive data was replaced by a richer capture (re-analyse).
    enriched_ids: list[uuid.UUID] = field(default_factory=list)
    status_updates: dict[uuid.UUID, StatusUpdate] = field(default_factory=dict)
    # Vinted/external id -> internal id, for every listing of the batch.
    ids_by_external: dict[str, uuid.UUID] = field(default_factory=dict)
    received: int = 0

    @property
    def to_analyze(self) -> list[uuid.UUID]:
        """New active listings that need an analysis (reposts of still-active items are skipped)."""
        return [i for i in self.new_ids if i in self.new_active_ids and i not in self.duplicates_of_active]


def identify_listing(engine: IdentificationEngine, pl: ProviderListing) -> IdentificationResult:
    return engine.identify(
        ListingText(
            title=pl.title,
            description=pl.description,
            brand_field=pl.brand,
            category_field=pl.category,
            subcategory_field=pl.subcategory,
            size_field=pl.size,
            condition=pl.condition,
            color_field=pl.color,
            material_field=pl.material,
        )
    )


def listing_columns(
    pl: ProviderListing, ident: IdentificationResult, catalog: Catalog, provider: str, now: datetime
) -> dict[str, Any]:
    """Map a provider listing + identification to Listing column values (pure)."""
    condition = normalize_condition(pl.condition)
    if condition == Condition.UNKNOWN:
        condition = condition_from_text(pl.description)
    category_slug = ident.category.value
    return {
        "provider": provider,
        "external_id": pl.external_id,
        "url": pl.url,
        "title": pl.title[:300],
        "description": pl.description or "",
        "price": pl.price,
        "currency": pl.currency,
        "brand_raw": (pl.brand or None) and pl.brand[:120],
        "brand_id": catalog.brand_id(ident.brand.value),
        "category_raw": (pl.category or None) and pl.category[:120],
        "subcategory_raw": (pl.subcategory or None) and pl.subcategory[:120],
        "category_id": catalog.category_id(category_slug),
        "size_raw": (pl.size or None) and pl.size[:60],
        "size_normalized": ident.size.value or normalize_size(pl.size, category_slug, ident.gender.value),
        "condition_raw": (pl.condition or None) and pl.condition[:60],
        "condition": condition.value,
        "color_raw": (pl.color or None) and pl.color[:60],
        "color": ident.color.value,
        "material_raw": (pl.material or None) and pl.material[:120],
        "material": ident.material.value,
        "country": normalize_country(pl.country),
        "model_name": ident.model.value[:120] if ident.model.value else None,
        "gender": ident.gender.value,
        "is_vintage": ident.is_vintage,
        "product_code": ident.product_code.value[:64] if ident.product_code.value else None,
        "identification_confidence": ident.confidence,
        "identification": ident.as_dict(),
        "buyer_protection_fee": pl.buyer_protection_fee,
        "shipping_fee": pl.shipping_fee,
        "buyer_protection_available": pl.buyer_protection_available,
        "favourite_count": pl.favourite_count or 0,
        "view_count": pl.view_count or 0,
        "photo_count": len(pl.images),
        "status": ListingStatus(pl.status).value,
        # Never replaced by the observation instant: an unknown date stays unknown.
        "published_at": pl.published_at,
        "published_at_kind": (pl.published_at_kind or "reported") if pl.published_at else "unknown",
        "first_seen_at": now,
        "last_seen_at": now,
        "status_changed_at": now if pl.status != ListingStatus.ACTIVE else None,
        "sold_at": pl.sold_at if pl.status == ListingStatus.SOLD else None,
        "removed_at": now if pl.status == ListingStatus.REMOVED else None,
        "title_fingerprint": title_fingerprint(pl.title),
        "raw": pl.raw or None,
        "capture_level": CaptureLevel(pl.capture_level).value,
    }


def descriptive_columns(pl: ProviderListing, ident: IdentificationResult, catalog: Catalog) -> dict[str, Any]:
    """Columns a richer capture may replace on an existing listing (not ids, dates or lifecycle)."""
    cols = listing_columns(pl, ident, catalog, "", datetime.now(UTC))
    keep = (
        "url",
        "title",
        "description",
        "currency",
        "brand_raw",
        "brand_id",
        "category_raw",
        "subcategory_raw",
        "category_id",
        "size_raw",
        "size_normalized",
        "condition_raw",
        "condition",
        "color_raw",
        "color",
        "material_raw",
        "material",
        "country",
        "model_name",
        "gender",
        "is_vintage",
        "product_code",
        "identification_confidence",
        "identification",
        "buyer_protection_fee",
        "shipping_fee",
        "buyer_protection_available",
        "photo_count",
        "title_fingerprint",
    )
    out = {k: cols[k] for k in keep}
    if pl.published_at is not None:
        out["published_at"] = pl.published_at
        out["published_at_kind"] = pl.published_at_kind or "reported"
    return out


def _lifecycle(update: StatusUpdate) -> dict[str, Any]:
    return {
        "status": update.status.value,
        "last_active_at": update.last_active_at,
        "last_active_price": update.last_active_price,
        "sold_at": update.sold_at,
        "sold_detected_at": update.sold_detected_at,
        "removed_at": update.removed_at,
        "days_to_sell": update.days_to_sell,
        "check_failures": update.check_failures,
        "unchanged_checks": update.unchanged_checks,
    }


class IngestionService:
    """Persists observations of listings.

    Every observation (new or known listing) appends a :class:`ListingSnapshot`; known listings
    are updated in place (dedup by ``(provider, external_id)``, i.e. the Vinted ID). A capture
    richer than what is stored (a full item page after a search card) replaces the descriptive
    data and photos; a poorer one never downgrades them.
    """

    def __init__(
        self,
        session: AsyncSession,
        provider: str,
        mode: AcquisitionMode | str = AcquisitionMode.PROVIDER_SCAN,
        track: bool | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.mode = AcquisitionMode(mode)
        self.track = self.mode in TRACKING_MODES if track is None else track
        self.evidence = evidence_for(self.mode)

    async def ingest(self, listings: list[ProviderListing], now: datetime | None = None) -> IngestResult:
        now = now or datetime.now(UTC)
        result = IngestResult(received=len(listings))
        if not listings:
            return result
        # Last occurrence wins if a page contains the same listing twice.
        by_ext = {pl.external_id: pl for pl in listings}
        listings = list(by_ext.values())
        catalog = await load_catalog(self.session)
        engine = get_engine(catalog.taxonomy)

        seller_ids = await self._upsert_sellers([pl.seller for pl in listings if pl.seller], now)
        existing_rows = (
            await self.session.execute(
                select(
                    Listing.id,
                    Listing.external_id,
                    Listing.price,
                    Listing.status,
                    Listing.capture_level,
                    Listing.acquisition_mode,
                    Listing.published_at,
                    Listing.last_active_at,
                    Listing.last_active_price,
                    Listing.sold_at,
                    Listing.sold_detected_at,
                    Listing.removed_at,
                    Listing.check_failures,
                    Listing.unchanged_checks,
                    Listing.tracked_at,
                    Listing.favourite_count,
                    Listing.view_count,
                    Listing.photo_count,
                    Listing.seller_id,
                ).where(Listing.provider == self.provider, Listing.external_id.in_(list(by_ext)))
            )
        ).all()
        existing = {r.external_id: r for r in existing_rows}

        new_rows: list[dict[str, Any]] = []
        new_images: list[dict[str, Any]] = []
        history_rows: list[dict[str, Any]] = []
        snapshot_rows: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        enrich_updates: list[dict[str, Any]] = []
        replace_images: dict[uuid.UUID, list[dict[str, Any]]] = {}
        product_specs: dict[str, dict[str, Any]] = {}
        row_product_key: dict[uuid.UUID, str] = {}

        for pl in listings:
            ex = existing.get(pl.external_id)
            obs = Observation(
                observed_at=now,
                evidence=self.evidence,
                status=ListingStatus(pl.status),
                price=pl.price,
                provider_sold_at=pl.sold_at if pl.status == ListingStatus.SOLD else None,
            )
            if ex is not None:
                result.ids_by_external[pl.external_id] = ex.id
                upd_status = apply_observation(
                    StatusState(
                        status=ListingStatus(ex.status),
                        published_at=ex.published_at,
                        last_active_at=ex.last_active_at,
                        last_active_price=ex.last_active_price,
                        sold_at=ex.sold_at,
                        sold_detected_at=ex.sold_detected_at,
                        removed_at=ex.removed_at,
                        check_failures=ex.check_failures,
                        unchanged_checks=ex.unchanged_checks,
                    ),
                    obs,
                )
                was_link_only = ex.capture_level == CaptureLevel.LINK
                price_changed = pl.price != ex.price and not was_link_only
                if price_changed and upd_status.unchanged_checks:
                    # A price change is a change: the listing is alive, check it sooner.
                    upd_status = replace(upd_status, unchanged_checks=0)
                result.status_updates[ex.id] = upd_status
                favourites = pl.favourite_count if pl.favourite_count is not None else ex.favourite_count
                tracked_at = ex.tracked_at or (now if self.track else None)
                upd: dict[str, Any] = {
                    "id": ex.id,
                    "last_seen_at": now,
                    "last_checked_at": now,
                    "price": pl.price,
                    "favourite_count": favourites,
                    "view_count": pl.view_count if pl.view_count is not None else ex.view_count,
                    "capture_level": max(
                        (ex.capture_level, CaptureLevel(pl.capture_level).value), key=CAPTURE_RANK.__getitem__
                    ),
                    "tracked_at": tracked_at,
                    "status_changed_at": now if upd_status.changed else None,
                    "next_check_at": schedule(
                        status=upd_status.status,
                        tracked_at=tracked_at,
                        acquisition_mode=ex.acquisition_mode,
                        now=now,
                        published_at=ex.published_at,
                        favourite_count=favourites,
                        unchanged_checks=upd_status.unchanged_checks,
                        check_failures=upd_status.check_failures,
                    ),
                    "updated_at": now,
                    **_lifecycle(upd_status),
                }
                if not upd_status.changed:
                    del upd["status_changed_at"]
                updates.append(upd)
                result.updated_ids.append(ex.id)
                if price_changed:
                    history_rows.append(
                        {"listing_id": ex.id, "price": pl.price, "currency": pl.currency, "observed_at": now}
                    )
                    result.price_changes.append(PriceChange(ex.id, ex.price, pl.price))
                if upd_status.changed:
                    result.status_changes.append((ex.id, ex.status, upd_status.status.value))

                incoming_rank = CAPTURE_RANK[CaptureLevel(pl.capture_level)]
                stored_rank = CAPTURE_RANK.get(ex.capture_level, 2)
                richer = incoming_rank > stored_rank or (
                    incoming_rank == stored_rank and self.mode != AcquisitionMode.PROVIDER_SCAN
                )
                if richer:
                    ident = identify_listing(engine, pl)
                    cols = descriptive_columns(pl, ident, catalog)
                    if not pl.images:
                        cols.pop("photo_count")  # this capture has no photos: keep the stored ones
                    elif (
                        pl.images_authoritative
                        or len(pl.images) >= (ex.photo_count or 0)
                        or incoming_rank > stored_rank
                    ):
                        replace_images[ex.id] = [
                            {
                                "listing_id": ex.id,
                                "position": pos,
                                "url": img.url,
                                "phash": img.phash,
                                "width": img.width,
                                "height": img.height,
                            }
                            for pos, img in enumerate(pl.images[:20])
                        ]
                    else:
                        cols.pop("photo_count")
                    seller_id = seller_ids.get(pl.seller.external_id) if pl.seller else None
                    enrich_updates.append(
                        {"id": ex.id, **cols, **({"seller_id": seller_id} if seller_id else {})}
                    )
                    result.enriched_ids.append(ex.id)
                snapshot_rows.append(self._snapshot(ex.id, pl, upd_status.status, now))
                continue

            ident = identify_listing(engine, pl)
            row = listing_columns(pl, ident, catalog, self.provider, now)
            row["id"] = uuid.uuid4()
            result.ids_by_external[pl.external_id] = row["id"]
            row["seller_id"] = seller_ids.get(pl.seller.external_id) if pl.seller else None
            first = apply_observation(
                StatusState(status=ListingStatus.UNKNOWN, published_at=row["published_at"]), obs
            )
            row.update(_lifecycle(first))
            row["acquisition_mode"] = self.mode.value
            row["tracked_at"] = now if self.track else None
            row["last_checked_at"] = now
            row["next_check_at"] = schedule(
                status=first.status,
                tracked_at=row["tracked_at"],
                acquisition_mode=self.mode,
                now=now,
                published_at=row["published_at"],
                favourite_count=row["favourite_count"],
            )
            new_rows.append(row)
            result.new_ids.append(row["id"])
            result.status_updates[row["id"]] = first
            if pl.status == ListingStatus.ACTIVE:
                result.new_active_ids.add(row["id"])
            if pl.capture_level != CaptureLevel.LINK:
                history_rows.append(
                    {
                        "listing_id": row["id"],
                        "price": pl.price,
                        "currency": pl.currency,
                        "observed_at": now,
                    }
                )
            snapshot_rows.append(self._snapshot(row["id"], pl, first.status, now))
            for pos, img in enumerate(pl.images[:20]):
                new_images.append(
                    {
                        "listing_id": row["id"],
                        "position": pos,
                        "url": img.url,
                        "phash": img.phash,
                        "width": img.width,
                        "height": img.height,
                    }
                )
            if key := ident.product_key:
                row_product_key[row["id"]] = key
                product_specs.setdefault(
                    key,
                    {
                        "id": uuid.uuid4(),
                        "product_key": key,
                        "brand_id": row["brand_id"],
                        "category_id": row["category_id"],
                        "model_name": row["model_name"],
                        "gender": row["gender"],
                        "canonical_name": ident.canonical_name(catalog.taxonomy)[:255],
                        "sku": row["product_code"],
                    },
                )

        if product_specs:
            product_ids = await self._upsert_products(list(product_specs.values()))
            for row in new_rows:
                key = row_product_key.get(row["id"])
                row["product_id"] = product_ids.get(key) if key else None
        for row in new_rows:
            row.setdefault("product_id", None)

        if new_rows:
            await self._detect_duplicates(new_rows, new_images, now, result)
            # Core (table) inserts: no per-row ORM bookkeeping on the ingestion hot path.
            stmt = pg_insert(Listing.__table__).on_conflict_do_nothing(
                index_elements=["provider", "external_id"]
            )
            # Sorted keys = same row-lock order in concurrent ingestions (no deadlocks).
            await self.session.execute(stmt, sorted(new_rows, key=lambda r: r["external_id"]))
        if new_images:
            await self.session.execute(pg_insert(ListingImage.__table__).on_conflict_do_nothing(), new_images)
        if history_rows:
            await self.session.execute(pg_insert(ListingPriceHistory.__table__), history_rows)
        if updates:
            # Bulk UPDATE by primary key groups parameter sets with the same keys.
            for group in _group_by_keys(updates):
                await self.session.execute(update(Listing), sorted(group, key=lambda u: str(u["id"])))
        if enrich_updates:
            for group in _group_by_keys(enrich_updates):
                await self.session.execute(update(Listing), sorted(group, key=lambda u: str(u["id"])))
        if replace_images:
            await self.session.execute(
                delete(ListingImage).where(ListingImage.listing_id.in_(list(replace_images)))
            )
            rows = [img for imgs in replace_images.values() for img in imgs]
            if rows:
                await self.session.execute(pg_insert(ListingImage.__table__).on_conflict_do_nothing(), rows)
        if snapshot_rows:
            await self.session.execute(insert(ListingSnapshot.__table__), snapshot_rows)
        await self._record_sales(result, new_rows)
        return result

    async def _record_sales(self, result: IngestResult, new_rows: list[dict[str, Any]]) -> None:
        """Listings seen sold (or no longer sold) go to the concluded sales right away."""
        sold = ListingStatus.SOLD.value
        ids = [r["id"] for r in new_rows if r["status"] == sold and r["duplicate_of_id"] is None]
        ids += [lid for lid, old, new in result.status_changes if sold in (old, new)]
        if ids:
            from app.market.sold_sales import record_vinted_sold

            await record_vinted_sold(self.session, ids)

    def _snapshot(
        self, listing_id: uuid.UUID, pl: ProviderListing, status: ListingStatus, now: datetime
    ) -> dict[str, Any]:
        link_only = pl.capture_level == CaptureLevel.LINK
        return {
            "listing_id": listing_id,
            "observed_at": now,
            "acquisition_mode": self.mode.value,
            "capture_level": CaptureLevel(pl.capture_level).value,
            "status": None if link_only else status.value,
            "price": None if link_only else pl.price,
            "currency": pl.currency,
            "favourite_count": pl.favourite_count,
            "view_count": pl.view_count,
            "photo_count": len(pl.images) if pl.images else None,
            "note": None,
        }

    async def _upsert_sellers(self, sellers: list[ProviderSeller], now: datetime) -> dict[str, uuid.UUID]:
        if not sellers:
            return {}
        # Sorted by key so concurrent upserts lock rows in the same order (no deadlocks).
        unique = {s.external_id: s for s in sorted(sellers, key=lambda s: s.external_id)}
        rows = []
        for s in unique.values():
            score = seller_reliability(SellerProfile(s.rating, s.review_count), now)
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "provider": self.provider,
                    "external_id": s.external_id,
                    "rating": s.rating,
                    "review_count": s.review_count,
                    **dict.fromkeys(
                        ("account_created_at", "item_count", "sold_count", "country", "last_active_at")
                    ),
                    "reliability_score": score.score,
                    "reliability_details": score.as_dict(),
                    "updated_at": now,
                }
            )
        stmt = pg_insert(Seller.__table__)
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider", "external_id"],
            set_={
                c: getattr(stmt.excluded, c)
                for c in (
                    "rating",
                    "review_count",
                    "item_count",
                    "sold_count",
                    "country",
                    "last_active_at",
                    "reliability_score",
                    "reliability_details",
                    "updated_at",
                )
            },
        ).returning(Seller.__table__.c.id, Seller.__table__.c.external_id)
        out: dict[str, uuid.UUID] = {}
        for chunk_start in range(0, len(rows), 500):
            res = await self.session.execute(stmt, rows[chunk_start : chunk_start + 500])
            out.update({r.external_id: r.id for r in res.all()})
        return out

    async def _upsert_products(self, specs: list[dict[str, Any]]) -> dict[str, uuid.UUID]:
        await self.session.execute(
            pg_insert(Product.__table__).on_conflict_do_nothing(index_elements=["product_key"]),
            sorted(specs, key=lambda s: s["product_key"]),
        )
        keys = [s["product_key"] for s in specs]
        rows = (
            await self.session.execute(
                select(Product.id, Product.product_key).where(Product.product_key.in_(keys))
            )
        ).all()
        return {r.product_key: r.id for r in rows}

    async def _detect_duplicates(
        self,
        new_rows: list[dict[str, Any]],
        new_images: list[dict[str, Any]],
        now: datetime,
        result: IngestResult,
    ) -> None:
        since = now - timedelta(days=REPOST_WINDOW_DAYS)
        seller_ids = {r["seller_id"] for r in new_rows if r["seller_id"]}
        candidates: dict[uuid.UUID, list[Any]] = defaultdict(list)
        if seller_ids:
            rows = (
                await self.session.execute(
                    select(
                        Listing.id,
                        Listing.seller_id,
                        Listing.title,
                        Listing.title_fingerprint,
                        Listing.price,
                        Listing.status,
                        Listing.duplicate_of_id,
                    ).where(Listing.seller_id.in_(seller_ids), Listing.first_seen_at >= since)
                )
            ).all()
            for r in rows:
                candidates[r.seller_id].append(r)

        url_rows = (
            await self.session.execute(
                select(Listing.id, Listing.url, Listing.status, Listing.duplicate_of_id).where(
                    Listing.url.in_([r["url"] for r in new_rows])
                )
            )
        ).all()
        by_url = {r.url: r for r in url_rows}

        phashes = {img["phash"] for img in new_images if img["phash"]}
        phash_hits: dict[str, list[Any]] = defaultdict(list)
        if phashes:
            rows = (
                await self.session.execute(
                    select(
                        ListingImage.phash,
                        Listing.id,
                        Listing.seller_id,
                        Listing.status,
                        Listing.duplicate_of_id,
                    )
                    .join(Listing, Listing.id == ListingImage.listing_id)
                    .where(ListingImage.phash.in_(phashes))
                )
            ).all()
            for r in rows:
                phash_hits[r.phash].append(r)
        images_by_listing: dict[uuid.UUID, list[str]] = defaultdict(list)
        for img in new_images:
            if img["phash"]:
                images_by_listing[img["listing_id"]].append(img["phash"])

        # Process oldest first so a repost inside the same batch points to its original.
        batch_seen: list[dict[str, Any]] = []
        for row in sorted(new_rows, key=lambda r: r["published_at"] or r["first_seen_at"]):
            dup_of: tuple[uuid.UUID, str, uuid.UUID | None] | None = None  # (id, status, root)
            if (u := by_url.get(row["url"])) is not None:
                dup_of = (u.id, u.status, u.duplicate_of_id)
            pool = list(candidates.get(row["seller_id"], [])) if row["seller_id"] else []
            pool += [
                _Row(
                    r["id"],
                    r["seller_id"],
                    r["title"],
                    r["title_fingerprint"],
                    r["price"],
                    r["status"],
                    r.get("duplicate_of_id"),
                )
                for r in batch_seen
                if r["seller_id"] and r["seller_id"] == row["seller_id"]
            ]
            if dup_of is None:
                for c in pool:
                    same_title = c.title_fingerprint == row["title_fingerprint"] or (
                        fuzz.token_set_ratio(c.title.lower(), row["title"].lower()) >= 92
                    )
                    similar_price = c.price > 0 and abs(float(row["price"]) / float(c.price) - 1) <= 0.25
                    if same_title and similar_price:
                        dup_of = (c.id, c.status, c.duplicate_of_id)
                        break
            reused_by_other = False
            for ph in images_by_listing.get(row["id"], []):
                for hit in phash_hits.get(ph, []):
                    if hit.id == row["id"]:
                        continue
                    if row["seller_id"] and hit.seller_id == row["seller_id"]:
                        dup_of = dup_of or (hit.id, hit.status, hit.duplicate_of_id)
                    else:
                        reused_by_other = True
            if reused_by_other:
                row["identification"] = {
                    **(row["identification"] or {}),
                    "photos_reused_by_other_seller": True,
                }
            if dup_of is not None:
                root = dup_of[2] or dup_of[0]
                row["duplicate_of_id"] = root
                result.duplicates[row["id"]] = root
                if dup_of[1] == ListingStatus.ACTIVE:
                    result.duplicates_of_active.add(row["id"])
            else:
                row["duplicate_of_id"] = None
            batch_seen.append(row)


def _group_by_keys(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[tuple(sorted(r))].append(r)
    return list(groups.values())


@dataclass
class _Row:
    id: uuid.UUID
    seller_id: uuid.UUID | None
    title: str
    title_fingerprint: str | None
    price: Decimal
    status: str
    duplicate_of_id: uuid.UUID | None


__all__ = ["IngestResult", "IngestionService", "PriceChange", "identify_listing", "listing_columns"]
