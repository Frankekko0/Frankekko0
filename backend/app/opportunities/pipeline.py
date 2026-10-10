"""Analysis pipeline: load a listing, gather market data, run the engine, persist the opportunity."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.verdicts import LLM_PROVIDERS, reclamp_analysis, reviewed_fields
from app.analysis.dossier import finalize_dossier
from app.analytics.calibration import STATE_KEY as CALIBRATION_KEY
from app.analytics.calibration import Calibration
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import (
    Analysis,
    ExternalPrice,
    ExternalSearch,
    Listing,
    ListingPriceHistory,
    MarketComparable,
    MarketStatistic,
    Opportunity,
    OpportunityScore,
    Seller,
    SoldSale,
    SystemState,
)
from app.domain.enums import AcquisitionMode, CaptureLevel, ListingStatus, Verdict
from app.external.keys import model_key
from app.identification.taxonomy import fold
from app.ingestion.catalog import Catalog, load_catalog
from app.ingestion.normalizer import title_tokens
from app.opportunities.analysis_record import (
    SCHEMA_VERSION,
    build_blocks,
    classify_trigger,
    inputs_hash,
    jsonable,
    listing_inputs,
    result_hash,
)
from app.opportunities.engine import (
    SAME_ITEM_ANOMALY,
    AnalysisResult,
    EconomicTargets,
    SubjectContext,
    recommended_action,
    run_analysis,
)
from app.pricing.comparables import ItemProfile
from app.pricing.evidence import (
    GATE_KEY,
    NEGOTIATION_KEY,
    EvidenceGate,
    ExternalRef,
    OwnRecord,
    PriceEvidence,
    discount_from_state,
    evidence_for_subject,
)
from app.pricing.market_value import SegmentPrior
from app.profit.calculator import CostProfile
from app.scoring.seller import SellerProfile

log = get_logger(__name__)
CANDIDATE_LIMIT = 400
REMOVED_LIMIT = 60
COMPARABLE_COLUMNS = (
    "listing_id",
    "comparable_listing_id",
    "similarity",
    "price",
    "adjusted_price",
    "weight",
    "is_sold",
    "included",
    "exclusion_reason",
    "computed_at",
)


def default_cost_profile(settings: Settings | None = None) -> CostProfile:
    s = settings or get_settings()
    return CostProfile(
        buyer_protection_fixed=s.default_buyer_protection_fixed,
        buyer_protection_pct=s.default_buyer_protection_pct,
        shipping_in=s.default_shipping_in,
    )


def default_targets(settings: Settings | None = None) -> EconomicTargets:
    s = settings or get_settings()
    return EconomicTargets(min_profit=s.default_min_profit, min_roi=s.default_min_roi)


def segment_key(brand_id: int | None, category_id: int | None, model: str | None, size: str | None) -> str:
    return f"{brand_id or '*'}:{category_id or '*'}:{(model or '*').lower()}:{size or '*'}"


@dataclass
class CandidatePool:
    """Comparable candidates shared by the subjects of one brand/category family."""

    sold: list[ItemProfile]
    active: list[ItemProfile]
    # Removed listings: never priced, used for the sold share and time online (as not sold).
    removed: list[ItemProfile] = field(default_factory=list)

    def for_subject(self, subject_id: uuid.UUID | None) -> list[ItemProfile]:
        half = CANDIDATE_LIMIT // 2
        sold = [c for c in self.sold if c.id != subject_id][:half]
        active = [c for c in self.active if c.id != subject_id][:half]
        removed = [c for c in self.removed if c.id != subject_id][:REMOVED_LIMIT]
        return [*sold, *active, *removed]


@dataclass
class AnalysisOutcome:
    opportunity_id: uuid.UUID
    listing_id: uuid.UUID
    is_new: bool
    previous_flip_score: int | None
    previous_price: Decimal | None
    result: AnalysisResult
    listing: Listing | None = None
    # The permanent record this outcome corresponds to; ``analysis_created`` is False when the
    # same inputs gave the same results again (the existing record is still the current one).
    analysis_id: uuid.UUID | None = None
    analysis_created: bool = False
    trigger: str | None = None
    # True when this is a new analysis the model has no valid review of (a new listing, or a change that makes the
    # old review stale): it has just entered the model-analysis queue (see ``app.ai.queue``).
    ai_pending: bool = False


_SIZE_TOKEN = re.compile(r"^(?:x{0,4}[sl]|xx+|[2-5]\d)$")
NEW_CONDITIONS = ("new_with_tags", "new_without_tags")


def item_key(title: str) -> str:
    """The title without size words: the same item listed in several sizes shares it."""
    return " ".join(sorted({t for t in title_tokens(title) if not _SIZE_TOKEN.match(t)}))


def profile_from_row(row: Any, catalog: Catalog) -> ItemProfile:
    category_slug = catalog.category_slug(row.category_id)
    return ItemProfile(
        id=row.id,
        title=row.title,
        price=row.price,
        brand=catalog.brand_slug(row.brand_id),
        category=category_slug,
        parent_category=catalog.parent_slug(category_slug),
        model=row.model_name,
        condition=row.condition,
        size=row.size_normalized,
        gender=row.gender,
        color=row.color,
        material=row.material,
        country=row.country,
        is_vintage=row.is_vintage,
        status=row.status,
        published_at=row.published_at,
        sold_at=row.sold_at,
        last_seen_at=row.last_seen_at,
        removed_at=getattr(row, "removed_at", None),
        url=row.url,
        favourite_count=row.favourite_count,
        tracked=getattr(row, "tracked_at", None) is not None,
    )


OWN_COLUMNS = (
    SoldSale.id,
    SoldSale.source,
    SoldSale.title,
    SoldSale.price_eur,
    SoldSale.brand_id,
    SoldSale.category_id,
    SoldSale.model_name,
    SoldSale.size_normalized,
    SoldSale.condition,
    SoldSale.sold_at,
    SoldSale.published_at,
    SoldSale.listing_id,
    SoldSale.purchase_id,
)
EXTERNAL_COLUMNS = (
    ExternalPrice.id,
    ExternalPrice.kind,
    ExternalPrice.source,
    ExternalPrice.price,
    ExternalPrice.currency,
    ExternalPrice.price_eur,
    ExternalPrice.observed_at,
    ExternalPrice.source_date,
    ExternalPrice.condition,
    ExternalPrice.source_url,
    ExternalPrice.title,
    ExternalPrice.model_key,
    ExternalPrice.brand_id,
    ExternalPrice.category_id,
    ExternalPrice.model_name,
    ExternalPrice.size,
)


def own_record(r: Any, catalog: Catalog) -> OwnRecord:
    """A ``sold_sales`` row of the user's own purchases/resales (``OWN_COLUMNS``)."""
    return OwnRecord(
        id=r.id,
        source=r.source,
        title=r.title,
        price_eur=float(r.price_eur),
        brand_slug=catalog.brand_slug(r.brand_id),
        category_slug=catalog.category_slug(r.category_id),
        model_name=r.model_name,
        size=r.size_normalized,
        condition=r.condition or "unknown",
        sold_at=r.sold_at,
        published_at=r.published_at,
        listing_id=r.listing_id,
        purchase_id=r.purchase_id,
    )


