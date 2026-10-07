"""POST /extension/page-stats: pre-computed statistics for a whole page in one query."""

from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import event, func, select

from app.db.models import Listing, ModelPriceStat, Opportunity
from app.db.session import get_engine, session_scope
from app.ingestion.catalog import load_catalog
from app.market.model_stats import stat_key
from tests.api.test_api import API
from tests.api.test_extension_api import _paired


def _stat(key: str, brand_id: int, category_id: int | None, model: str | None, **kw: Any) -> ModelPriceStat:
    values: dict[str, Any] = {
        "segment_key": key,
        "brand_id": brand_id,
        "category_id": category_id,
        "model_name": model,
        "size_normalized": None,
        "condition": None,
        "price_basis": "sold",
        "median_price": Decimal("45.00"),
        "low_price": Decimal("38.00"),
        "high_price": Decimal("52.00"),
        "n_samples": 7,
        "n_sales": 7,
        "n_own": 1,
        "n_vinted_sold": 5,
        "n_external_sold": 1,
        "n_asking": 12,
        "n_outliers": 0,
        "avg_days_to_sell": Decimal("10.0"),
        "median_days_to_sell": Decimal("9.5"),
        "n_seen": 20,
        "sell_through": Decimal("0.42"),
        "median_new_price": Decimal("120.00"),
        "window_days": 180,
    }
    values.update(kw)
    return ModelPriceStat(**values)


async def _seed_stats() -> None:
    async with session_scope() as s:
        catalog = await load_catalog(s)
        nike, sneakers = catalog.brand_id("nike"), catalog.category_id("sneakers")
        assert nike and sneakers
        s.add_all(
            [
                # Air Max 90, EU42, very good: the most specific segment, priced on sales.
                _stat(
                    stat_key(nike, None, "Air Max 90", "EU42", "very_good"),
                    nike,
                    None,
                    "Air Max 90",
                    size_normalized="EU42",
                    condition="very_good",
                ),
                # The whole model, priced on asks only (too few sales).
                _stat(
                    stat_key(nike, None, "Air Max 90", None, None),
                    nike,
                    None,
                    "Air Max 90",
                    price_basis="asking",
                    median_price=Decimal("60.00"),
                    n_sales=1,
                    n_own=0,
                    n_vinted_sold=1,
                    n_external_sold=0,
                ),
                # Nike sneakers in general.
                _stat(
                    stat_key(nike, sneakers, None, None, None),
                    nike,
                    sneakers,
                    None,
                    median_price=Decimal("35.00"),
                    n_sales=40,
                    median_new_price=None,
                ),
                # Two more models: one with enough concluded sales, one without.
                _stat(stat_key(nike, None, "Air Force 1", None, None), nike, None, "Air Force 1", n_sales=6),
                _stat(stat_key(nike, None, "Dunk", None, None), nike, None, "Dunk", n_sales=4),
            ]
        )


async def test_market_cache_carries_model_sales(auth_client: httpx.AsyncClient) -> None:
    """The instant verdict gets per-model sold statistics, only for models with >= 5 sales
    (any size and condition; models priced on asks are left out)."""
    await _seed_stats()
    headers = await _paired(auth_client)
    r = await auth_client.get(f"{API}/extension/market-cache", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["models"] == {"nike|air force 1": [45.0, 38.0, 52.0, 6, 9.5, 0.42]}
    assert "analysis;dur=" in r.headers["server-timing"]


async def test_page_stats_one_query_for_the_page(auth_client: httpx.AsyncClient) -> None:
    await _seed_stats()
    headers = await _paired(auth_client)
    items = [
        {
            "vinted_id": "101",
            "title": "Nike Air Max 90 bianche",
            "brand": "Nike",
            "size": "42",
            "condition": "Ottime condizioni",
        },
        # Other size and condition: falls back to the whole model (asks only).
        {
            "vinted_id": "102",
            "title": "Air Max 90 nere",
            "brand": "Nike",
            "size": "44",
            "condition": "Nuovo con cartellino",
        },
        # No model recognised: brand + category.
        {"vinted_id": "103", "title": "Sneakers bianche", "brand": "Nike", "size": "41", "condition": None},
        # Nothing known: omitted.
        {"vinted_id": "104", "title": "Felpa", "brand": None, "size": None, "condition": None},
    ]
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        r = await auth_client.post(f"{API}/extension/page-stats", json={"items": items}, headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["generated_at"]
    stats = body["stats"]
    assert set(stats) == {"101", "102", "103"}

    exact = stats["101"]
    assert exact["level"] == "model_size_condition" and exact["basis"] == "sold"
    assert exact["median"] == 45.0 and exact["low"] == 38.0 and exact["high"] == 52.0
    assert exact["n_sales"] == 7 and exact["n_own"] == 1 and exact["n_vinted_sold"] == 5
    assert exact["n_external_sold"] == 1 and exact["n_asking"] == 12
    assert exact["days"] == 9.5 and exact["sell_through"] == 0.42 and exact["new_price"] == 120.0
    assert exact["model"] == "Air Max 90" and exact["brand"] == "nike"
    # Model-level segments are filed under "any category": the card's category is reported.
    assert exact["category"] == "sneakers"
    assert exact["segment"].endswith("|air max 90|EU42|very_good")

    assert stats["102"]["level"] == "model" and stats["102"]["basis"] == "asking"
    assert stats["102"]["median"] == 60.0
    general = stats["103"]
    assert general["level"] == "brand_category" and general["median"] == 35.0
    assert general["category"] == "sneakers" and general["model"] is None and general["new_price"] is None

    # ONE statement for the statistics, none of them writes, nothing stored or analysed.
    assert sum("model_price_stats" in st for st in statements) == 1
    assert not [st for st in statements if st.lstrip().upper().startswith(("INSERT", "DELETE"))]
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Listing))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(Opportunity))).scalar_one() == 0

    # Timed like the captures.
    assert "db;dur=" in r.headers["server-timing"] and "total;dur=" in r.headers["server-timing"]
    assert r.headers["access-control-expose-headers"] == "Server-Timing"


async def test_page_stats_validation_and_auth(
    client: httpx.AsyncClient, auth_client: httpx.AsyncClient
) -> None:
    headers = await _paired(auth_client)
    too_many = [{"vinted_id": str(i), "title": "Polo"} for i in range(121)]
    r = await auth_client.post(f"{API}/extension/page-stats", json={"items": too_many}, headers=headers)
    assert r.status_code == 422
    bad_id = await auth_client.post(
        f"{API}/extension/page-stats", json={"items": [{"vinted_id": "12a", "title": "x"}]}, headers=headers
    )
    assert bad_id.status_code == 422
    empty = await auth_client.post(
        f"{API}/extension/page-stats", json={"items": [{"vinted_id": "7", "title": "Polo"}]}, headers=headers
    )
    assert empty.status_code == 200 and empty.json()["stats"] == {}

    anon = httpx.AsyncClient(transport=client._transport, base_url="http://testserver")
    r = await anon.post(f"{API}/extension/page-stats", json={"items": [{"vinted_id": "7", "title": "Polo"}]})
    assert r.status_code == 401
    await anon.aclose()
