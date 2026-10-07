"""External price references: refresh of the per-model cache.

Never called while a page is analysed. The analysis only queues the models it sees
(``external_searches.demand``); a worker job (``app.external.jobs``) searches the models due,
highest demand first, within the query budget:

1. the queries of ``app.external.queries`` (2 per model, a 3rd for concluded sales only when the
   first two found the model and the budget allows);
2. every result parsed (``app.external.parse``) and matched strictly to the model
   (``app.external.matching``); rejections counted per reason;
3. kept prices stored in ``external_prices`` (source, dates, currency, EUR price, condition, kind,
   match details), deduplicated by ``sha256(kind|url|price)``; implausible prices of the model
   flagged as outliers (log space);
4. ``external_searches`` updated (status, counts, queries used, next refresh in
   ``EXTERNAL_REFRESH_DAYS``; exponential backoff after an error);
5. new concluded sales copied into ``sold_sales`` (``sync_sold_sales``).

A model nobody has looked at since its last search (``demand = 0``) is not searched again.
The run commits after every model, so the queries spent are recorded even if a later one fails.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, and_, cast, func, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import ExternalPrice, ExternalSearch
from app.external.fx import to_eur
from app.external.keys import model_key
from app.external.matching import MAX_PRICE_EUR, MIN_PRICE_EUR, REASONS, ModelSpec, match_offer, model_spec
from app.external.parse import Offer, extract_size, parse_organic, parse_shopping
from app.external.provider import (
    COST_PER_QUERY_USD,
    FREE_QUERIES,
    ProviderError,
    QueryBudget,
    SearchProvider,
    SerperProvider,
    charged_search,
)
from app.external.queries import SearchQuery, model_queries, sold_query
from app.ingestion.catalog import Catalog, load_catalog
from app.market.cleaning import outlier_ids
from app.market.sold_sales import sync_sold_sales

log = get_logger(__name__)

QUERIES_PER_MODEL = 2  # Shopping + second-hand marketplaces (the sold query is optional)
ERROR_BACKOFF = timedelta(hours=6)  # doubled after every consecutive error, capped at refresh_days
MAX_ERRORS_IN_A_ROW = 3  # a run stops when this many models fail one after the other
MAX_MODELS_PER_RUN = 500
NEW_CONDITIONS = ("new_with_tags", "new_without_tags")
SHOWN_REASONS = ("kids", "replica", "lot", "other_model")  # always present in the status


# ---------------------------------------------------------------------------- one model
@dataclass
class Candidate:
    """A result kept for the model, ready to store."""

    offer: Offer
    kind: str  # new | asking | sold
    condition: str
    price: Decimal
    currency: str
    price_eur: Decimal
    size: str | None
    score: float
    match: dict[str, Any]

    @property
    def dedupe_key(self) -> str:
        return hashlib.sha256(f"{self.kind}|{self.offer.url}|{self.price:.2f}".encode()).hexdigest()[:40]


@dataclass
class ModelSearch:
    """Outcome of the search of one model (nothing stored yet)."""

    spec: ModelSpec
    queries: list[SearchQuery] = field(default_factory=list)
    results: int = 0
    kept: list[Candidate] = field(default_factory=list)
    rejected: list[tuple[Offer, str, str | None]] = field(default_factory=list)
    duplicates: int = 0
    error: ProviderError | None = None
    skipped_budget: bool = False

    @property
    def queries_used(self) -> int:
        return len(self.queries)

    def rejected_counts(self) -> dict[str, int]:
        return dict(Counter(reason for _, reason, _ in self.rejected))

    def kept_counts(self) -> dict[str, int]:
        counts = Counter(c.kind for c in self.kept)
        return {k: counts.get(k, 0) for k in ("new", "asking", "sold")}


def classify(offer: Offer) -> tuple[str, str]:
    """``(kind, condition)``: a sale stated by the page is "sold"; an offer on a second-hand
    marketplace or a resale platform is "asking"; a retailer's offer is "new" unless it says the
    item is used."""
    stated = offer.condition
    if offer.sold:
        return "sold", stated or "used"
    if offer.resale:
        return "asking", stated or "new_with_tags"
    if offer.second_hand:
        return "asking", stated or "used"
    if stated and stated not in NEW_CONDITIONS:
        return "asking", stated
    return "new", stated or "new_with_tags"


def evaluate(spec: ModelSpec, offers: list[Offer], search: ModelSearch) -> None:
    """Match every offer to the model; kept ones become candidates, the others are counted."""
    seen = {c.dedupe_key for c in search.kept}
    for offer in offers:
        search.results += 1
        if offer.excluded:
            search.rejected.append((offer, "source", offer.source))
            continue
        if offer.endpoint == "search" and not offer.is_item:
            search.rejected.append((offer, "not_item", None))
            continue
        m = match_offer(spec, offer.title, offer.snippet)
        if not m.ok:
            search.rejected.append((offer, m.reason or "model", m.detail))
            continue
        if offer.price is None:
            search.rejected.append((offer, "no_price", offer.price_text))
            continue
        converted = to_eur(offer.price, offer.currency)
        if converted is None:
            search.rejected.append((offer, "currency", offer.price_text))
            continue
        if not MIN_PRICE_EUR <= converted.price_eur <= MAX_PRICE_EUR:
            search.rejected.append((offer, "price", offer.price_text))
            continue
        kind, condition = classify(offer)
        candidate = Candidate(
            offer=offer,
            kind=kind,
            condition=condition,
            price=offer.price.quantize(Decimal("0.01")),
            currency=converted.currency,
            price_eur=converted.price_eur,
            size=extract_size(offer.text, spec.category_slug),
            score=m.score,
            match={
                **m.info,
                "score": m.score,
                "endpoint": offer.endpoint,
                "purpose": offer.purpose,
                "position": offer.position,
                "price_from": offer.price_from,
                "price_text": offer.price_text,
                "sold_marker": offer.sold_marker,
                "condition_stated": offer.condition,
                "fx": converted.record(),
                **({"shop": offer.extra["shop"]} if offer.extra.get("shop") else {}),
            },
        )
        if candidate.dedupe_key in seen:
            search.duplicates += 1
            continue
        seen.add(candidate.dedupe_key)
        search.kept.append(candidate)


def _parse(response_payload: dict[str, Any], query: SearchQuery, now: datetime) -> list[Offer]:
    if query.endpoint == "shopping":
        return parse_shopping(response_payload, query.q, query.purpose)
    return parse_organic(response_payload, query.q, query.purpose, now)


async def search_model(
    provider: SearchProvider,
    budget: QueryBudget,
    spec: ModelSpec,
    *,
    now: datetime,
    with_sold_query: bool = True,
) -> ModelSearch:
    """Run the queries of one model within ``budget`` (charged before each call); nothing stored.
    A provider error ends the search of this model (``ModelSearch.error``)."""
    search = ModelSearch(spec)
    if not budget.allows(QUERIES_PER_MODEL):
        search.skipped_budget = True
        return search
    plan = model_queries(spec.brand_name, spec.model_name)
    for index in range(3):
        if index == 2:
            # The concluded-sales query: only when the model was found and the budget allows.
            if not (with_sold_query and search.kept and budget.allows(1)):
                break
            plan.append(sold_query(spec.brand_name, spec.model_name))
        query = plan[index]
        if not budget.reserve(1):
            break
        search.queries.append(query)
        try:
            response = await charged_search(provider, budget, query.endpoint, query.q)
        except ProviderError as exc:
            search.error = exc
            log.warning("external.search_failed", model=spec.model_name, status=exc.status, fatal=exc.fatal)
            break
        evaluate(spec, _parse(response.payload, query, now), search)
    return search


# ---------------------------------------------------------------------------- storing
def _row(
    c: Candidate, key: str, brand_id: int | None, category_id: int | None, model: str, now: datetime
) -> dict:
    o = c.offer
    return {
        "dedupe_key": c.dedupe_key,
        "model_key": key,
        "brand_id": brand_id,
        "category_id": category_id,
        "model_name": model[:120],
        "kind": c.kind,
        "price": c.price,
        "currency": c.currency,
        "price_eur": c.price_eur,
        "condition": c.condition[:24],
        "size": c.size,
        "source": o.source[:80],
        "source_url": o.url,
        "title": o.title[:300],
        "snippet": o.snippet,
        "provider": "serper",
        "query": o.query[:300],
        "observed_at": now,
        "source_date": o.source_date,
        "match_score": Decimal(str(c.score)),
        "match": c.match,
        "is_outlier": False,
    }


async def store_prices(
    session: AsyncSession,
    search: ModelSearch,
    *,
    key: str,
    brand_id: int | None,
    category_id: int | None,
    model_name: str,
    now: datetime,
) -> dict[str, int]:
    """Insert the kept prices (a price already known for the same page and kind is left as first
    observed); returns the new rows per kind."""
    rows = [_row(c, key, brand_id, category_id, model_name, now) for c in search.kept]
    stored = {"new": 0, "asking": 0, "sold": 0}
    if not rows:
        return stored
    stmt = (
        pg_insert(ExternalPrice.__table__)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(ExternalPrice.__table__.c.kind)
    )
    for (kind,) in (await session.execute(stmt)).all():
        stored[kind] += 1
    return stored


async def flag_model_outliers(session: AsyncSession, key: str) -> int:
    """Implausible prices of one model (log-space outliers per kind); returns the rows flagged."""
    table = ExternalPrice.__table__
    rows = (
        await session.execute(
            select(table.c.id, table.c.kind, table.c.price_eur, table.c.condition).where(
                table.c.model_key == key
            )
        )
    ).all()
    flagged = outlier_ids((r.id, (key, r.kind), r.price_eur, r.condition) for r in rows)
    ids = [r.id for r in rows]
    if ids:
        await session.execute(
            update(table)
            .where(table.c.id.in_(ids))
            .values(is_outlier=table.c.id.in_(sorted(flagged) or [-1]))
        )
    return len(flagged)


def spec_for(row: Any, catalog: Catalog) -> ModelSpec | None:
    slug = catalog.brand_slug(row.brand_id) or row.model_key.split("|", 1)[0]
    return model_spec(catalog.taxonomy, slug, row.model_name, catalog.category_slug(row.category_id))


def _backoff(errors: int, refresh_days: int) -> timedelta:
    return min(ERROR_BACKOFF * 2 ** max(errors - 1, 0), timedelta(days=refresh_days))


async def _record(
    session: AsyncSession,
    row: Any,
    search: ModelSearch | None,
    *,
    status: str,
    now: datetime,
    settings: Settings,
    stored: dict[str, int] | None = None,
    outliers: int = 0,
    error: str | None = None,
) -> None:
    """Update the cache row of one searched model."""
    previous = row.results or {}
    errors = int(previous.get("errors", 0)) + 1 if status == "error" else 0
    values: dict[str, Any] = {"status": status, "updated_at": now}
    if status == "skipped_budget":
        await session.execute(
            update(ExternalSearch).where(ExternalSearch.model_key == row.model_key).values(**values)
        )
        return
    rejected = search.rejected_counts() if search else {}
    if outliers:
        rejected["outlier"] = outliers
    values.update(
        last_searched_at=now,
        next_refresh_at=now
        + (
            _backoff(errors, settings.external_refresh_days)
            if status == "error"
            else timedelta(days=settings.external_refresh_days)
        ),
        queries_used=ExternalSearch.queries_used + (search.queries_used if search else 0),
        error=(error or "")[:300] or None,
        results={
            "at": now.isoformat(),
            "queries": search.queries_used if search else 0,
            "results": search.results if search else 0,
            "kept": search.kept_counts() if search else {"new": 0, "asking": 0, "sold": 0},
            "stored": stored or {"new": 0, "asking": 0, "sold": 0},
            "duplicates": search.duplicates if search else 0,
            "rejected": rejected,
            "errors": errors,
        },
    )
    if status != "error":
        values["demand"] = 0  # demand counts the analyses since the last search
    await session.execute(
        update(ExternalSearch).where(ExternalSearch.model_key == row.model_key).values(**values)
    )


def disabled_reason(settings: Settings) -> str | None:
    """Why the external search cannot run (``None`` when it can)."""
    if settings.external_search_provider == "none":
        return "Ricerca esterna disattivata (EXTERNAL_SEARCH_PROVIDER=none)"
    if not settings.external_search_enabled:
        return "Chiave mancante: imposta SERPER_API_KEY"
    return None


async def refresh_due_models(
    session: AsyncSession,
    now: datetime | None = None,
    max_models: int | None = None,
    *,
    provider: SearchProvider | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Search the models due for a refresh (highest demand first) within the query budget."""
    start = time.perf_counter()
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    budget = await QueryBudget.load(session, settings, now)
    result: dict[str, Any] = {
        "status": "ok",
        "provider": settings.external_search_provider,
        "models_searched": 0,
        "models_ok": 0,
        "models_no_results": 0,
        "models_error": 0,
        "models_skipped_budget": 0,
        "queries_used": 0,
        "prices_stored": {"new": 0, "asking": 0, "sold": 0},
        "rejected": {},
        "sold_synced": 0,
        "last_error": None,
    }
    if provider is None:
        reason = disabled_reason(settings)
        provider = None if reason else SerperProvider.from_settings(settings)
        if provider is None:
            return {**result, "status": "disabled", "reason": reason or "Provider non disponibile"}

    catalog = await load_catalog(session)
    limit = min(max_models or MAX_MODELS_PER_RUN, MAX_MODELS_PER_RUN)
    due = (
        await session.execute(
            select(ExternalSearch.__table__)
            .where(
                ExternalSearch.demand > 0,
                or_(ExternalSearch.next_refresh_at.is_(None), ExternalSearch.next_refresh_at <= now),
            )
            .order_by(ExternalSearch.demand.desc(), ExternalSearch.created_at, ExternalSearch.model_key)
            .limit(limit)
        )
    ).all()
    if not due:
        result["status"] = "nothing_due"
    rejected: Counter[str] = Counter()
    errors_in_a_row = 0
    for row in due:
        if not budget.allows(QUERIES_PER_MODEL):
            await _record(session, row, None, status="skipped_budget", now=now, settings=settings)
            result["models_skipped_budget"] += 1
            result["status"] = "budget_exhausted"
            break
        spec = spec_for(row, catalog)
        if spec is None:
            await _record(
                session, row, None, status="error", now=now, settings=settings, error="Marca sconosciuta"
            )
            result["models_error"] += 1
            continue
        used_before = budget.today_used
        search = await search_model(
            provider, budget, spec, now=now, with_sold_query=settings.external_search_sold_query
        )
        result["queries_used"] += budget.today_used - used_before
        if search.error is not None and search.error.fatal:
            # The key, the credits or the rate limit: not the model's fault, it stays due.
            result["status"] = "stopped"
            result["last_error"] = search.error.message
            await budget.save(session)
            await session.commit()
            break
        stored = await store_prices(
            session,
            search,
            key=row.model_key,
            brand_id=row.brand_id,
            category_id=row.category_id,
            model_name=row.model_name,
            now=now,
        )
        outliers = await flag_model_outliers(session, row.model_key) if search.kept else 0
        if search.error is not None:
            status = "error"
            result["last_error"] = search.error.message
        else:
            status = "ok" if search.kept else "no_results"
        await _record(
            session,
            row,
            search,
            status=status,
            now=now,
            settings=settings,
            stored=stored,
            outliers=outliers,
            error=search.error.message if search.error else None,
        )
        result["models_searched"] += 1
        result[f"models_{status}"] += 1
        for kind, n in stored.items():
            result["prices_stored"][kind] += n
        rejected.update(search.rejected_counts())
        await budget.save(session)
        await session.commit()
        errors_in_a_row = errors_in_a_row + 1 if status == "error" else 0
        if errors_in_a_row >= MAX_ERRORS_IN_A_ROW:
            result["status"] = "stopped"
            break

    result["rejected"] = dict(rejected)
    if result["prices_stored"]["sold"]:
        synced = await sync_sold_sales(session)
        result["sold_synced"] = synced["external_sold"]
    result["today_used"] = budget.today_used
    result["month_used"] = budget.month_used
    result["duration_ms"] = round((time.perf_counter() - start) * 1000)
    await budget.save(
        session,
        last_run=now.isoformat(),
        last_error=result["last_error"],
        last_result={k: v for k, v in result.items() if k not in ("rejected",)},
    )
    await session.commit()
    log.info(
        "external.refreshed",
        **{k: v for k, v in result.items() if not isinstance(v, dict) and k != "last_error"},
        error=bool(result["last_error"]),
    )
    return result


