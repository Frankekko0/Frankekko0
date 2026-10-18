"""The opportunity page says why each comparable is, or is not, part of the price (PX-11)."""

from collections.abc import Callable

import httpx

from app.ingestion.service import IngestionService
from app.marketplace.base import ProviderListing
from app.opportunities.pipeline import AnalysisPipeline
from tests.conftest import NOW

API = "/api/v1"
AM95 = "Nike Air Max 95 scarpe sneakers uomo 42"
AF1 = "Nike Air Force 1 scarpe sneakers uomo 42"


async def seed(make_listing: Callable[..., ProviderListing]) -> str:
    from app.db.session import session_scope

    def shoe(title: str, price: float, i: int, ago: float, **kw) -> ProviderListing:
        return make_listing(
            title=title,
            price=price,
            brand="Nike",
            category="Sneakers",
            size="EU 42",
            seller_id=f"why-{i}",
            published_days_ago=ago,
            **kw,
        )

    rows = [shoe(AM95, 98 + i, i, 12 + 2 * i, status="sold", sold_after_days=3) for i in range(10)]
    rows += [shoe(AF1, 62 + i % 6, 100 + i, 8 + i, status="sold", sold_after_days=3) for i in range(12)]
    async with session_scope() as s:
        await IngestionService(s, "test").ingest(rows, now=NOW)
        res = await IngestionService(s, "test").ingest([shoe(AM95, 55, 999, 0.05)], now=NOW)
        outcome = await AnalysisPipeline(s).analyze_listing(res.new_ids[0], now=NOW)
        assert outcome is not None
        return str(outcome.opportunity_id)


async def test_each_comparable_carries_its_tier_what_it_shares_and_the_reason_it_was_left_out(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    opp_id = await seed(make_listing)
    detail = (await auth_client.get(f"{API}/opportunities/{opp_id}")).json()
    comps = detail["comparables"]
    used = [c for c in comps if c["included"]]
    left_out = [c for c in comps if not c["included"] and c["exclusion_reason"] == "other_line"]
    assert used and left_out
    for c in used:
        assert c["tier"] in ("model", "line")
        assert "stesso modello" in c["match"] or "stessa linea" in c["match"]
        assert c["exclusion_label"] is None
    for c in left_out:
        assert c["tier"] == "family" and "altra linea o modello" in c["match"]
        assert "altra linea" in c["exclusion_label"] and "riferimento" in c["exclusion_label"]
    # the market summary says which tier carries the price and keeps the other lines apart
    summary = detail["market_comparison"]
    assert summary["tier"] in ("model", "line") and summary["specific_used"] >= 5
    assert summary["reference"]["n"] >= len(left_out) and "riferimento" in summary["reference"]["label"]
