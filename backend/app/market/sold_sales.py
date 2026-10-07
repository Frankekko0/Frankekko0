"""Concluded sales (``sold_sales``): the most reliable price evidence, from every source.

Sources, most reliable first (``reliability``):

1. ``own_sale`` - the user's resales recorded in tracking: the price really received;
2. ``own_purchase`` - the user's purchases: the price really paid;
3. ``vinted_sold`` - listings seen turning to "sold" (captures, periodic checks, emails): the last
   price seen while on sale ("ultimo prezzo rilevato", not necessarily the price paid) and the
   days from publication to the sale;
4. ``external_sold`` - sales published by other marketplaces, found by the external search
   (``external_prices`` with kind "sold", not outliers): the price reported, dated by the source
   when it says so, else by the search.

Every row has a ``dedupe_key`` (``sale:<id>``, ``purchase:<id>``, ``vinted:<external_id>``,
``ext:<external_price_id>``), so syncing is idempotent. A listing the user bought is recorded once,
as the purchase; the same external page is recorded once. Rows whose source no longer qualifies
(a sale deleted, a listing seen on sale again, an external price marked as outlier) are removed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    and_,
    case,
    cast,
    delete,
    exists,
    func,
    literal,
    literal_column,
    not_,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExternalPrice, InventoryItem, Listing, Purchase, Sale, SoldSale
from app.domain.enums import Condition, ListingStatus
from app.identification.engine import IdentificationEngine, ListingText
from app.identification.taxonomy import Taxonomy, fold
from app.ingestion.catalog import Catalog, load_catalog
from app.ingestion.normalizer import normalize_condition, normalize_size
from app.market.state import get_state, parse_time, set_state

RELIABILITY = {"own_sale": 1, "own_purchase": 2, "vinted_sold": 3, "external_sold": 4}
PRICE_KIND = {
    "own_sale": "received",
    "own_purchase": "paid",
    "vinted_sold": "last_seen",
    "external_sold": "reported",
}
SOURCES = tuple(RELIABILITY)
SYNC_KEY = "sold_sales_sync"
# Incremental syncs re-read what changed since the last one, with an overlap for clock skew and
# transactions still open at the time of the previous sync.
INCREMENTAL_OVERLAP = timedelta(minutes=15)
# Columns refreshed when a source row changes (``is_outlier`` belongs to the cleaning step).
_UPDATABLE = (
    "listing_id",
    "purchase_id",
    "sale_id",
    "external_price_id",
    "user_id",
    "title",
    "brand_id",
    "category_id",
    "model_name",
    "size_normalized",
    "condition",
    "price",
    "currency",
    "price_eur",
    "sold_at",
    "published_at",
    "days_to_sell",
    "source_name",
    "source_url",
)

_engines: dict[int, IdentificationEngine] = {}


def _engine(taxonomy: Taxonomy) -> IdentificationEngine:
    if id(taxonomy) not in _engines:
        _engines.clear()
        _engines[id(taxonomy)] = IdentificationEngine(taxonomy)
    return _engines[id(taxonomy)]


def _noon(d: date) -> datetime:
    """A calendar date as a moment (midday UTC: never shifts to another day in Europe)."""
    return datetime.combine(d, time(12, 0), tzinfo=UTC)


def _upsert(rows_stmt: Any) -> Any:
    """ON CONFLICT (dedupe_key): update only when something changed; RETURNING inserted?"""
    stmt = rows_stmt
    changed = or_(*(getattr(SoldSale, c).is_distinct_from(getattr(stmt.excluded, c)) for c in _UPDATABLE))
    return stmt.on_conflict_do_update(
        index_elements=["dedupe_key"],
        set_={**{c: getattr(stmt.excluded, c) for c in _UPDATABLE}, "updated_at": func.now()},
        where=changed,
    ).returning(literal_column("(xmax = 0)").label("inserted"))


# ---------------------------------------------------------------------------- own records
def own_rows(purchases: Sequence[Any], catalog: Catalog) -> list[dict[str, Any]]:
    """``sold_sales`` rows of the user's purchases and resales (pure).

    ``purchases`` rows carry the purchase, its sale and listing columns (see ``_own_query``).
    The model comes from the listing the item was bought from, else from the title (same
    identification engine as the listings), so own records match listings of the same model.
    """
    engine = _engine(catalog.taxonomy)
    out: list[dict[str, Any]] = []
    for r in purchases:
        brand_id, category_id, model = r.l_brand_id or r.brand_id, r.l_category_id or r.category_id, r.l_model
        size, condition = r.l_size, r.l_condition
        if r.l_id is None or model is None or brand_id is None:
            ident = engine.identify(
                ListingText(
                    title=r.title or "",
                    brand_field=r.brand_name,
                    category_field=r.category_name,
                    size_field=r.size,
                    condition=r.condition,
                )
            )
            brand_id = brand_id or catalog.brand_id(ident.brand.value)
            category_id = category_id or catalog.category_id(ident.category.value)
            model = model or ident.model.value
            if size is None:
                size = ident.size.value or normalize_size(
                    r.size, catalog.category_slug(category_id), ident.gender.value
                )
        if r.condition:
            declared = normalize_condition(r.condition)
            if declared != Condition.UNKNOWN:
                condition = declared.value
        base = {
            "user_id": r.user_id,
            "title": (r.title or "")[:300] or "Acquisto",
            "brand_id": brand_id,
            "category_id": category_id,
            "model_name": model[:120] if model else None,
            "size_normalized": (size or None) and size[:20],
            "condition": condition or Condition.UNKNOWN.value,
            "currency": "EUR",
            "source_name": "tracking",
            "external_price_id": None,
        }
        if r.purchase_price and r.purchase_price > 0:
            bought_at = _noon(r.purchase_date)
            published = r.l_published_at
            out.append(
                {
                    **base,
                    "dedupe_key": f"purchase:{r.id}",
                    "source": "own_purchase",
                    "reliability": RELIABILITY["own_purchase"],
                    "price_kind": PRICE_KIND["own_purchase"],
                    "listing_id": r.l_id,
                    "purchase_id": r.id,
                    "sale_id": None,
                    "price": r.purchase_price,
                    "price_eur": r.purchase_price,
                    "sold_at": bought_at,
                    "published_at": published,
                    "days_to_sell": Decimal(
                        str(round(max(0.0, (bought_at - published).total_seconds() / 86400), 1))
                    )
                    if published is not None and published <= bought_at
                    else None,
                    "source_url": r.l_url,
                }
            )
        if r.sale_id is not None and r.sale_price and r.sale_price > 0:
            sold_at = _noon(r.sale_date)
            listed = r.listed_at
            days = (r.sale_date - listed.date()).days if listed is not None else None
            out.append(
                {
                    **base,
                    "dedupe_key": f"sale:{r.sale_id}",
                    "source": "own_sale",
                    "reliability": RELIABILITY["own_sale"],
                    "price_kind": PRICE_KIND["own_sale"],
                    # The resale is another transaction than the listing the item was bought from.
                    "listing_id": None,
                    "purchase_id": r.id,
                    "sale_id": r.sale_id,
                    "price": r.sale_price,
                    "price_eur": r.sale_price,
                    "sold_at": sold_at,
                    "published_at": listed,
                    "days_to_sell": Decimal(days) if days is not None and days >= 0 else None,
                    "source_url": None,
                }
            )
    return out


def _own_query() -> Select[Any]:
    return (
        select(
            Purchase.id,
            Purchase.user_id,
            Purchase.title,
            Purchase.brand_id,
            Purchase.brand_name,
            Purchase.category_id,
            Purchase.category_name,
            Purchase.size,
            Purchase.condition,
            Purchase.purchase_price,
            Purchase.purchase_date,
            Sale.id.label("sale_id"),
            Sale.sale_price,
            Sale.sale_date,
            InventoryItem.listed_at,
            Listing.id.label("l_id"),
            Listing.brand_id.label("l_brand_id"),
            Listing.category_id.label("l_category_id"),
            Listing.model_name.label("l_model"),
            Listing.size_normalized.label("l_size"),
            Listing.condition.label("l_condition"),
            Listing.published_at.label("l_published_at"),
            Listing.url.label("l_url"),
        )
        .outerjoin(Sale, Sale.purchase_id == Purchase.id)
        .outerjoin(InventoryItem, InventoryItem.purchase_id == Purchase.id)
        .outerjoin(Listing, Listing.id == Purchase.listing_id)
        .order_by(Purchase.id)
    )


async def sync_own_records(
    session: AsyncSession, purchase_ids: Sequence[uuid.UUID] | None = None
) -> dict[str, int]:
    """Upsert the user's purchases and resales (all of them, or only ``purchase_ids``, e.g. right
    after one is recorded or edited); rows of deleted or zero-priced records are removed."""
    catalog = await load_catalog(session)
    stmt = _own_query()
    if purchase_ids is not None:
        if not purchase_ids:
            return {"own_sale": 0, "own_purchase": 0, "removed": 0}
        stmt = stmt.where(Purchase.id.in_(list(purchase_ids)))
    rows = own_rows((await session.execute(stmt)).all(), catalog)
    counts = {"own_sale": 0, "own_purchase": 0}
    for source in counts:
        subset = [r for r in rows if r["source"] == source]
        if subset:
            # RETURNING yields one row per row actually written (unchanged rows are skipped).
            res = await session.execute(_upsert(pg_insert(SoldSale.__table__)), subset)
            counts[source] = len(res.all())
    keys = [r["dedupe_key"] for r in rows]
    scope = SoldSale.source.in_(("own_sale", "own_purchase"))
    if purchase_ids is not None:
        scope = and_(scope, SoldSale.purchase_id.in_(list(purchase_ids)))
    removed = (
        await session.execute(
            delete(SoldSale).where(scope, SoldSale.dedupe_key.not_in(keys) if keys else true())
        )
    ).rowcount
    return {**counts, "removed": int(removed or 0)}


# ---------------------------------------------------------------------------- Vinted sales
def _vinted_select(where: ColumnElement[bool] | None) -> Select[Any]:
    price = func.coalesce(Listing.last_active_price, Listing.price)
    bought = exists().where(Purchase.listing_id == Listing.id)
    stmt = select(
        case(
            (Listing.provider == "vinted", literal("vinted:") + Listing.external_id),
            else_=Listing.provider + literal(":") + Listing.external_id,
        ).label("dedupe_key"),
        literal("vinted_sold").label("source"),
        literal(RELIABILITY["vinted_sold"]).label("reliability"),
        literal(PRICE_KIND["vinted_sold"]).label("price_kind"),
        Listing.id.label("listing_id"),
        func.left(Listing.title, 300).label("title"),
        Listing.brand_id,
        Listing.category_id,
        Listing.model_name,
        Listing.size_normalized,
        Listing.condition,
        price.label("price"),
        Listing.currency,
        price.label("price_eur"),
        func.coalesce(
            Listing.sold_at, Listing.sold_detected_at, Listing.status_changed_at, Listing.last_seen_at
        ).label("sold_at"),
        Listing.published_at,
        Listing.days_to_sell,
        Listing.provider.label("source_name"),
        Listing.url.label("source_url"),
    ).where(
        Listing.status == ListingStatus.SOLD.value,
        Listing.duplicate_of_id.is_(None),
        # Prices in other currencies are not converted here: EUR listings only.
        Listing.currency == "EUR",
        price > 0,
        not_(bought),
    )
    return stmt.where(where) if where is not None else stmt


_VINTED_COLS = (
    "dedupe_key",
    "source",
    "reliability",
    "price_kind",
    "listing_id",
    "title",
    "brand_id",
    "category_id",
    "model_name",
    "size_normalized",
    "condition",
    "price",
    "currency",
    "price_eur",
    "sold_at",
    "published_at",
    "days_to_sell",
    "source_name",
    "source_url",
)


async def _sync_vinted(session: AsyncSession, where: ColumnElement[bool] | None) -> int:
    stmt = _upsert(pg_insert(SoldSale.__table__).from_select(_VINTED_COLS, _vinted_select(where)))
    return len((await session.execute(stmt)).all())


def _vinted_stale(listing_ids: Sequence[uuid.UUID] | None = None) -> Any:
    """Vinted rows whose listing is no longer a qualifying sale (seen on sale again, a repost,
    deleted) or was bought by the user (recorded once, as the purchase)."""
    still_sold = exists().where(
        Listing.id == SoldSale.listing_id,
        Listing.status == ListingStatus.SOLD.value,
        Listing.duplicate_of_id.is_(None),
    )
    bought = exists().where(Purchase.listing_id == SoldSale.listing_id)
    cond = and_(
        SoldSale.source == "vinted_sold", or_(SoldSale.listing_id.is_(None), not_(still_sold), bought)
    )
    if listing_ids is not None:
        cond = and_(cond, SoldSale.listing_id.in_(list(listing_ids)))
    return delete(SoldSale).where(cond)


async def record_vinted_sold(session: AsyncSession, listing_ids: list[uuid.UUID]) -> int:
    """Record listings just seen turning to "sold" (called right after a capture, a periodic check
    or an email). Listings of the list that are no longer sold lose their row. Returns rows
    written."""
    ids = list(dict.fromkeys(listing_ids))
    if not ids:
        return 0
    await session.execute(_vinted_stale(ids))
    return await _sync_vinted(session, Listing.id.in_(ids))


# ---------------------------------------------------------------------------- external sales
def _external_chosen() -> Any:
    """One external sale per page (the newest observation): ids of the rows to record."""
    when = func.coalesce(ExternalPrice.source_date, ExternalPrice.observed_at)
    return (
        select(ExternalPrice.id)
        .where(ExternalPrice.kind == "sold", ExternalPrice.is_outlier.is_(False))
        .distinct(ExternalPrice.source_url)
        .order_by(ExternalPrice.source_url, when.desc(), ExternalPrice.id)
        .subquery()
    )


async def _sync_external(session: AsyncSession) -> tuple[int, int]:
    chosen = _external_chosen()
    when = func.coalesce(ExternalPrice.source_date, ExternalPrice.observed_at)
    src = select(
        (literal("ext:") + cast(ExternalPrice.id, String)).label("dedupe_key"),
        literal("external_sold").label("source"),
        literal(RELIABILITY["external_sold"]).label("reliability"),
        literal(PRICE_KIND["external_sold"]).label("price_kind"),
        ExternalPrice.id.label("external_price_id"),
        func.left(ExternalPrice.title, 300).label("title"),
        ExternalPrice.brand_id,
        ExternalPrice.category_id,
        ExternalPrice.model_name,
        func.left(ExternalPrice.size, 20).label("size_normalized"),
        ExternalPrice.condition,
        ExternalPrice.price,
        ExternalPrice.currency,
        ExternalPrice.price_eur,
        when.label("sold_at"),
        ExternalPrice.source.label("source_name"),
        ExternalPrice.source_url,
    ).where(ExternalPrice.id.in_(select(chosen.c.id)), ExternalPrice.price_eur > 0)
    cols = (
        "dedupe_key",
        "source",
        "reliability",
        "price_kind",
        "external_price_id",
        "title",
        "brand_id",
        "category_id",
        "model_name",
        "size_normalized",
        "condition",
        "price",
        "currency",
        "price_eur",
        "sold_at",
        "source_name",
        "source_url",
    )
    written = len(
        (await session.execute(_upsert(pg_insert(SoldSale.__table__).from_select(cols, src)))).all()
    )
    removed = (
        await session.execute(
            delete(SoldSale).where(
                SoldSale.source == "external_sold",
                or_(
                    SoldSale.external_price_id.is_(None),
                    SoldSale.external_price_id.not_in(select(_external_chosen().c.id)),
                ),
            )
        )
    ).rowcount
    return written, int(removed or 0)


# ---------------------------------------------------------------------------- sync
async def sync_sold_sales(session: AsyncSession, *, full: bool = False) -> dict[str, int]:
    """Upsert concluded sales from every source; returns rows inserted/updated per source
    (``removed``: rows whose source no longer qualifies).

    Incremental by default: Vinted listings changed since the last sync; own records and
    external sales are small and always re-read in full. ``full`` re-reads every listing.
    """
    state = await get_state(session, SYNC_KEY) or {}
    now = datetime.now(UTC)
    last = parse_time(state.get("last_sync"))
    own = await sync_own_records(session)
    where = None
    if not full and last is not None:
        since = last - INCREMENTAL_OVERLAP
        where = or_(Listing.updated_at >= since, Listing.status_changed_at >= since)
    stale = (await session.execute(_vinted_stale())).rowcount
    vinted = await _sync_vinted(session, where)
    external, ext_removed = await _sync_external(session)
    result = {
        "own_sale": own["own_sale"],
        "own_purchase": own["own_purchase"],
        "vinted_sold": vinted,
        "external_sold": external,
        "removed": own["removed"] + int(stale or 0) + ext_removed,
    }
    await set_state(
        session,
        SYNC_KEY,
        {
            **state,
            "last_sync": now.isoformat(),
            "last_full": now.isoformat() if full or last is None else state.get("last_full"),
            "last_result": result,
        },
    )
    return result


async def sold_sales_summary(session: AsyncSession) -> dict[str, Any]:
    """{"total", "by_source": {...}, "models_with_5_sales", "models_total", "last_sync"} -
    concluded sales usable for estimates (outliers excluded, counted apart in ``outliers``)."""
    rows = (
        await session.execute(
            select(SoldSale.source, SoldSale.is_outlier, func.count()).group_by(
                SoldSale.source, SoldSale.is_outlier
            )
        )
    ).all()
    by_source = dict.fromkeys(SOURCES, 0)
    outliers = 0
    for source, is_outlier, n in rows:
        if is_outlier:
            outliers += n
        elif source in by_source:
            by_source[source] += n
    per_model = (
        select(func.count().label("n"))
        .where(
            SoldSale.is_outlier.is_(False), SoldSale.model_name.is_not(None), SoldSale.brand_id.is_not(None)
        )
        .group_by(SoldSale.brand_id, func.lower(SoldSale.model_name))
        .subquery()
    )
    models_5, models_total = (
        await session.execute(
            select(func.count().filter(per_model.c.n >= 5), func.count()).select_from(per_model)
        )
    ).one()
    state = await get_state(session, SYNC_KEY) or {}
    return {
        "total": sum(by_source.values()),
        "by_source": by_source,
        "models_with_5_sales": int(models_5 or 0),
        "models_total": int(models_total or 0),
        "last_sync": state.get("last_sync"),
        "outliers": outliers,
    }


def model_group(brand_id: int | None, model: str | None) -> tuple[int | None, str] | None:
    """Grouping key of a model across sources: brand + folded model name."""
    return (brand_id, fold(model)) if brand_id is not None and model else None
