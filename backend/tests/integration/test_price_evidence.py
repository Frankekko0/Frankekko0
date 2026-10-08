"""Price evidence: concluded sales from every source, negotiation discount, outliers, pre-computed
statistics (one query per page) and the provenance of an analysis, end to end."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal as D
from typing import Any

import pytest
from sqlalchemy import event, select

from app.core.security import hash_password
from app.db.models import (
    ExternalPrice,
    ExternalSearch,
    InventoryItem,
    Listing,
    ModelPriceStat,
    Opportunity,
    Purchase,
    Sale,
    SoldSale,
    User,
)
from app.domain.enums import AcquisitionMode, ListingStatus, StatusEvidence
from app.external.keys import model_key
from app.ingestion.catalog import load_catalog
from app.ingestion.service import IngestionService
from app.market.cleaning import flag_external_outliers, flag_sold_outliers
from app.market.jobs import sync_price_evidence
from app.market.model_stats import StatQuery, lookup_stats, recompute_model_stats, stat_key
from app.market.negotiation import compute_negotiation_discount, current_negotiation_discount
from app.market.sold_sales import record_vinted_sold, sold_sales_summary, sync_sold_sales
from app.market.state import set_state
from app.marketplace.base import ProviderListing
from app.opportunities.pipeline import AnalysisPipeline
from app.pricing.evidence import GATE_KEY
from app.tracking.service import TrackingService
from app.tracking.status import Observation
from tests.conftest import NOW

pytestmark = pytest.mark.usefixtures("clean_db")

MODEL = "Custom Slim Fit"
PROVENANCE_KEYS = {
    "expected_price",
    "price_range",
    "days_to_sell",
    "sale_probability",
    "new_price",
    "real_sales",
    "external",
}


@contextmanager
def count_queries(session: Any) -> Iterator[list[str]]:
    statements: list[str] = []

    def before(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


async def market(session: Any, make_listing: Callable[..., ProviderListing], sold: int = 12, active: int = 8):
    """Ralph Lauren Custom Slim Fit polos: ``sold`` sold around 30 EUR, ``active`` on sale ~36."""
    items = [
        make_listing(
            price=26 + i % 8,
            status="sold",
            published_days_ago=10 + i % 15,
            sold_after_days=2 + i % 6,
            external_id=f"s{i}",
        )
        for i in range(sold)
    ]
    items += [
        make_listing(price=33 + i % 6, published_days_ago=1 + i, external_id=f"a{i}") for i in range(active)
    ]
    res = await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD).ingest(items, now=NOW)
    await session.commit()
    return res


async def user(session: Any) -> User:
    u = User(email="flip@example.com", password_hash=hash_password("x" * 12))
    session.add(u)
    await session.flush()
    return u


async def ralph(session: Any) -> int:
    catalog = await load_catalog(session)
    brand_id = catalog.brand_id("ralph-lauren")
    assert brand_id is not None
    return brand_id


def ext_row(
    brand_id: int,
    kind: str,
    price: float,
    url: str,
    *,
    model: str = MODEL,
    days_ago: float = 5,
    source: str = "ebay.it",
    source_date: bool = True,
    outlier: bool = False,
    condition: str = "used",
) -> ExternalPrice:
    when = NOW - timedelta(days=days_ago)
    return ExternalPrice(
        dedupe_key=hashlib.sha256(f"{kind}|{url}|{price}".encode()).hexdigest()[:40],
        model_key=model_key("ralph-lauren", model),
        brand_id=brand_id,
        model_name=model,
        kind=kind,
        price=D(str(price)),
        currency="EUR",
        price_eur=D(str(price)),
        condition=condition,
        source=source,
        source_url=url,
        title=f"Polo Ralph Lauren {model} uomo",
        provider="serper",
        query=f"ralph lauren {model}",
        observed_at=when,
        source_date=when if source_date else None,
        match_score=D("0.9"),
        is_outlier=outlier,
    )


async def purchase(
    session: Any,
    u: User,
    price: float,
    *,
    listing_id: Any = None,
    title: str = "Polo Ralph Lauren Custom Slim Fit bianca M",
    day: date | None = None,
    brand_id: int | None = None,
) -> Purchase:
    p = Purchase(
        user_id=u.id,
        listing_id=listing_id,
        title=title,
        brand_id=brand_id,
        brand_name="Ralph Lauren",
        size="M",
        condition="Ottime condizioni",
        purchase_price=D(str(price)),
        total_cost=D(str(price + 4)),
        purchase_date=day or (NOW - timedelta(days=20)).date(),
    )
    session.add(p)
    await session.flush()
    return p


# ---------------------------------------------------------------------------- concluded sales
async def test_sold_listings_are_recorded_when_captured(session, make_listing) -> None:
    await market(session, make_listing, sold=5, active=2)
    rows = (await session.execute(select(SoldSale))).scalars().all()
    assert len(rows) == 5  # recorded by the capture itself, no sync needed
    r = next(x for x in rows if x.dedupe_key == "vinted:s0")
    assert r.source == "vinted_sold" and r.reliability == 3 and r.price_kind == "last_seen"
    assert r.model_name == MODEL and r.price == r.price_eur == D("26.00")
    assert r.days_to_sell == D("2.0") and r.source_name == "vinted" and r.source_url.endswith("s0-test")

    # An active listing seen sold later (another capture) is recorded right away ...
    pl = make_listing(price=31, published_days_ago=3, external_id="a0")
    sold = ProviderListing.model_validate({**pl.model_dump(), "status": "sold"})
    await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest(
        [sold], now=NOW + timedelta(hours=6)
    )
    assert (
        await session.execute(select(SoldSale).where(SoldSale.dedupe_key == "vinted:a0"))
    ).scalar_one().price == D("33.00")  # the last price seen on sale

    # ... and a sold listing seen on sale again (cancelled sale) loses its row.
    lid = (await session.execute(select(Listing.id).where(Listing.external_id == "s1"))).scalar_one()
    await TrackingService(session).observe(
        {
            lid: Observation(
                observed_at=NOW + timedelta(hours=7),
                evidence=StatusEvidence.PAGE,
                status=ListingStatus.ACTIVE,
                price=D("27"),
            )
        },
        AcquisitionMode.EXTENSION_REFRESH,
    )
    keys = set((await session.execute(select(SoldSale.dedupe_key))).scalars())
    assert "vinted:s1" not in keys and "vinted:a0" in keys


async def test_sync_from_every_source_with_dedupe(session, make_listing) -> None:
    await market(session, make_listing, sold=6, active=2)
    brand_id = await ralph(session)
    u = await user(session)
    bought = (await session.execute(select(Listing).where(Listing.external_id == "s2"))).scalar_one()
    p1 = await purchase(session, u, 24, listing_id=bought.id, brand_id=brand_id)  # bought on Vinted
    p2 = await purchase(session, u, 18, title="Polo Ralph Lauren Custom Slim Fit rossa L")  # typed in
    session.add(InventoryItem(user_id=u.id, purchase_id=p1.id, listed_at=NOW - timedelta(days=12)))
    session.add(
        Sale(
            user_id=u.id,
            purchase_id=p1.id,
            sale_price=D("39"),
            net_revenue=D("39"),
            profit=D("11"),
            roi=D("0.4"),
            holding_days=18,
            sale_date=(NOW - timedelta(days=2)).date(),
        )
    )
    session.add_all(
        [
            ext_row(brand_id, "sold", 31, "https://www.ebay.it/itm/1", days_ago=9),
            ext_row(brand_id, "sold", 30, "https://www.ebay.it/itm/1", days_ago=20),  # same page, older
            ext_row(brand_id, "sold", 29, "https://www.ebay.it/itm/2", source_date=False),
            ext_row(brand_id, "sold", 300, "https://www.ebay.it/itm/3", outlier=True),
            ext_row(brand_id, "asking", 45, "https://www.vestiairecollective.com/x"),
            ext_row(brand_id, "new", 99, "https://www.zalando.it/p", condition="new"),
        ]
    )
    await session.flush()

    before = (await sold_sales_summary(session))["total"]
    counts = await sync_sold_sales(session, full=True)
    assert counts["own_sale"] == 1 and counts["own_purchase"] == 2 and counts["external_sold"] == 2
    assert counts["removed"] == 1  # the Vinted row of the listing the user bought

    rows = {r.dedupe_key: r for r in (await session.execute(select(SoldSale))).scalars()}
    assert f"vinted:{bought.external_id}" not in rows  # own purchase wins over the Vinted sale
    own_p = rows[f"purchase:{p1.id}"]
    assert own_p.source == "own_purchase" and own_p.reliability == 2 and own_p.price_kind == "paid"
    assert own_p.listing_id == bought.id and own_p.price == D("24.00") and own_p.model_name == MODEL
    typed = rows[f"purchase:{p2.id}"]
    assert typed.model_name == MODEL and typed.brand_id == brand_id  # identified from the title
    assert typed.size_normalized == "M" and typed.condition == "very_good"
    sale = next(r for r in rows.values() if r.source == "own_sale")
    assert sale.reliability == 1 and sale.price_kind == "received" and sale.price == D("39.00")
    assert sale.days_to_sell == D("10") and sale.listing_id is None
    ext = [r for r in rows.values() if r.source == "external_sold"]
    assert sorted(float(r.price) for r in ext) == [29.0, 31.0]  # one per page, outlier excluded
    undated = next(r for r in ext if r.price == D("29"))
    assert undated.sold_at == NOW - timedelta(days=5)  # no source date: when the search found it
    assert all(r.price_kind == "reported" and r.reliability == 4 for r in ext)

    after = await sold_sales_summary(session)
    assert before == 6 and after["total"] == 6 - 1 + 2 + 1 + 2
    assert after["by_source"] == {"own_sale": 1, "own_purchase": 2, "vinted_sold": 5, "external_sold": 2}
    assert after["models_with_5_sales"] == 1 and after["models_total"] == 1

    # Idempotent: nothing changes, nothing is written.
    again = await sync_sold_sales(session)
    assert again == {"own_sale": 0, "own_purchase": 0, "vinted_sold": 0, "external_sold": 0, "removed": 0}

    # A deleted sale disappears with it (cascade); a record_vinted_sold on a bought listing never
    # re-adds the Vinted row; a purchase whose price becomes 0 loses its row at the next sync.
    await session.delete(await session.get(Sale, sale.sale_id))
    p2.purchase_price = D("0")
    await session.flush()
    assert await record_vinted_sold(session, [bought.id]) == 0
    assert (await sold_sales_summary(session))["by_source"]["own_sale"] == 0
    assert (await sync_sold_sales(session))["removed"] == 1
    assert (await sold_sales_summary(session))["by_source"]["own_purchase"] == 1


async def test_negotiation_discount_needs_three_purchases(session, make_listing) -> None:
    await market(session, make_listing, sold=6, active=0)
    u = await user(session)
    listings = (await session.execute(select(Listing).order_by(Listing.external_id))).scalars().all()
    assert (await current_negotiation_discount(session))["discount"] is None
    # Asked 26 / 27 / 28 (price history on the purchase day), paid 10% / 5% / 20% less.
    for listing, paid in zip(listings[:2], (26 * 0.9, 27 * 0.95), strict=True):
        await purchase(session, u, round(paid, 2), listing_id=listing.id, day=NOW.date())
    two = await compute_negotiation_discount(session)
    assert two["discount"] is None and two["n"] == 2 and "almeno 3" in two["note"]
    await purchase(session, u, round(28 * 0.8, 2), listing_id=listings[2].id, day=NOW.date())
    three = await compute_negotiation_discount(session)
    assert three["n"] == 3 and three["discount"] == pytest.approx(0.10, abs=0.002)
    assert "10%" in three["note"]
    stored = await current_negotiation_discount(session)
    assert stored == three


async def test_outliers_are_flagged_per_model_and_kind(session) -> None:
    brand_id = await ralph(session)
    session.add_all([ext_row(brand_id, "asking", p, f"https://x/{p}") for p in (40, 42, 44, 41, 400)])
    session.add_all([ext_row(brand_id, "new", p, f"https://n/{p}", condition="new") for p in (99, 105)])
    for i, p in enumerate((30, 31, 29, 32, 33, 3)):
        session.add(
            SoldSale(
                dedupe_key=f"vinted:o{i}",
                source="vinted_sold",
                reliability=3,
                price_kind="last_seen",
                title="polo",
                brand_id=brand_id,
                model_name=MODEL,
                condition="very_good",
                price=D(p),
                price_eur=D(p),
                sold_at=NOW,
                source_name="vinted",
            )
        )
    await session.flush()
    assert await flag_external_outliers(session) == 1
    assert await flag_sold_outliers(session) == 1
    flagged_ext = (
        await session.execute(select(ExternalPrice.price).where(ExternalPrice.is_outlier))
    ).scalars()
    flagged_sold = (await session.execute(select(SoldSale.price).where(SoldSale.is_outlier))).scalars()
    assert list(flagged_ext) == [D("400.00")] and list(flagged_sold) == [D("3.00")]
    # Recomputed every run: more data making a price plausible clears the flag.
    for i, p in enumerate((4, 3.5, 4.2, 3.8, 3.9, 4.1, 3.7)):
        session.add(
            SoldSale(
                dedupe_key=f"vinted:p{i}",
                source="vinted_sold",
                reliability=3,
                price_kind="last_seen",
                title="polo",
                brand_id=brand_id,
                model_name="Custom Fit",
                condition="very_good",
                price=D(str(p)),
                price_eur=D(str(p)),
                sold_at=NOW,
                source_name="vinted",
            )
        )
    await session.flush()
    await flag_sold_outliers(session)
    assert list((await session.execute(select(SoldSale.price).where(SoldSale.is_outlier))).scalars()) == [
        D("3.00")
    ]


# ---------------------------------------------------------------------------- statistics
async def test_model_stats_rows_and_lookup_in_one_query(session, make_listing) -> None:
    await market(session, make_listing, sold=12, active=8)
    brand_id = await ralph(session)
    session.add_all(
        [
            ext_row(brand_id, "sold", 30, "https://www.ebay.it/itm/9"),
            ext_row(brand_id, "asking", 44, "https://www.depop.com/a"),
            ext_row(brand_id, "new", 110, "https://www.zalando.it/a", condition="new"),
            ext_row(brand_id, "new", 120, "https://www.ralphlauren.it/a", condition="new"),
            # Another model of the brand, only asked: priced on asks.
            *(
                ext_row(brand_id, "asking", 50 + i, f"https://www.ebay.it/b{i}", model="Big Pony")
                for i in range(4)
            ),
        ]
    )
    await session.flush()
    result = await sync_price_evidence(session, full=True)
    assert result["sold_sales_before"] == 12 and result["sold_sales_after"] == 13
    assert result["stats_rows"] > 0

    rows = {r.segment_key: r for r in (await session.execute(select(ModelPriceStat))).scalars()}
    model = rows[stat_key(brand_id, None, MODEL, None, None)]
    assert model.price_basis == "sold" and model.n_sales == 13
    assert model.n_vinted_sold == 12 and model.n_external_sold == 1 and model.n_own == 0
    assert model.n_asking == 9  # 8 Vinted asks + 1 from another marketplace
    assert model.low_price <= model.median_price <= model.high_price
    assert D("26") <= model.median_price <= D("34")
    assert model.median_new_price == D("115.00") and model.n_new == 2
    assert model.n_seen == 20 and model.sell_through == D("0.6000")
    assert model.median_days_to_sell is not None and model.avg_days_to_sell is not None
    assert model.sources["weights"]["own_sale"] == 5.0 and model.sources["n"]["vinted_sold"] == 12
    assert model.category_id is not None and model.model_name == MODEL
    size_cond = rows[stat_key(brand_id, None, MODEL, "M", "very_good")]
    assert size_cond.n_sales == 12  # the external sale has no size
    brand_cat = rows[stat_key(brand_id, model.category_id, None, None, None)]
    assert brand_cat.model_name is None and brand_cat.n_sales == 12
    pony = rows[stat_key(brand_id, None, "Big Pony", None, None)]
    assert pony.price_basis == "asking" and pony.n_sales == 0 and pony.n_asking == 4
    assert pony.sources["ask_to_sale"] == pytest.approx(0.88)
    assert float(pony.median_price) == pytest.approx(51.5 * 0.88, abs=0.01)

    queries = [
        StatQuery("exact", brand_id, model.category_id, MODEL, "M", "very_good"),
        StatQuery("other-size", brand_id, model.category_id, MODEL, "XL", "good"),
        StatQuery("pony", brand_id, model.category_id, "Big Pony", "M", "very_good"),
        StatQuery("no-model", brand_id, model.category_id, None, None, None),
        StatQuery("nothing", None, None, None, None, None),
    ]
    with count_queries(session) as statements:
        found = await lookup_stats(session, queries)
    assert len(statements) == 1  # the whole page in one round trip
    assert set(found) == {"exact", "other-size", "pony", "no-model"}
    assert found["exact"]["level"] == "model_size_condition" and found["exact"]["basis"] == "sold"
    assert found["other-size"]["level"] == "model"
    assert found["pony"]["level"] == "model" and found["pony"]["basis"] == "asking"
    assert found["no-model"]["level"] == "brand_category"
    exact = found["exact"]
    assert set(exact) == {
        "segment",
        "level",
        "basis",
        "median",
        "low",
        "high",
        "n_sales",
        "n_own",
        "n_vinted_sold",
        "n_external_sold",
        "n_asking",
        "days",
        "sell_through",
        "new_price",
        "model",
        "brand",
        "category",
    }
    assert (
        exact["brand"] == "ralph-lauren"
        and exact["category"] == "polo-shirts"
        and exact["new_price"] == 115.0
    )

    # The gate switches external data off: it no longer enters the prices.
    await set_state(
        session, GATE_KEY, {"use_external": False, "use_own_purchases": True, "use_new_cap": False}
    )
    await recompute_model_stats(session)
    model = (
        await session.execute(
            select(ModelPriceStat).where(
                ModelPriceStat.segment_key == stat_key(brand_id, None, MODEL, None, None)
            )
        )
    ).scalar_one()
    assert model.n_external_sold == 0 and model.n_sales == 12
    assert model.sources["excluded"] == {"external_sold": 1, "external_asking": 1}
    assert stat_key(brand_id, None, "Big Pony", None, None) not in set(
        (await session.execute(select(ModelPriceStat.segment_key))).scalars()
    )


# ---------------------------------------------------------------------------- analysis
async def test_analysis_provenance_end_to_end(session, make_listing) -> None:
    await market(session, make_listing, sold=3, active=6)
    brand_id = await ralph(session)
    u = await user(session)
    p = await purchase(session, u, 22, title="Polo Ralph Lauren Custom Slim Fit verde M")
    session.add(
        Sale(
            user_id=u.id,
            purchase_id=p.id,
            sale_price=D("33"),
            net_revenue=D("33"),
            profit=D("7"),
            roi=D("0.3"),
            holding_days=10,
            sale_date=(NOW - timedelta(days=4)).date(),
        )
    )
    session.add_all(
        [
            ext_row(brand_id, "sold", 31, "https://www.ebay.it/itm/5", days_ago=6),
            ext_row(brand_id, "sold", 32, "https://www.ebay.com/itm/6", days_ago=8, source="ebay.com"),
            ext_row(brand_id, "asking", 39, "https://www.vinted-free-market.example/z", source="depop.com"),
            ext_row(brand_id, "new", 99, "https://www.zalando.it/q", condition="new", source="zalando.it"),
            # Different model of the same brand: never used for a Custom Slim Fit.
            ext_row(brand_id, "sold", 12, "https://www.ebay.it/itm/7", model="Big Pony"),
        ]
    )
    await session.flush()
    await sync_sold_sales(session, full=True)
    await session.commit()

    deal = make_listing(price=15, published_days_ago=0.1, external_id="deal")
    res = await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest([deal], now=NOW)
    outcome = await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    await session.commit()
    assert outcome is not None
    opp = (
        await session.execute(select(Opportunity).where(Opportunity.listing_id == res.new_ids[0]))
    ).scalar_one()
    prov = opp.score_breakdown["provenance"]
    assert set(prov) == PROVENANCE_KEYS
    exp = prov["expected_price"]
    assert set(exp) == {
        "value",
        "basis",
        "n",
        "by_source",
        "negotiation_discount",
        "calibrated",
        "prior",
        "label",
    }
    assert set(exp["by_source"]) == {
        "own_sale",
        "own_purchase",
        "vinted_sold",
        "external_sold",
        "vinted_asking",
        "external_asking",
    }
    by = exp["by_source"]
    # 3 Vinted + 1 own resale + 1 own purchase + 2 other marketplaces = 7 sales: sold only.
    assert (
        by["own_sale"] == 1
        and by["own_purchase"] == 1
        and by["vinted_sold"] == 3
        and by["external_sold"] == 2
    )
    assert exp["basis"] == "sold" and by["vinted_asking"] == 0 and by["external_asking"] == 0
    assert exp["value"] == float(opp.expected_sale_price) and exp["n"] == 7
    assert exp["negotiation_discount"] is None and exp["calibrated"] is False
    assert set(exp["prior"]) == {"used", "level", "n"}
    assert "7 vendite concluse (2 tue, 3 Vinted, 2 da altri mercati)" in exp["label"]
    assert "sconto da trattativa non ancora misurato" in exp["label"]
    assert prov["real_sales"] == {"total": 7, "own": 2, "vinted_sold": 3, "external_sold": 2}
    assert set(prov["price_range"]) == {"low", "high", "basis", "label"}
    assert prov["price_range"]["basis"] == "percentiles"
    assert (
        set(prov["days_to_sell"]) == {"value", "n", "basis", "label"}
        and prov["days_to_sell"]["basis"] == "sold"
    )
    assert set(prov["sale_probability"]) == {"value", "n", "basis", "label"}
    new = prov["new_price"]
    assert set(new) == {"value", "n", "sources", "label"} and new["value"] == 99.0 and new["n"] == 1
    assert new["sources"][0] == {
        "source": "zalando.it",
        "price": 99.0,
        "currency": "EUR",
        "date": (NOW - timedelta(days=5)).date().isoformat(),
        "url": "https://www.zalando.it/q",
    }
    ext = prov["external"]
    assert 1 <= len(ext) <= 12 and {e["kind"] for e in ext} == {"sold", "asking", "new"}
    assert all(
        set(e)
        == {
            "kind",
            "source",
            "price",
            "currency",
            "price_eur",
            "date",
            "condition",
            "url",
            "title",
            "used_in_estimate",
        }
        for e in ext
    )
    assert all(e["price"] != 12 for e in ext)  # the other model is never considered
    used = {e["url"] for e in ext if e["used_in_estimate"]}
    assert used == {"https://www.ebay.it/itm/5", "https://www.ebay.com/itm/6"}  # asks are reference only

    # The analysed model is queued for the external search (demand), nothing is searched.
    search = (await session.execute(select(ExternalSearch))).scalar_one()
    assert (
        search.model_key == "ralph-lauren|custom slim fit"
        and search.demand == 1
        and search.status == "pending"
    )
    await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    await session.commit()
    assert (await session.execute(select(ExternalSearch.demand))).scalar_one() == 2

    # Gate: external data and own purchases switched off by the backtest.
    await set_state(
        session, GATE_KEY, {"use_external": False, "use_own_purchases": False, "use_new_cap": False}
    )
    await session.commit()
    outcome = await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    prov = outcome.result.provenance
    by = prov["expected_price"]["by_source"]
    assert by["external_sold"] == 0 and by["own_purchase"] == 0 and by["own_sale"] == 1
    assert not any(e["used_in_estimate"] for e in prov["external"])
    assert "altri mercati esclusi" in prov["expected_price"]["label"]
    assert "acquisti esclusi" in prov["expected_price"]["label"]


async def test_negotiation_discount_applies_to_vinted_sales(session, make_listing) -> None:
    await market(session, make_listing, sold=8, active=0)
    deal = make_listing(price=15, published_days_ago=0.1, external_id="deal")
    res = await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest([deal], now=NOW)
    await session.commit()
    plain = await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    await set_state(
        session, "negotiation_discount", {"discount": 0.1, "n": 4, "note": "", "measured_at": None}
    )
    await session.commit()
    discounted = await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    assert plain is not None and discounted is not None
    a, b = plain.result.market.expected_sale_price, discounted.result.market.expected_sale_price
    assert float(b) == pytest.approx(float(a) * 0.9, abs=1.0)
    exp = discounted.result.provenance["expected_price"]
    assert exp["negotiation_discount"] == 0.1 and "scontato del 10%" in exp["label"]


async def test_batch_loads_evidence_with_constant_queries(session, make_listing) -> None:
    """Own records and external rows are fetched for the whole batch, never per listing."""
    await market(session, make_listing, sold=6, active=2)
    brand_id = await ralph(session)
    session.add(ext_row(brand_id, "sold", 30, "https://www.ebay.it/itm/c"))
    await session.commit()
    deals = [make_listing(price=14 + i, published_days_ago=0.1, external_id=f"d{i}") for i in range(8)]
    res = await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD).ingest(deals, now=NOW)
    await session.commit()
    pipeline = AnalysisPipeline(session)
    with count_queries(session) as small:
        await pipeline.analyze_many(res.new_ids[:2], now=NOW)
    await session.commit()
    with count_queries(session) as large:
        await pipeline.analyze_many(res.new_ids, now=NOW)
    await session.commit()
    evidence = [s for s in large if "sold_sales" in s or "external_prices" in s or "external_searches" in s]
    assert len(evidence) == 3  # own records, external rows, demand upsert
    assert len(large) == len(small)


async def test_listing_without_extra_evidence_is_unchanged(session, make_listing) -> None:
    """No own records, no external rows, no discount: same estimate as before this feature."""
    await market(session, make_listing, sold=10, active=6)
    deal = make_listing(price=15, published_days_ago=0.1, external_id="deal")
    res = await IngestionService(session, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest([deal], now=NOW)
    await session.commit()
    outcome = await AnalysisPipeline(session).analyze_listing(res.new_ids[0], now=NOW)
    assert outcome is not None
    from app.opportunities.engine import price_estimate

    listing = await session.get(Listing, res.new_ids[0])
    catalog = await load_catalog(session)
    pipeline = AnalysisPipeline(session)
    subject = pipeline.subject_context(listing, catalog, NOW, ())
    cands = await pipeline.candidates(subject.profile, catalog, listing.brand_id, NOW)
    prior = await pipeline.segment_prior(listing.brand_id, listing.category_id, listing.model_name)
    _, _, plain = price_estimate(subject.profile, cands, NOW, prior)
    assert outcome.result.market.expected_sale_price == plain.expected_sale_price
    assert outcome.result.market.quick_sale_price == plain.quick_sale_price
    prov = outcome.result.provenance
    assert prov["external"] == [] and prov["new_price"] is None
    assert prov["real_sales"]["total"] == prov["real_sales"]["vinted_sold"] == outcome.result.market.n_sold


async def test_a_bought_listing_is_counted_once_and_comes_back_when_the_purchase_is_deleted(
    session, make_listing
) -> None:
    from sqlalchemy import select

    from app.db.models import Listing, Purchase, SoldSale
    from app.market.sold_sales import sync_own_records, sync_sold_sales

    async def keys() -> set[str]:
        return {r.dedupe_key for r in (await session.execute(select(SoldSale))).scalars()}

    await market(session, make_listing, sold=6, active=0)
    await sync_sold_sales(session, full=True)
    bought = (await session.execute(select(Listing).where(Listing.external_id == "s2"))).scalar_one()
    p = await purchase(session, await user(session), 24, listing_id=bought.id)
    await sync_own_records(session, [p.id])  # what the portfolio API does right after recording it
    k = await keys()
    assert f"purchase:{p.id}" in k and f"vinted:{bought.external_id}" not in k  # recorded once
    listing_id = p.listing_id
    await session.delete(await session.get(Purchase, p.id))
    await session.flush()
    await sync_own_records(session, [], [listing_id])  # what the portfolio API does after deleting it
    k = await keys()
    assert f"purchase:{p.id}" not in k and f"vinted:{bought.external_id}" in k