def external_ref(r: Any, catalog: Catalog) -> ExternalRef:
    """An ``external_prices`` row (``EXTERNAL_COLUMNS``); its date is the one the source states,
    else when the search found it."""
    return ExternalRef(
        id=r.id,
        kind=r.kind,
        source=r.source,
        price=float(r.price),
        currency=r.currency,
        price_eur=float(r.price_eur),
        at=r.source_date or r.observed_at,
        condition=r.condition or "unknown",
        url=r.source_url,
        title=r.title,
        brand_slug=catalog.brand_slug(r.brand_id) or r.model_key.split("|", 1)[0],
        category_slug=catalog.category_slug(r.category_id),
        model_name=r.model_name,
        size=r.size,
    )


def seller_profile(seller: Seller | None, anomalies: tuple[str, ...] = ()) -> SellerProfile | None:
    if seller is None:
        return None
    return SellerProfile(
        rating=seller.rating,
        review_count=seller.review_count,
        anomalies=anomalies,
    )


class AnalysisPipeline:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # ---------------------------------------------------------------- data access
    async def candidates(
        self, subject: ItemProfile, catalog: Catalog, brand_id: int | None, now: datetime
    ) -> list[ItemProfile]:
        """Comparable candidates for one subject (see ``candidate_pool``)."""
        if brand_id is None:
            return await self._unbranded_candidates(subject, catalog, now)
        pool = await self.candidate_pool(
            brand_id, catalog.sibling_category_ids(subject.category), catalog, now
        )
        return pool.for_subject(subject.id)

    def _candidate_columns(self) -> tuple[Any, ...]:
        return (
            Listing.id,
            Listing.title,
            Listing.price,
            Listing.brand_id,
            Listing.category_id,
            Listing.model_name,
            Listing.condition,
            Listing.size_normalized,
            Listing.gender,
            Listing.color,
            Listing.material,
            Listing.country,
            Listing.is_vintage,
            Listing.status,
            Listing.published_at,
            Listing.sold_at,
            Listing.last_seen_at,
            Listing.removed_at,
            Listing.url,
            Listing.favourite_count,
            Listing.tracked_at,
        )

    async def _fetch_split(
        self, base: Any, now: datetime, limit: int
    ) -> tuple[list[Any], list[Any], list[Any]]:
        """Recent SOLD, ACTIVE and REMOVED items, fetched separately.

        Bounded queries per status guarantee that realized prices are always represented, however
        many active listings the segment has (ordering a single query by "last seen" would let
        fresh active listings crowd out the sold ones). Removed listings only feed the sold share
        and time online, so a smaller quota is enough.
        """
        since = now - timedelta(days=self.settings.comparables_window_days)
        sold_states = [ListingStatus.SOLD.value]
        event_time = func.coalesce(Listing.sold_at, Listing.status_changed_at, Listing.last_seen_at)
        sold_stmt = (
            base.where(Listing.status.in_(sold_states), event_time >= since)
            .order_by(event_time.desc(), Listing.id)
            .limit(limit)
        )
        active_stmt = (
            base.where(Listing.status == ListingStatus.ACTIVE.value, Listing.listed_at >= since)
            .order_by(Listing.listed_at.desc(), Listing.id)
            .limit(limit)
        )
        removed_stmt = (
            base.where(Listing.status == ListingStatus.REMOVED.value, Listing.removed_at >= since)
            .order_by(Listing.removed_at.desc(), Listing.id)
            .limit(REMOVED_LIMIT + 1)
        )
        sold = (await self.session.execute(sold_stmt)).all()
        active = (await self.session.execute(active_stmt)).all()
        removed = (await self.session.execute(removed_stmt)).all()
        return sold, active, removed

    async def candidate_pool(
        self, brand_id: int, category_ids: list[int] | None, catalog: Catalog, now: datetime
    ) -> CandidatePool:
        """Candidates shared by every subject of the same brand and category family.

        One extra row per half lets each subject drop itself and still keep the full quota, so
        the result is identical to a per-subject query.
        """
        base = select(*self._candidate_columns()).where(
            Listing.duplicate_of_id.is_(None), Listing.brand_id == brand_id
        )
        if category_ids:
            base = base.where(Listing.category_id.in_(category_ids))
        sold, active, removed = await self._fetch_split(base, now, CANDIDATE_LIMIT // 2 + 1)
        return CandidatePool(
            [profile_from_row(r, catalog) for r in sold],
            [profile_from_row(r, catalog) for r in active],
            [profile_from_row(r, catalog) for r in removed],
        )

    async def _unbranded_candidates(
        self, subject: ItemProfile, catalog: Catalog, now: datetime
    ) -> list[ItemProfile]:
        # Unknown brand: only compare with other unidentified items with a similar title.
        base = select(*self._candidate_columns()).where(
            Listing.duplicate_of_id.is_(None),
            Listing.brand_id.is_(None),
            func.similarity(Listing.title, subject.title) > 0.25,
        )
        if subject.id is not None:
            base = base.where(Listing.id != subject.id)
        category_ids = catalog.sibling_category_ids(subject.category)
        if category_ids:
            base = base.where(Listing.category_id.in_(category_ids))
        sold, active, removed = await self._fetch_split(base, now, CANDIDATE_LIMIT // 2)
        return [profile_from_row(r, catalog) for r in (*sold, *active, *removed[:REMOVED_LIMIT])]

    async def segment_prior(
        self, brand_id: int | None, category_id: int | None, model: str | None
    ) -> SegmentPrior | None:
        return (await self.segment_priors([(brand_id, category_id, model)]))[0]

    async def segment_priors(
        self, segments: list[tuple[int | None, int | None, str | None]]
    ) -> list[SegmentPrior | None]:
        """Market-database priors for many (brand, category, model) segments in one query."""
        wanted: list[list[str]] = []
        for brand_id, category_id, model in segments:
            if brand_id is None or category_id is None:
                wanted.append([])
                continue
            keys = [segment_key(brand_id, category_id, model, None)] if model else []
            keys.append(segment_key(brand_id, category_id, None, None))
            wanted.append(keys)
        all_keys = {k for keys in wanted for k in keys}
        by_key: dict[str, MarketStatistic] = {}
        if all_keys:
            rows = (
                await self.session.execute(
                    select(MarketStatistic).where(MarketStatistic.segment_key.in_(all_keys))
                )
            ).scalars()
            by_key = {r.segment_key: r for r in rows}
        out: list[SegmentPrior | None] = []
        for keys in wanted:
            st = next((by_key[k] for k in keys if k in by_key), None)
            out.append(
                SegmentPrior(
                    median_price=float(st.median_price),
                    p25_price=float(st.p25_price),
                    p75_price=float(st.p75_price),
                    sample_size=st.sample_size,
                    ask_to_sale_ratio=float(st.ask_to_sale_ratio) if st.ask_to_sale_ratio else None,
                    sell_through_rate=float(st.sell_through_rate),
                    avg_days_to_sale=float(st.avg_days_to_sale) if st.avg_days_to_sale else None,
                    level="model" if st.model_name else "brand_category",
                )
                if st is not None
                else None
            )
        return out

    async def evidence_state(self) -> tuple[Calibration, EvidenceGate, float | None]:
        """Calibration, backtest gate and negotiation discount in one query."""
        rows = (
            await self.session.execute(
                select(SystemState.key, SystemState.value).where(
                    SystemState.key.in_((CALIBRATION_KEY, GATE_KEY, NEGOTIATION_KEY))
                )
            )
        ).all()
        state = {r.key: r.value for r in rows}
        return (
            Calibration.from_state(state.get(CALIBRATION_KEY)),
            EvidenceGate.from_state(state.get(GATE_KEY)),
            discount_from_state(state.get(NEGOTIATION_KEY)),
        )

    async def price_evidence(
        self,
        subjects: list[ItemProfile],
        catalog: Catalog,
        gate: EvidenceGate,
        discount: float | None,
    ) -> list[PriceEvidence]:
        """Own records and external prices of the batch's models, two queries for the whole batch
        (never a query per listing, never an external search: only what the database holds)."""
        wanted = {(s.brand, fold(s.model)) for s in subjects if s.brand and s.model}
        own: list[OwnRecord] = []
        refs: list[ExternalRef] = []
        if wanted:
            brand_ids = {bid for slug, _ in wanted if (bid := catalog.brand_id(slug)) is not None}
            own_rows = (
                await self.session.execute(
                    select(*OWN_COLUMNS).where(
                        SoldSale.source.in_(("own_sale", "own_purchase")),
                        SoldSale.brand_id.in_(brand_ids),
                        SoldSale.is_outlier.is_(False),
                    )
                )
            ).all()
            own = [
                own_record(r, catalog)
                for r in own_rows
                if (catalog.brand_slug(r.brand_id), fold(r.model_name or "")) in wanted
            ]
            keys = sorted({model_key(slug, model) for slug, model in wanted})
            ext_rows = (
                await self.session.execute(
                    select(*EXTERNAL_COLUMNS).where(
                        ExternalPrice.model_key.in_(keys), ExternalPrice.is_outlier.is_(False)
                    )
                )
            ).all()
            refs = [external_ref(r, catalog) for r in ext_rows]
        own_by: dict[tuple[str | None, str], list[OwnRecord]] = {}
        for rec in own:
            own_by.setdefault((rec.brand_slug, fold(rec.model_name or "")), []).append(rec)
        refs_by: dict[tuple[str | None, str], list[ExternalRef]] = {}
        for ref in refs:
            refs_by.setdefault((ref.brand_slug, fold(ref.model_name or "")), []).append(ref)
        # Subjects of the same model and category share the same evidence (read-only).
        out: list[PriceEvidence] = []
        shared: dict[tuple[Any, ...], PriceEvidence] = {}
        for s in subjects:
            key = (s.brand, fold(s.model or ""))
            cache_key = (*key, s.category, s.parent_category)
            if cache_key not in shared:
                shared[cache_key] = evidence_for_subject(
                    s,
                    own_by.get(key, []),
                    refs_by.get(key, []),
                    negotiation_discount=discount,
                    gate=gate,
                    parent_of=catalog.parent_slug,
                )
            out.append(shared[cache_key])
        return out

    async def record_demand(self, listings: list[Listing], catalog: Catalog) -> None:
        """Models seen while analysing join the refresh queue of the external search
        (``demand`` counts analyses since the last search; nothing is searched here)."""
        rows: dict[str, dict[str, Any]] = {}
        for x in listings:
            slug = catalog.brand_slug(x.brand_id)
            if not slug or not x.model_name:
                continue
            key = model_key(slug, x.model_name)
            row = rows.setdefault(
                key,
                {
                    "model_key": key,
                    "brand_id": x.brand_id,
                    "category_id": x.category_id,
                    "model_name": x.model_name[:120],
                    "demand": 0,
                    "status": "pending",
                },
            )
            row["demand"] += 1
        if not rows:
            return
        table = ExternalSearch.__table__
        stmt = pg_insert(table)
        stmt = stmt.on_conflict_do_update(
            index_elements=["model_key"],
            set_={"demand": table.c.demand + stmt.excluded.demand, "updated_at": func.now()},
        )
        # Sorted keys: concurrent batches lock the same rows in the same order (no deadlocks).
        await self.session.execute(stmt, sorted(rows.values(), key=lambda r: r["model_key"]))

    async def seller_anomalies(
        self, seller_id: uuid.UUID | None, catalog: Catalog, now: datetime
    ) -> tuple[str, ...]:
        if seller_id is None:
            return ()
        return (await self.sellers_anomalies([seller_id], catalog, now)).get(seller_id, ())

    async def sellers_anomalies(
        self, seller_ids: list[uuid.UUID], catalog: Catalog, now: datetime
    ) -> dict[uuid.UUID, tuple[str, ...]]:
        """Suspicious seller patterns (many identical listings, many counterfeit-prone brands)."""
        if not seller_ids:
            return {}
        rows = (
            await self.session.execute(
                select(
                    Listing.seller_id,
                    Listing.title,
                    Listing.brand_id,
                    Listing.size_normalized,
                    Listing.condition,
                ).where(
                    Listing.seller_id.in_(set(seller_ids)),
                    Listing.status == ListingStatus.ACTIVE,
                    Listing.first_seen_at >= now - timedelta(days=30),
                )
            )
        ).all()
        by_seller: dict[uuid.UUID, list[Any]] = {}
        for r in rows:
            by_seller.setdefault(r.seller_id, []).append(r)
        out: dict[uuid.UUID, tuple[str, ...]] = {}
        for seller_id, items in by_seller.items():
            anomalies: list[str] = []
            same: dict[str, list[Any]] = {}
            for r in items:
                same.setdefault(item_key(r.title), []).append(r)
            if any(
                len(group) >= 3
                or (
                    len({g.size_normalized for g in group if g.size_normalized}) >= 2
                    and all(g.condition in NEW_CONDITIONS for g in group)
                )
                for key, group in same.items()
                if key
            ):
                anomalies.append(SAME_ITEM_ANOMALY)
            risky = sum(
                1
                for r in items
                if r.brand_id in catalog.brands_by_id
                and catalog.brands_by_id[r.brand_id].counterfeit_risk >= 0.3
            )
            if risky >= 4:
                anomalies.append("Molti annunci di brand spesso contraffatti in poco tempo")
            out[seller_id] = tuple(anomalies)
        return out

    async def calibration(self) -> Calibration:
        state = await self.session.get(SystemState, CALIBRATION_KEY)
        return Calibration.from_state(state.value if state else None)

    async def price_histories(
        self, listing_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[tuple[datetime, Decimal]]]:
        if not listing_ids:
            return {}
        rows = (
            await self.session.execute(
                select(
                    ListingPriceHistory.listing_id, ListingPriceHistory.observed_at, ListingPriceHistory.price
                )
                .where(ListingPriceHistory.listing_id.in_(listing_ids))
                .order_by(ListingPriceHistory.listing_id, ListingPriceHistory.observed_at)
            )
        ).all()
        out: dict[uuid.UUID, list[tuple[datetime, Decimal]]] = {}
        for r in rows:
            out.setdefault(r.listing_id, []).append((r.observed_at, r.price))
        return out

    async def seller_habits(self, seller_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
        """How often each seller lowered prices on the listings seen so far (offer leverage)."""
        if not seller_ids:
            return {}
        top = func.max(ListingPriceHistory.price)
        rows = (
            await self.session.execute(
                select(Listing.seller_id, Listing.status, Listing.price, top.label("top"))
                .outerjoin(ListingPriceHistory, ListingPriceHistory.listing_id == Listing.id)
                .where(Listing.seller_id.in_(set(seller_ids)), Listing.duplicate_of_id.is_(None))
                .group_by(Listing.seller_id, Listing.id)
            )
        ).all()
        out: dict[uuid.UUID, dict[str, Any]] = {}
        for r in rows:
            h = out.setdefault(
                r.seller_id, {"listings": 0, "with_drops": 0, "drops": [], "sold_after_drop": 0}
            )
            h["listings"] += 1
            if r.top is not None and r.top > r.price > 0:
                h["with_drops"] += 1
                h["drops"].append(float((r.top - r.price) / r.top))
                h["sold_after_drop"] += r.status == ListingStatus.SOLD
        for h in out.values():
            drops = h.pop("drops")
            h["avg_drop_pct"] = sum(drops) / len(drops) if drops else None
        return out

    def subject_context(
        self, listing: Listing, catalog: Catalog, now: datetime, anomalies: tuple[str, ...]
    ) -> SubjectContext:
        profile = profile_from_row(listing, catalog)
        brand = catalog.brands_by_id.get(listing.brand_id) if listing.brand_id else None
        category = catalog.categories_by_id.get(listing.category_id) if listing.category_id else None
        published = listing.published_at or listing.first_seen_at
        seller = listing.seller
        ident = dict(listing.identification or {})
        return SubjectContext(
            profile=profile,
            description_length=len(listing.description or ""),
            photo_count=listing.photo_count,
            favourite_count=listing.favourite_count,
            listing_age_hours=max(0.0, (now - published).total_seconds() / 3600) if published else None,
            shipping_fee=listing.shipping_fee,
            brand_name=brand.name if brand else None,
            brand_counterfeit_risk=brand.counterfeit_risk if brand else 0.1,
            category_baseline_days=category.baseline_days if category else 14,
            identification=ident,
            identification_confidence=listing.identification_confidence or 0,
            seller=seller_profile(seller, anomalies),
            is_repost=listing.duplicate_of_id is not None,
            vision=ident.get("vision"),
            description=listing.description or "",
            buyer_protection_fee=listing.buyer_protection_fee,
        )

    # ---------------------------------------------------------------- analysis
    async def analyze_listing(
        self, listing_id: uuid.UUID, now: datetime | None = None
    ) -> AnalysisOutcome | None:
        outcomes = await self.analyze_many([listing_id], now)
        return outcomes[0] if outcomes else None

    async def analyze_many(
        self,
        listing_ids: Sequence[uuid.UUID],
        now: datetime | None = None,
        mode: AcquisitionMode | str | None = None,
        trigger: str | None = None,
    ) -> list[AnalysisOutcome]:
        """Analyse a batch of listings with a handful of queries instead of a dozen per listing.

        Listings of the same brand and category family share one comparables pool; priors,
        seller signals and all writes are fetched or flushed once for the whole batch.
        Inactive listings are deactivated and skipped. Outcomes follow the input order.
        """
        now = now or datetime.now(UTC)
        unique_ids = list(dict.fromkeys(listing_ids))
        if not unique_ids:
            return []
        loaded = (
            (
                await self.session.execute(
                    select(Listing).options(selectinload(Listing.images)).where(Listing.id.in_(unique_ids))
                )
            )
            .unique()
            .scalars()
            .all()
        )
        by_id = {listing.id: listing for listing in loaded}
        inactive = [lid for lid, listing in by_id.items() if listing.status != ListingStatus.ACTIVE]
        await self.deactivate(inactive)
        listings = [
            by_id[lid] for lid in unique_ids if lid in by_id and by_id[lid].status == ListingStatus.ACTIVE
        ]
        if not listings:
            return []

        catalog = await load_catalog(self.session)
        anomalies = await self.sellers_anomalies(
            [x.seller_id for x in listings if x.seller_id is not None], catalog, now
        )
        subjects = [
            self.subject_context(x, catalog, now, anomalies.get(x.seller_id, ()) if x.seller_id else ())
            for x in listings
        ]
        histories = await self.price_histories([x.id for x in listings])
        habits = await self.seller_habits([x.seller_id for x in listings if x.seller_id is not None])
        for x, subject in zip(listings, subjects, strict=True):
            subject.price_history = histories.get(x.id, [])
            subject.seller_habits = habits.get(x.seller_id) if x.seller_id else None
        priors = await self.segment_priors([(x.brand_id, x.category_id, x.model_name) for x in listings])
        calibration, gate, discount = await self.evidence_state()
        evidence = await self.price_evidence([s.profile for s in subjects], catalog, gate, discount)

        pools: dict[tuple[int, tuple[int, ...]], CandidatePool] = {}
        cost, targets = default_cost_profile(self.settings), default_targets(self.settings)
        results: list[tuple[Listing, AnalysisResult]] = []
        for listing, subject, prior, ev in zip(listings, subjects, priors, evidence, strict=True):
            if listing.brand_id is None:
                cands = await self._unbranded_candidates(subject.profile, catalog, now)
            else:
                cat_ids = catalog.sibling_category_ids(subject.profile.category) or []
                key = (listing.brand_id, tuple(sorted(cat_ids)))
                if key not in pools:
                    pools[key] = await self.candidate_pool(listing.brand_id, cat_ids, catalog, now)
                cands = pools[key].for_subject(listing.id)
            results.append(
                (
                    listing,
                    run_analysis(
                        subject, cands, now, cost, targets, prior, calibration=calibration, evidence=ev
                    ),
                )
            )
        outcomes = await self.persist_many(results, now, mode, trigger)
        await self.record_demand(listings, catalog)
        return outcomes

    async def persist(self, listing: Listing, result: AnalysisResult, now: datetime) -> AnalysisOutcome:
        return (await self.persist_many([(listing, result)], now))[0]

    async def persist_many(
        self,
        items: list[tuple[Listing, AnalysisResult]],
        now: datetime,
        mode: AcquisitionMode | str | None = None,
        trigger: str | None = None,
    ) -> list[AnalysisOutcome]:
        """Upsert opportunities, record the analyses, append score history, replace comparables.

        An analysis whose inputs, algorithm version and results equal the current one's adds
        nothing (the current record stays); any other is stored for good in ``analyses`` with the
        reason it ran, and in the score history with its algorithm version, the acquisition mode
        of the data and the analysis depth.
        """
        version = self.settings.algorithm_version
        ids = [listing.id for listing, _ in items]
        # The model's review of a row is kept (not overwritten with the rules' text) while it is still valid for the
        # current analysis; the JSON of those rows is read only for them.
        reviewed = Opportunity.ai_provider.in_(sorted(LLM_PROVIDERS)) & (
            Opportunity.ai_for_analysis_id == Opportunity.analysis_id
        )
        previous = {
            r.listing_id: r
            for r in (
                await self.session.execute(
                    select(
                        Opportunity.listing_id,
                        Opportunity.flip_score,
                        Opportunity.listing_price,
                        Opportunity.dossier,
                        Opportunity.analysis_id,
                        Opportunity.ai_provider,
                        Opportunity.ai_for_analysis_id,
                        Opportunity.ai_attempts,
                        Opportunity.ai_next_attempt_at,
                        Opportunity.ai_last_error,
                        reviewed.label("ai_reviewed"),
                        case((reviewed, Opportunity.ai_analysis)).label("kept_ai_analysis"),
                        case((reviewed, Opportunity.ai_analyzed_at)).label("kept_ai_analyzed_at"),
                        case((reviewed, Opportunity.verdict)).label("kept_verdict"),
                        case((reviewed, Opportunity.decision)).label("kept_decision"),
                        case((reviewed, Opportunity.decision_verdict)).label("kept_decision_verdict"),
                        case((reviewed, Opportunity.recommended_action)).label("kept_recommended_action"),
                    ).where(Opportunity.listing_id.in_(ids))
                )
            ).all()
        }
        # Sorted by key: concurrent batches lock opportunity rows in the same order.
        ordered = sorted(items, key=lambda it: str(it[0].id))
        current = await self._current_analyses(ids)
        rows: list[dict[str, Any]] = []
        analysis_rows: list[dict[str, Any]] = []
        analysis_info: dict[uuid.UUID, tuple[uuid.UUID, bool, str | None]] = {}
        ai_pending: dict[uuid.UUID, bool] = {}
        for listing, result in ordered:
            values = opportunity_values(listing, result, version, now, mode)
            inputs = listing_inputs(listing)
            prev = current.get(listing.id)
            if values.get("dossier"):  # what moved since the last analysis, in words
                old = previous.get(listing.id)
                values["dossier"] = finalize_dossier(
                    values["dossier"],
                    old.dossier if old is not None else None,
                    prev.inputs if prev is not None else None,
                    jsonable(inputs),
                )
            in_hash, out_hash = inputs_hash(inputs), result_hash(values)
            if prev is not None and (prev.input_hash, prev.result_hash, prev.algorithm_version) == (
                in_hash,
                out_hash,
                version,
            ):
                analysis_id, created, why = prev.id, False, None
            else:
                analysis_id, created = uuid.uuid4(), True
                why = classify_trigger(prev.inputs if prev is not None else None, inputs, trigger)
                analysis_rows.append(
                    {
                        "id": analysis_id,
                        "listing_id": listing.id,
                        "vinted_id": listing.external_id if listing.provider == "vinted" else None,
                        "provider": listing.provider,
                        "url": listing.url,
                        "source": str(mode or listing.acquisition_mode),
                        "created_at": now,
                        "schema_version": SCHEMA_VERSION,
                        "algorithm_version": version,
                        "trigger": why,
                        "input_hash": in_hash,
                        "result_hash": out_hash,
                        "inputs": jsonable(inputs),
                        **build_blocks(listing, result, values),
                    }
                )
            analysis_info[listing.id] = (analysis_id, created, why)
            row = {"id": uuid.uuid4(), "created_at": now, **values, "analysis_id": analysis_id}
            ai_pending[listing.id] = self._keep_review(
                row, previous.get(listing.id), prev, created, analysis_id, inputs, version
            )
            rows.append(row)
        if analysis_rows:
            await self.session.execute(Analysis.__table__.insert(), analysis_rows)
        # One single-row statement run with many parameter sets ("insertmanyvalues"): compiled
        # once and cached, where a 200-row VALUES clause took ~0.5 s just to compile.
        table = Opportunity.__table__
        stmt = pg_insert(table)
        stmt = stmt.on_conflict_do_update(
            index_elements=["listing_id"],
            set_={k: stmt.excluded[k] for k in rows[0] if k not in ("id", "created_at", "listing_id")},
        ).returning(table.c.id, table.c.listing_id)
        opp_ids = {r.listing_id: r.id for r in (await self.session.execute(stmt, rows)).all()}

        # Core (not ORM) executemany: no per-row ORM bookkeeping on the hot path. One score row per
        # *new* analysis: an identical re-run adds nothing.
        recorded = [(listing, result) for listing, result in ordered if analysis_info[listing.id][1]]
        if recorded:
            await self.session.execute(
                OpportunityScore.__table__.insert(),
                [
                    {
                        "opportunity_id": opp_ids[listing.id],
                        "algorithm_version": version,
                        "acquisition_mode": str(mode or listing.acquisition_mode),
                        "analysis_depth": analysis_depth(listing),
                        "data_quality": result.data_quality,
                        "listing_price": listing.price,
                        "flip_score": result.flip.score,
                        "confidence_score": result.confidence.score,
                        "risk_score": result.risk.score,
                        "components": result.flip.components,
                        "penalties": result.flip.penalties,
                        "expected_roi": result.expected_roi,
                        "computed_at": now,
                    }
                    for listing, result in recorded
                ],
            )
        await self.session.execute(delete(MarketComparable).where(MarketComparable.listing_id.in_(ids)))
        records = [
            (
                listing.id,
                c.item.id,
                Decimal(str(round(c.similarity, 4))),
                c.item.price,
                Decimal(str(round(c.adjusted_price, 2))),
                Decimal(str(round(min(c.weight, 999999), 4))),
                c.is_sold,
                c.included,
                c.exclusion_reason,
                now,
            )
            for listing, result in ordered
            for c in result.comparables
            if c.item.id is not None
        ]
        if records:
            await self._copy_comparables(records)

        return [
            AnalysisOutcome(
                opportunity_id=opp_ids[listing.id],
                listing_id=listing.id,
                is_new=listing.id not in previous,
                previous_flip_score=previous[listing.id].flip_score if listing.id in previous else None,
                previous_price=previous[listing.id].listing_price if listing.id in previous else None,
                result=result,
                listing=listing,
                analysis_id=analysis_info[listing.id][0],
                analysis_created=analysis_info[listing.id][1],
                trigger=analysis_info[listing.id][2],
                ai_pending=ai_pending[listing.id],
            )
            for listing, result in items
        ]

    def _keep_review(
        self,
        row: dict[str, Any],
        old: Any,
        prev: Any,
        created: bool,
        analysis_id: uuid.UUID,
        inputs: dict[str, Any],
        version: str,
    ) -> bool:
        """Decide what happens to the model's review of this listing when its row is written (``row`` is updated in
        place); returns True when this is a new analysis the model has no valid review of (the row enters the
        queue now: the batch may kick it; a row that has been waiting since before is left to the sweep).

        * No valid review (a new row, or only the rules' text): the rules' text is written as before, and when
          the analysis is a new one the queue state starts over. The same analysis again leaves the queue state
          (backoff, attempts) alone.
        * A review and the same analysis again: nothing the model wrote is touched.
        * A review and a new analysis that is not a material change (only the market moved a little: same inputs
          and algorithm, the flip score within ``ai_reanalyze_flip_delta``, the same engine verdict): the review
          is kept, its two numbers are held to the new computed ones and its caution is applied again to the new
          decision (never the old decision copied: that would carry old numbers).
        * A review and a material change: the rules' text is written and the review is queued again.
        """
        fresh = {
            "ai_for_analysis_id": None,
            "ai_attempts": 0,
            "ai_next_attempt_at": None,
            "ai_last_error": None,
        }
        if old is None:
            row |= fresh
            return True
        if not old.ai_reviewed:
            if created:
                row |= fresh
                return True
            row |= {k: getattr(old, k) for k in fresh}  # the same analysis again: the backoff stands
            return False
        stored = {
            "ai_analysis": old.kept_ai_analysis,
            "ai_provider": old.ai_provider,
            "ai_analyzed_at": old.kept_ai_analyzed_at,
            "verdict": old.kept_verdict,
            "decision": old.kept_decision,
            "decision_verdict": old.kept_decision_verdict,
            "recommended_action": old.kept_recommended_action,
        }
        if not created:
            row |= stored | {k: getattr(old, k) for k in fresh}
            return False
        material = (
            prev is None
            or prev.algorithm_version != version
            or jsonable(inputs) != prev.inputs
            or abs(row["flip_score"] - old.flip_score) >= self.settings.ai_reanalyze_flip_delta
            or row["decision_verdict"] != prev.decision_verdict
        )
        if material:
            row |= fresh
            return True
        analysis = reclamp_analysis(
            old.kept_ai_analysis or {},
            max_buy_price=row["max_buy_price"],
            quick_sale_price=row["quick_sale_price"],
            optimistic_sale_price=row["optimistic_sale_price"],
        )
        row |= {
            "ai_analysis": analysis,
            "ai_provider": old.ai_provider,
            "ai_analyzed_at": old.kept_ai_analyzed_at,
            **reviewed_fields(
                row["decision"], row["verdict"], Verdict(analysis.get("verdict", row["verdict"]))
            ),
            **fresh,
            "ai_for_analysis_id": analysis_id,
        }
        return False

    async def _current_analyses(self, listing_ids: list[uuid.UUID]) -> dict[uuid.UUID, Any]:
        """The analysis each listing's opportunity points at (what the next one is compared to)."""
        rows = await self.session.execute(
            select(
                Opportunity.listing_id,
                Analysis.id,
                Analysis.input_hash,
                Analysis.result_hash,
                Analysis.algorithm_version,
                Analysis.inputs,
                Analysis.decision["decision_verdict"].astext.label(
                    "decision_verdict"
                ),  # the engine's, never lowered
            )
            .join(Analysis, Analysis.id == Opportunity.analysis_id)
            .where(Opportunity.listing_id.in_(listing_ids))
        )
        return {r.listing_id: r for r in rows}

    async def _copy_comparables(self, records: list[tuple[Any, ...]]) -> None:
        """Bulk-load comparables with PostgreSQL COPY (much faster than INSERT for thousands of rows).

        Rows for these listings were deleted in this same transaction. If a concurrent batch
        analysed the same listing, COPY fails on the unique key and the job is retried.
        """
        conn = await self.session.connection()
        raw = await conn.get_raw_connection()
        await raw.driver_connection.copy_records_to_table(  # type: ignore[union-attr]
            MarketComparable.__tablename__, records=records, columns=COMPARABLE_COLUMNS
        )

    async def deactivate(self, listing_ids: list[uuid.UUID]) -> None:
        if listing_ids:
            await self.session.execute(
                update(Opportunity).where(Opportunity.listing_id.in_(listing_ids)).values(is_active=False)
            )


def analysis_depth(listing: Listing) -> str:
    """ "quick" when only a search card (or a link) is known, "full" with the item page data."""
    return "quick" if listing.capture_level in (CaptureLevel.CARD, CaptureLevel.LINK) else "full"


def opportunity_values(
    listing: Listing,
    r: AnalysisResult,
    version: str,
    now: datetime,
    mode: AcquisitionMode | str | None = None,
) -> dict[str, Any]:
    m = r.market
    st = m.stats
    cons = r.scenario("conservative")
    exp = r.scenario("expected")
    opt = r.scenario("optimistic")

    def dec(v: float | None) -> Decimal | None:
        return Decimal(str(round(v, 2))) if v is not None else None

    return {
        "listing_id": listing.id,
        "product_id": listing.product_id,
        "algorithm_version": version,
        "acquisition_mode": str(mode or listing.acquisition_mode),
        "analysis_depth": analysis_depth(listing),
        "data_quality": r.data_quality,
        "insufficient_reason": r.insufficient_reason,
        "headline": r.headline[:200],
        "is_active": listing.status == ListingStatus.ACTIVE,
        "listing_price": listing.price,
        "currency": listing.currency,
        "fair_market_value": m.fair_market_value,
        "market_median": dec(st.median) if st else None,
        "market_mean": dec(st.mean) if st else None,
        "market_p25": dec(st.p25) if st else None,
        "market_p75": dec(st.p75) if st else None,
        "market_min": dec(st.min_reasonable) if st else None,
        "market_max": dec(st.max_reasonable) if st else None,
        "quick_sale_price": m.quick_sale_price,
        "expected_sale_price": m.expected_sale_price,
        "optimistic_sale_price": m.optimistic_sale_price,
        "discount_vs_market": r.discount_vs_market,
        "total_acquisition_cost": r.total_acquisition_cost,
        "expected_net_revenue": exp.result.sale.net if exp else None,
        "expected_profit": exp.result.net_profit if exp else None,
        "expected_roi": exp.result.roi if exp else None,
        "conservative_profit": cons.result.net_profit if cons else None,
        "conservative_roi": cons.result.roi if cons else None,
        "optimistic_profit": opt.result.net_profit if opt else None,
        "optimistic_roi": opt.result.roi if opt else None,
        "max_buy_price": r.max_buy_price,
        "good_buy_price": r.good_buy_price,
        "suggested_offer": r.offer.suggested_offer,
        "demand_level": r.demand.level.value,
        "demand_score": r.demand.score,
        "sell_through_rate": Decimal(str(r.demand.sell_through_rate)),
        "velocity_score": r.velocity.score,
        "estimated_days_to_sell": Decimal(str(r.velocity.estimated_days)),
        "velocity_bucket": r.velocity.bucket.value,
        "flip_score": r.flip.score,
        "confidence_score": r.confidence.score,
        "risk_score": r.risk.score,
        "risk_level": r.risk.level.value,
        "seller_score": r.seller.score,
        "deal_tier": r.tier.value,
        "is_ultra_deal": r.ultra,
        "verdict": (r.decision.legacy_verdict if r.decision else r.analysis.verdict).value,
        "decision_verdict": r.decision.verdict.value if r.decision else None,
        "data_completeness_score": r.completeness.score if r.completeness else None,
        "analysis_coverage_score": r.dossier.get("analysis_coverage") if r.dossier else None,
        "dossier": r.dossier or None,
        "decision": r.decision.as_dict() | {"completeness": r.completeness.as_dict()}
        if r.decision and r.completeness
        else None,
        "recommended_action": recommended_action(r).value,
        "comparables_count": m.n_used,
        "sold_comparables_count": m.n_sold,
        "identification_confidence": r.subject.identification_confidence,
        "risk_adjusted_profit": dec(r.risk_adjusted_profit),
        "sale_probability": Decimal(str(r.sale_probability)) if r.sale_probability is not None else None,
        "authenticity_probability": Decimal(str(r.authenticity["p_authentic"])) if r.authenticity else None,
        "authenticity_verdict": r.authenticity.get("verdict") if r.authenticity else None,
        "score_breakdown": {
            "insights": r.insights,
            "components": r.flip.components,
            "penalties": r.flip.penalties,
            "cap": r.flip.cap,
            "base": r.flip.base,
            "confidence_components": r.confidence.components,
            "seller": r.seller.as_dict(),
            "demand": {
                "observations": r.demand.observations,
                "raw_sell_through_rate": r.demand.raw_sell_through_rate,
                "favourites_signal": r.demand.favourites_signal,
                "pool_size": r.pool_size,
            },
            "velocity": {
                "sample_size": r.velocity.sample_size,
                "quick_sale_days": r.velocity.quick_sale_days,
                "optimistic_sale_days": r.velocity.optimistic_sale_days,
            },
            "offer": {"action": r.offer.action.value, "rationale": r.offer.rationale},
            "market_comparison": r.market_comparison,
            "velocity_detail": r.time_online,
            "risk_signals": [s.as_dict() for s in r.risk_signals],
            "headline": r.headline,
            "provenance": r.provenance,
            "economics": r.economics,
        },
        "explanation": r.explanation,
        "risk_factors": r.risk.as_list(),
        "market_snapshot": m.snapshot(),
        "ai_analysis": r.analysis.model_dump(mode="json"),
        "ai_provider": r.analysis.provider,
        "ai_analyzed_at": now,
        "analyzed_at": now,
        "updated_at": now,
    }