async def refresh_model(
    session: AsyncSession,
    brand_id: int | None,
    category_id: int | None,
    model_name: str,
    brand_slug: str,
    *,
    provider: SearchProvider,
    settings: Settings | None = None,
    now: datetime | None = None,
    store: bool = True,
) -> tuple[ModelSearch | None, dict[str, Any]]:
    """Search one model on demand (CLI). ``store=False`` searches without storing any price (the
    queries are still counted in the budget). Returns the search and a summary."""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    catalog = await load_catalog(session)
    spec = model_spec(catalog.taxonomy, brand_slug, model_name, catalog.category_slug(category_id))
    if spec is None:
        return None, {"status": "error", "error": "Marca sconosciuta"}
    budget = await QueryBudget.load(session, settings, now)
    search = await search_model(
        provider, budget, spec, now=now, with_sold_query=settings.external_search_sold_query
    )
    await budget.save(session)
    summary: dict[str, Any] = {
        "status": "skipped_budget"
        if search.skipped_budget
        else "error"
        if search.error
        else ("ok" if search.kept else "no_results"),
        "error": search.error.message if search.error else None,
        "queries_used": search.queries_used,
        "today_used": budget.today_used,
        "month_used": budget.month_used,
        "kept": search.kept_counts(),
        "rejected": search.rejected_counts(),
        "stored": None,
    }
    if store and not search.skipped_budget and not (search.error and search.error.fatal):
        key = model_key(spec.brand_slug, spec.model_name)
        table = ExternalSearch.__table__
        insert = pg_insert(table).values(
            model_key=key,
            brand_id=brand_id,
            category_id=category_id,
            model_name=spec.model_name[:120],
            status="pending",
        )
        await session.execute(insert.on_conflict_do_nothing(index_elements=["model_key"]))
        row = (await session.execute(select(table).where(table.c.model_key == key))).one()
        stored = await store_prices(
            session,
            search,
            key=key,
            brand_id=brand_id,
            category_id=category_id,
            model_name=spec.model_name,
            now=now,
        )
        outliers = await flag_model_outliers(session, key) if search.kept else 0
        await _record(
            session,
            row,
            search,
            status=summary["status"],
            now=now,
            settings=settings,
            stored=stored,
            outliers=outliers,
            error=summary["error"],
        )
        summary["stored"] = stored
        if stored["sold"]:
            summary["sold_synced"] = (await sync_sold_sales(session))["external_sold"]
    await session.commit()
    return search, summary


