"""Analysis pipeline: load a listing, gather market data, run the engine, persist the opportunity."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import Listing, MarketComparable, MarketStatistic, Opportunity, OpportunityScore, Seller
from app.domain.enums import AcquisitionMode, CaptureLevel, ListingStatus
from app.ingestion.catalog import Catalog, load_catalog
from app.opportunities.engine import (
    AnalysisResult,
    EconomicTargets,
    SubjectContext,
    recommended_action,
    run_analysis,
)
from app.pricing.comparables import ItemProfile
from app.pricing.market_value import SegmentPrior
from app.profit.calculator import CostProfile
from app.scoring.seller import SellerProfile

log = get_logger(__name__)
CANDIDATE_LIMIT = 400
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

    def for_subject(self, subject_id: uuid.UUID | None) -> list[ItemProfile]:
        half = CANDIDATE_LIMIT // 2
        sold = [c for c in self.sold if c.id != subject_id][:half]
        active = [c for c in self.active if c.id != subject_id][:half]
        return [*sold, *active]


@dataclass
class AnalysisOutcome:
    opportunity_id: uuid.UUID
    listing_id: uuid.UUID
    is_new: bool
    previous_flip_score: int | None
    previous_price: Decimal | None
    result: AnalysisResult
    listing: Listing | None = None


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
        url=row.url,
        favourite_count=row.favourite_count,
    )


def seller_profile(seller: Seller | None, anomalies: tuple[str, ...] = ()) -> SellerProfile | None:
    if seller is None:
        return None
    return SellerProfile(
        rating=seller.rating,
        review_count=seller.review_count,
        account_created_at=seller.account_created_at,
        item_count=seller.item_count,
        sold_count=seller.sold_count,
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
            Listing.url,
            Listing.favourite_count,
        )

    async def _fetch_split(self, base: Any, now: datetime, limit: int) -> tuple[list[Any], list[Any]]:
        """Recent SOLD and recent ACTIVE items, fetched separately.

        Two bounded queries guarantee that realized prices are always represented, however many
        active listings the segment has (ordering a single query by "last seen" would let fresh
        active listings crowd out the sold ones).
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
            base.where(Listing.status == ListingStatus.ACTIVE.value, Listing.published_at >= since)
            .order_by(Listing.published_at.desc(), Listing.id)
            .limit(limit)
        )
        sold = (await self.session.execute(sold_stmt)).all()
        active = (await self.session.execute(active_stmt)).all()
        return sold, active

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
        sold, active = await self._fetch_split(base, now, CANDIDATE_LIMIT // 2 + 1)
        return CandidatePool(
            [profile_from_row(r, catalog) for r in sold], [profile_from_row(r, catalog) for r in active]
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
        sold, active = await self._fetch_split(base, now, CANDIDATE_LIMIT // 2)
        return [profile_from_row(r, catalog) for r in (*sold, *active)]

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
                )
                if st is not None
                else None
            )
        return out

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
                select(Listing.seller_id, Listing.title_fingerprint, Listing.brand_id).where(
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
            fps: dict[str | None, int] = {}
            for r in items:
                fps[r.title_fingerprint] = fps.get(r.title_fingerprint, 0) + 1
            if any(n >= 3 for fp, n in fps.items() if fp):
                anomalies.append("Molti annunci identici dello stesso venditore")
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
            seller_account_age_days=(now - seller.account_created_at).days
            if seller and seller.account_created_at
            else None,
            is_repost=listing.duplicate_of_id is not None,
            vision=ident.get("vision"),
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
        priors = await self.segment_priors([(x.brand_id, x.category_id, x.model_name) for x in listings])

        pools: dict[tuple[int, tuple[int, ...]], CandidatePool] = {}
        cost, targets = default_cost_profile(self.settings), default_targets(self.settings)
        results: list[tuple[Listing, AnalysisResult]] = []
        for listing, subject, prior in zip(listings, subjects, priors, strict=True):
            if listing.brand_id is None:
                cands = await self._unbranded_candidates(subject.profile, catalog, now)
            else:
                cat_ids = catalog.sibling_category_ids(subject.profile.category) or []
                key = (listing.brand_id, tuple(sorted(cat_ids)))
                if key not in pools:
                    pools[key] = await self.candidate_pool(listing.brand_id, cat_ids, catalog, now)
                cands = pools[key].for_subject(listing.id)
            results.append((listing, run_analysis(subject, cands, now, cost, targets, prior)))
        return await self.persist_many(results, now, mode)

    async def persist(self, listing: Listing, result: AnalysisResult, now: datetime) -> AnalysisOutcome:
        return (await self.persist_many([(listing, result)], now))[0]

    async def persist_many(
        self,
        items: list[tuple[Listing, AnalysisResult]],
        now: datetime,
        mode: AcquisitionMode | str | None = None,
    ) -> list[AnalysisOutcome]:
        """Upsert opportunities, append score history and replace comparables, all in bulk.

        Every analysis is recorded permanently in the score history with its algorithm version,
        the acquisition mode of the data and the analysis depth.
        """
        version = self.settings.algorithm_version
        ids = [listing.id for listing, _ in items]
        previous = {
            r.listing_id: r
            for r in (
                await self.session.execute(
                    select(Opportunity.listing_id, Opportunity.flip_score, Opportunity.listing_price).where(
                        Opportunity.listing_id.in_(ids)
                    )
                )
            ).all()
        }
        # Sorted by key: concurrent batches lock opportunity rows in the same order.
        ordered = sorted(items, key=lambda it: str(it[0].id))
        rows = [
            {"id": uuid.uuid4(), "created_at": now, **opportunity_values(listing, result, version, now, mode)}
            for listing, result in ordered
        ]
        # One single-row statement run with many parameter sets ("insertmanyvalues"): compiled
        # once and cached, where a 200-row VALUES clause took ~0.5 s just to compile.
        table = Opportunity.__table__
        stmt = pg_insert(table)
        stmt = stmt.on_conflict_do_update(
            index_elements=["listing_id"],
            set_={k: stmt.excluded[k] for k in rows[0] if k not in ("id", "created_at", "listing_id")},
        ).returning(table.c.id, table.c.listing_id)
        opp_ids = {r.listing_id: r.id for r in (await self.session.execute(stmt, rows)).all()}

        # Core (not ORM) executemany: no per-row ORM bookkeeping on the hot path.
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
                for listing, result in ordered
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
            )
            for listing, result in items
        ]

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
        "verdict": r.analysis.verdict.value,
        "recommended_action": recommended_action(r).value,
        "comparables_count": m.n_used,
        "sold_comparables_count": m.n_sold,
        "identification_confidence": r.subject.identification_confidence,
        "score_breakdown": {
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
