"""Phase 2.7: what was asked and what was really paid are never mixed; a sold listing reaches the
sold-prices table with its estimated sale date, last asking price and days online."""

import csv
import io
from datetime import timedelta
from decimal import Decimal as D

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import Listing, SoldSale
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.market.sold_table import MIN_SAMPLES, sold_price_table, to_csv
from tests.conftest import NOW
from tests.integration.test_tracking_ingest import card


async def test_a_sold_listing_enters_the_table_with_asking_price_window_and_days(
    session, make_listing
) -> None:
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD)
    pl = card(make_listing(external_id="8001", price=22, published_days_ago=10))
    await svc.ingest([pl], now=NOW)
    await svc.ingest([pl.model_copy(update={"price": D("18")})], now=NOW + timedelta(days=2))
    # Noticed as sold four days after it was last seen on sale.
    await svc.ingest([pl.model_copy(update={"status": "sold"})], now=NOW + timedelta(days=6))
    await session.commit()

    sale = (await session.execute(select(SoldSale))).scalar_one()
    assert sale.source == "vinted_sold" and sale.price_kind == "last_seen"
    # The last price asked while on sale; nothing claims it was the price paid.
    assert sale.asking_price == D("18.00") and sale.realized_price is None
    assert sale.window_start == NOW + timedelta(days=2) and sale.window_end == NOW + timedelta(days=6)
    assert sale.sold_at == NOW + timedelta(days=4)  # midpoint of the window
    assert sale.observed_days == D("4.0") and sale.days_to_sell is not None
    li = (await session.execute(select(Listing).where(Listing.external_id == "8001"))).scalar_one()
    assert li.sold_at == sale.sold_at

    table = await sold_price_table(session)
    [row] = [r for r in table if r["samples"]["total"] == 1]
    assert row["asking"]["median"] == 18.0 and row["realized"] is None and row["reported"] is None
    # One sale is shown, but not as an estimate.
    assert row["evidence"] == "insufficient" and MIN_SAMPLES == 3
    assert row["days_to_sell"]["basis"] in ("published", "observed")


async def test_unknown_publication_date_gives_observed_days_marked_as_such(session, make_listing) -> None:
    svc = IngestionService(session, "vinted", AcquisitionMode.EXTENSION_CARD)
    pl = card(make_listing(external_id="8002", price=15)).model_copy(update={"published_at": None})
    await svc.ingest([pl], now=NOW)
    await svc.ingest([pl.model_copy(update={"status": "sold"})], now=NOW + timedelta(days=3))
    await session.commit()
    sale = (await session.execute(select(SoldSale))).scalar_one()
    assert sale.days_to_sell is None and sale.observed_days == D("1.5")
    [row] = await sold_price_table(session)
    assert row["days_to_sell"] == {"median": 1.5, "samples": 1, "basis": "observed"}


def _sale(i: int, kind: str, price: float, **kw) -> SoldSale:
    asking = D(str(price)) if kind == "last_seen" else None
    realized = D(str(price)) if kind in ("paid", "received") else None
    return SoldSale(
        dedupe_key=f"t:{i}",
        source={
            "last_seen": "vinted_sold",
            "paid": "own_purchase",
            "received": "own_sale",
            "reported": "external_sold",
        }[kind],
        reliability=1,
        price_kind=kind,
        title="Polo",
        model_name="Custom Fit",
        condition="very_good",
        price=D(str(price)),
        asking_price=asking,
        realized_price=realized,
        price_eur=D(str(price)),
        sold_at=NOW,
        source_name="test",
        **kw,
    )