# ---------------------------------------------------------------------------- status
async def external_status(session: AsyncSession) -> dict[str, Any]:
    """Provider, budget used, cache size and last run (shape in the design document)."""
    settings = get_settings()
    now = datetime.now(UTC)
    budget = await QueryBudget.load(session, settings, now)
    t = ExternalSearch.__table__
    searched = t.c.last_searched_at.is_not(None)
    month_ago = now - timedelta(days=30)
    counts = (
        await session.execute(
            select(
                func.count().filter(searched).label("cached"),
                func.count().filter(and_(searched, t.c.demand > 0, t.c.next_refresh_at <= now)).label("due"),
                func.count().filter(t.c.last_searched_at.is_(None)).label("pending"),
                func.count().filter(t.c.updated_at >= month_ago).label("seen_30d"),
                func.avg(cast(t.c.results["queries"].astext, Integer))
                .filter(and_(searched, t.c.status.in_(("ok", "no_results"))))
                .label("avg_queries"),
            )
        )
    ).one()
    prices = {"new": 0, "asking": 0, "sold": 0}
    for kind, n in (
        await session.execute(
            select(ExternalPrice.kind, func.count())
            .where(ExternalPrice.is_outlier.is_(False))
            .group_by(ExternalPrice.kind)
        )
    ).all():
        prices[kind] = int(n)
    each = func.jsonb_each_text(t.c.results["rejected"]).table_valued("key", "value").alias("r")
    rejected_rows = (
        await session.execute(
            select(each.c.key, func.sum(cast(each.c.value, Integer)))
            .select_from(t)
            .join(each, true())
            .group_by(each.c.key)
        )
    ).all()
    rejected = {r: 0 for r in SHOWN_REASONS}
    rejected.update({str(k): int(v) for k, v in rejected_rows if k in REASONS and v})
    per_model = float(counts.avg_queries or QUERIES_PER_MODEL)
    expected = math.ceil(int(counts.seen_30d or 0) * per_model * 30 / settings.external_refresh_days)
    return {
        "provider": "serper" if settings.external_search_provider == "serper" else "none",
        "enabled": settings.external_search_enabled,
        "key_configured": bool(
            settings.serper_api_key and settings.serper_api_key.get_secret_value().strip()
        ),
        "cost_per_query_usd": COST_PER_QUERY_USD,
        "free_queries": FREE_QUERIES,
        "month_used": budget.month_used,
        "month_budget": settings.external_search_monthly_budget,
        "today_used": budget.today_used,
        "daily_max": settings.external_search_daily_max,
        "refresh_days": settings.external_refresh_days,
        "models_cached": int(counts.cached or 0),
        "models_due": int(counts.due or 0),
        "models_pending": int(counts.pending or 0),
        "prices": prices,
        "rejected": rejected,
        "last_run": budget.state.get("last_run"),
        "last_error": budget.state.get("last_error"),
        "expected_monthly_queries": expected,
    }