async def test_the_table_keeps_the_three_kinds_of_price_apart(session) -> None:
    rows = [
        *(_sale(i, "paid", p) for i, p in enumerate((30, 32))),
        *(
            _sale(10 + i, "last_seen", p, days_to_sell=D(d))
            for i, (p, d) in enumerate(((40, 4), (42, 6), (44, 8)))
        ),
        _sale(20, "reported", 50),
    ]
    session = session
    session.add_all(rows)
    await session.commit()
    [r] = await sold_price_table(session)
    assert r["samples"] == {"total": 6, "realized": 2, "asking": 3, "reported": 1}
    assert r["realized"] == {"median": 31.0, "min": 30.0, "max": 32.0}
    assert r["asking"]["median"] == 42.0 and r["asking"]["min"] == 40.0 and r["asking"]["max"] == 44.0
    assert r["reported"] == {"median": 50.0}  # never folded into the others
    assert r["days_to_sell"] == {"median": 6.0, "samples": 3, "basis": "published"}
    assert r["evidence"] == "sufficient"
    # Outliers stay in the data but out of the table.
    rows[0].is_outlier = True
    await session.commit()
    [r] = await sold_price_table(session)
    assert r["samples"]["realized"] == 1


async def test_csv_has_one_column_per_meaning(session) -> None:
    session.add_all([_sale(1, "paid", 30), _sale(2, "last_seen", 40), _sale(3, "last_seen", 44)])
    await session.commit()
    text = to_csv(await sold_price_table(session))
    assert text.startswith("﻿")
    [header, row] = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    cells = dict(zip(header, row, strict=True))
    assert cells["Prezzo reale: mediana"] == "30.0" and cells["Ultimo prezzo richiesto: mediana"] == "42.0"
    assert cells["Campioni"] == "3" and cells["Evidenza"] == "sufficiente"


async def test_a_price_cannot_be_both_asked_and_paid(session) -> None:
    bad = _sale(1, "last_seen", 40)
    bad.realized_price = D("40")
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
    missing = _sale(2, "paid", 30)
    missing.realized_price = None
    session.add(missing)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_migration_splits_existing_prices_by_what_they_are() -> None:
    import uuid

    from tests.integration.test_migration import ADMIN_DSN, MIG_DB, MIG_DSN, alembic

    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
    await admin.execute(f"CREATE DATABASE {MIG_DB}")
    await admin.close()
    c = None
    try:
        alembic("upgrade", "0015")
        c = await asyncpg.connect(MIG_DSN)
        lid = uuid.uuid4()
        await c.execute(
            "INSERT INTO listings (id, provider, external_id, url, title, price, status, first_seen_at, last_active_at,"
            " sold_detected_at) VALUES ($1, 'vinted', '9500', 'https://www.vinted.it/items/9500', 'Felpa', 20, 'sold',"
            " now() - interval '10 days', now() - interval '6 days', now() - interval '2 days')",
            lid,
        )
        base = (
            "INSERT INTO sold_sales (dedupe_key, source, reliability, price_kind, listing_id, title, price, currency,"
            " price_eur, sold_at, source_name) VALUES ($1, $2, $3, $4, $5, 'Felpa', 20, 'EUR', 20,"
            " now() - interval '4 days', 'x')"
        )
        await c.execute(base, "vinted:9500", "vinted_sold", 3, "last_seen", lid)
        await c.execute(base, "purchase:1", "own_purchase", 2, "paid", None)
        await c.execute(base, "ext:1", "external_sold", 4, "reported", None)
        alembic("upgrade", "0016")
        rows = {r["price_kind"]: r for r in await c.fetch("SELECT * FROM sold_sales")}
        assert rows["last_seen"]["asking_price"] == 20 and rows["last_seen"]["realized_price"] is None
        assert rows["paid"]["realized_price"] == 20 and rows["paid"]["asking_price"] is None
        assert rows["reported"]["asking_price"] is None and rows["reported"]["realized_price"] is None
        assert rows["last_seen"]["window_start"] is not None and rows["last_seen"]["observed_days"] == 6.0
        alembic("downgrade", "0015")
        assert await c.fetchval("SELECT count(*) FROM sold_sales") == 3
    finally:
        if c is not None:
            await c.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f"DROP DATABASE IF EXISTS {MIG_DB}")
        await admin.close()
