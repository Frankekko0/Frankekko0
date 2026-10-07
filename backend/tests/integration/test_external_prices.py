"""External price references end to end: refresh of the models due with a fake Serper transport
and recorded fixtures (never the network) -> rows in the database, budget, errors, status, CLI,
concluded sales."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update

from app.core.config import Settings
from app.db.models import ExternalPrice, ExternalSearch, SoldSale
from app.external import service
from app.external.jobs import refresh_external_prices_task
from app.external.keys import model_key
from app.external.provider import BUDGET_KEY, SerperProvider
from app.external.service import external_status, refresh_due_models
from app.ingestion.catalog import load_catalog
from app.market.sold_sales import sold_sales_summary
from app.market.state import get_state
from app.opportunities.pipeline import EXTERNAL_COLUMNS, external_ref
from app.tools import external_prices as cli

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "external"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
KEY = "sk-test-secret-key-0123456789"
AM90 = model_key("nike", "Air Max 90")
STATUS_KEYS = {
    "provider",
    "enabled",
    "key_configured",
    "cost_per_query_usd",
    "free_queries",
    "month_used",
    "month_budget",
    "today_used",
    "daily_max",
    "refresh_days",
    "models_cached",
    "models_due",
    "models_pending",
    "prices",
    "rejected",
    "last_run",
    "last_error",
    "expected_monthly_queries",
}


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "external_search_provider": "serper",
        "serper_api_key": KEY,
        "external_search_daily_max": 60,
        "external_search_monthly_budget": 900,
        "external_refresh_days": 30,
    }
    return Settings(**{**base, **kw})


class FakeSerper:
    """Answers like Serper from the fixtures: Air Max 90 has results, every other model none."""

    def __init__(self, status: int | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "google.serper.dev"
        body = json.loads(request.content)
        endpoint = request.url.path.strip("/")
        self.calls.append((endpoint, body["q"]))
        if self.status:
            return httpx.Response(self.status, json=fixture("errors.json").get(str(self.status), {}))
        if "Air Max 90" not in body["q"]:
            return httpx.Response(200, json=fixture("empty.json"))
        if endpoint == "shopping":
            return httpx.Response(200, json=fixture("shopping_nike_air_max_90.json"))
        if "usato prezzo" in body["q"]:
            return httpx.Response(200, json=fixture("search_nike_air_max_90_used.json"))
        return httpx.Response(200, json=fixture("search_nike_air_max_90_sold.json"))

    def provider(self) -> SerperProvider:
        return SerperProvider(KEY, transport=httpx.MockTransport(self), retry_delay=0)


async def queue(session: Any, brand: str, model: str, category: str | None, demand: int = 1) -> str:
    """A model seen by the analysis (what ``record_demand`` writes)."""
    catalog = await load_catalog(session)
    key = model_key(brand, model)
    session.add(
        ExternalSearch(
            model_key=key,
            brand_id=catalog.brand_id(brand),
            category_id=catalog.category_id(category),
            model_name=model,
            demand=demand,
        )
    )
    await session.commit()
    return key


async def search_row(session: Any, key: str) -> ExternalSearch:
    session.expire_all()
    return (await session.execute(select(ExternalSearch).where(ExternalSearch.model_key == key))).scalar_one()


# ---------------------------------------------------------------------------- refresh
async def test_refresh_stores_prices_with_source_date_currency_and_kind(session: Any) -> None:
    await queue(session, "nike", "Air Max 90", "sneakers", demand=5)
    empty = await queue(session, "patagonia", "Down Sweater", "puffer-jackets", demand=1)
    fake = FakeSerper()
    result = await refresh_due_models(session, now=NOW, provider=fake.provider(), settings=settings())

    # Highest demand first; the sold query only for the model that was found.
    assert [e for e, _ in fake.calls] == ["shopping", "search", "search", "shopping", "search"]
    assert "usato prezzo" in fake.calls[1][1] and "venduto OR sold" in fake.calls[2][1]
    assert all("vinted" not in q for _, q in fake.calls)
    assert result["status"] == "ok"
    assert (result["models_searched"], result["models_ok"], result["models_no_results"]) == (2, 1, 1)
    assert result["queries_used"] == 5
    assert result["prices_stored"] == {"new": 4, "asking": 11, "sold": 6}
    assert result["rejected"]["kids"] == 3 and result["rejected"]["other_model"] == 4
    assert result["sold_synced"] == 6

    rows = (await session.execute(select(ExternalPrice))).scalars().all()
    assert len(rows) == 21
    for r in rows:
        # Every stored price: source, date, currency, condition and kind.
        assert r.source and r.source_url and r.provider == "serper" and r.query
        assert r.observed_at == NOW and r.currency in ("EUR", "GBP", "USD")
        assert r.kind in ("new", "asking", "sold") and r.condition
        assert r.price > 0 and r.price_eur > 0
        assert r.model_key == AM90 and r.model_name == "Air Max 90" and r.brand_id and r.category_id
        assert r.match["fx"]["date"] and r.match["score"] == float(r.match_score)
        assert (
            r.dedupe_key == hashlib.sha256(f"{r.kind}|{r.source_url}|{r.price:.2f}".encode()).hexdigest()[:40]
        )
    by_url = {r.source_url: r for r in rows}
    uk = by_url["https://www.ebay.co.uk/itm/335512345678"]
    assert (uk.kind, uk.source, uk.currency, str(uk.price), str(uk.price_eur)) == (
        "sold",
        "ebay.co.uk",
        "GBP",
        "48.00",
        "56.71",
    )
    assert uk.source_date == datetime(2026, 9, 14, 12, tzinfo=UTC) and uk.size == "EU43"
    nike = by_url["https://www.nike.com/it/t/scarpa-air-max-90-uomo-6n3vKB/CN8490-100"]
    assert (nike.kind, nike.condition, nike.source_date) == ("new", "new_with_tags", None)
    vestiaire = next(r for r in rows if r.source == "vestiairecollective.com" and r.kind == "sold")
    assert vestiaire.source_date == datetime(2026, 9, 1, 12, tzinfo=UTC) and vestiaire.condition == "good"
    # The 950 EUR "Air Max 90" is kept with the others but flagged as implausible for the model.
    assert by_url["https://www.ebay.it/itm/196500000950"].is_outlier
    assert sum(r.is_outlier for r in rows) == 1

    am90 = await search_row(session, AM90)
    assert (am90.status, am90.demand, am90.queries_used, am90.error) == ("ok", 0, 3, None)
    assert am90.last_searched_at == NOW and am90.next_refresh_at == NOW + timedelta(days=30)
    assert am90.results["kept"] == {"new": 4, "asking": 11, "sold": 6}
    assert am90.results["stored"] == {"new": 4, "asking": 11, "sold": 6}
    assert am90.results["rejected"]["outlier"] == 1 and am90.results["queries"] == 3
    other = await search_row(session, empty)
    assert (other.status, other.demand, other.queries_used) == ("no_results", 0, 2)
    assert other.next_refresh_at == NOW + timedelta(days=30)
    budget = await get_state(session, BUDGET_KEY)
    assert budget is not None
    assert (budget["today_used"], budget["month_used"], budget["day"]) == (5, 5, "2026-10-07")
    assert budget["last_run"] == NOW.isoformat() and budget["last_error"] is None

    # External sold rows became concluded sales (source 4, price reported, dated by the source).
    sales = (
        (await session.execute(select(SoldSale).where(SoldSale.source == "external_sold"))).scalars().all()
    )
    assert len(sales) == 6
    assert all(
        s.price_kind == "reported" and s.reliability == 4 and s.model_name == "Air Max 90" for s in sales
    )
    uk_sale = next(s for s in sales if s.source_url == "https://www.ebay.co.uk/itm/335512345678")
    assert (uk_sale.source_name, uk_sale.sold_at, uk_sale.currency) == (
        "ebay.co.uk",
        datetime(2026, 9, 14, 12, tzinfo=UTC),
        "GBP",
    )
    assert (await sold_sales_summary(session))["by_source"]["external_sold"] == 6

    # The analysis reads them as references (pipeline column set).
    catalog = await load_catalog(session)
    refs = [external_ref(r, catalog) for r in (await session.execute(select(*EXTERNAL_COLUMNS))).all()]
    assert {r.brand_slug for r in refs} == {"nike"} and {r.kind for r in refs} == {"new", "asking", "sold"}

    # Nothing is due any more: no query sent.
    again = await refresh_due_models(
        session, now=NOW + timedelta(hours=1), provider=fake.provider(), settings=settings()
    )
    assert again["status"] == "nothing_due" and len(fake.calls) == 5


async def test_refresh_after_the_period_dedupes_and_skips_unseen_models(session: Any) -> None:
    await queue(session, "nike", "Air Max 90", "sneakers", demand=3)
    unseen = await queue(session, "nike", "Dunk Low", "sneakers", demand=1)
    fake = FakeSerper()
    await refresh_due_models(session, now=NOW, provider=fake.provider(), settings=settings())
    first_calls = len(fake.calls)
    # A month later the Air Max 90 has been seen again (demand), the Dunk Low has not.
    await session.execute(update(ExternalSearch).where(ExternalSearch.model_key == AM90).values(demand=2))
    await session.commit()
    later = NOW + timedelta(days=31)
    result = await refresh_due_models(session, now=later, provider=fake.provider(), settings=settings())
    assert result["models_searched"] == 1 and len(fake.calls) == first_calls + 3
    assert result["prices_stored"] == {"new": 0, "asking": 0, "sold": 0}  # same pages, same prices
    assert (await session.execute(select(func.count()).select_from(ExternalPrice))).scalar() == 21
    # Prices keep the date they were first observed.
    assert (await session.execute(select(func.max(ExternalPrice.observed_at)))).scalar() == NOW
    am90 = await search_row(session, AM90)
    assert am90.queries_used == 6 and am90.results["stored"] == {"new": 0, "asking": 0, "sold": 0}
    assert am90.results["kept"]["sold"] == 6 and am90.results["duplicates"] == 0
    assert (await search_row(session, unseen)).last_searched_at == NOW  # not searched again


async def test_budget_is_counted_before_calling(session: Any) -> None:
    await queue(session, "nike", "Air Max 90", "sneakers", demand=5)
    second = await queue(session, "nike", "Dunk Low", "sneakers", demand=2)
    fake = FakeSerper()
    result = await refresh_due_models(
        session, now=NOW, provider=fake.provider(), settings=settings(external_search_daily_max=3)
    )
    # 3 queries for the first model (2 + sold); the second would need 2 more: not even started.
    assert len(fake.calls) == 3
    assert result["status"] == "budget_exhausted" and result["models_skipped_budget"] == 1
    skipped = await search_row(session, second)
    assert (skipped.status, skipped.demand, skipped.next_refresh_at, skipped.last_searched_at) == (
        "skipped_budget",
        2,
        None,
        None,
    )
    budget = await get_state(session, BUDGET_KEY)
    assert budget is not None and budget["today_used"] == 3

    # Same day, monthly budget already spent: no call at all; the next day the daily count restarts
    # but the month does not.
    fake2 = FakeSerper()
    tight = settings(external_search_daily_max=60, external_search_monthly_budget=4)
    result = await refresh_due_models(
        session, now=NOW + timedelta(days=1), provider=fake2.provider(), settings=tight
    )
    assert fake2.calls == [] and result["status"] == "budget_exhausted"
    roomy = settings(external_search_daily_max=60, external_search_monthly_budget=5)
    result = await refresh_due_models(
        session, now=NOW + timedelta(days=1), provider=fake2.provider(), settings=roomy
    )
    assert len(fake2.calls) == 2 and result["models_no_results"] == 1  # Dunk Low: nothing found
    budget = await get_state(session, BUDGET_KEY)
    assert budget is not None and (budget["today_used"], budget["month_used"]) == (2, 5)


async def test_sold_query_needs_budget_and_can_be_turned_off(session: Any) -> None:
    await queue(session, "nike", "Air Max 90", "sneakers", demand=5)
    fake = FakeSerper()
    result = await refresh_due_models(
        session, now=NOW, provider=fake.provider(), settings=settings(external_search_sold_query=False)
    )
    assert len(fake.calls) == 2 and result["prices_stored"]["sold"] == 1  # Grailed "Sold for $120"


@pytest.mark.parametrize(
    ("status", "words"), [(401, "Chiave Serper rifiutata"), (402, "Crediti"), (429, "Limite")]
)
async def test_fatal_provider_errors_stop_the_run(
    session: Any,
    status: int,
    words: str,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await queue(session, "nike", "Air Max 90", "sneakers", demand=5)
    await queue(session, "nike", "Dunk Low", "sneakers", demand=2)
    fake = FakeSerper(status=status)
    result = await refresh_due_models(session, now=NOW, provider=fake.provider(), settings=settings())
    assert len(fake.calls) == 1  # no retry, no other model
    assert result["status"] == "stopped" and words in result["last_error"]
    assert KEY not in result["last_error"]
    row = await search_row(session, AM90)
    # Not the model's fault: still due, demand kept.
    assert (row.status, row.next_refresh_at, row.demand) == ("pending", None, 5)
    budget = await get_state(session, BUDGET_KEY)
    assert budget is not None and words in budget["last_error"] and budget["today_used"] == 1
    assert KEY not in json.dumps(budget)
    assert (await session.execute(select(func.count()).select_from(ExternalPrice))).scalar() == 0
    captured = capsys.readouterr()
    logs = captured.out + captured.err + caplog.text  # stdout or stdlib logging, per configuration
    assert "external.search_failed" in logs  # logged, without the key
    assert KEY not in logs


async def test_server_errors_back_off_and_stop_after_three_models(session: Any) -> None:
    for i, model in enumerate(("Air Max 90", "Dunk Low", "Air Force 1", "Air Max 95")):
        await queue(session, "nike", model, "sneakers", demand=10 - i)
    fake = FakeSerper(status=500)
    result = await refresh_due_models(session, now=NOW, provider=fake.provider(), settings=settings())
    assert result["status"] == "stopped" and result["models_error"] == 3
    assert len(fake.calls) == 6  # one retry per model
    row = await search_row(session, AM90)
    assert row.status == "error" and "HTTP 500" in (row.error or "")
    assert row.next_refresh_at == NOW + timedelta(hours=6) and row.demand == 10 and row.results["errors"] == 1
    untouched = await search_row(session, model_key("nike", "Air Max 95"))
    assert untouched.status == "pending" and untouched.last_searched_at is None
    # Second failure in a row for the same model: the wait doubles.
    later = NOW + timedelta(hours=7)
    await refresh_due_models(session, now=later, max_models=1, provider=fake.provider(), settings=settings())
    row = await search_row(session, AM90)
    assert row.results["errors"] == 2 and row.next_refresh_at == later + timedelta(hours=12)


async def test_disabled_without_provider_or_key(session: Any) -> None:
    await queue(session, "nike", "Air Max 90", "sneakers")
    off = await refresh_due_models(session, now=NOW, settings=settings(external_search_provider="none"))
    assert off["status"] == "disabled" and "EXTERNAL_SEARCH_PROVIDER" in off["reason"]
    no_key = await refresh_due_models(session, now=NOW, settings=settings(serper_api_key=""))
    assert no_key["status"] == "disabled" and "SERPER_API_KEY" in no_key["reason"]
    assert (await search_row(session, AM90)).status == "pending"
    # The worker job with the default settings (provider none): a clear no-op.
    job = await refresh_external_prices_task({})
    assert job["status"] == "disabled" and job["reason"]


# ---------------------------------------------------------------------------- status
async def test_external_status_shape(session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    configured = settings()
    monkeypatch.setattr(service, "get_settings", lambda: configured)
    await queue(session, "nike", "Air Max 90", "sneakers", demand=5)
    await queue(session, "patagonia", "Down Sweater", "puffer-jackets", demand=1)
    now = datetime.now(UTC)
    await refresh_due_models(session, now=now, provider=FakeSerper().provider(), settings=configured)
    await queue(session, "nike", "Dunk Low", "sneakers", demand=1)  # seen, never searched
    status = await external_status(session)
    assert set(status) == STATUS_KEYS
    assert status["provider"] == "serper" and status["enabled"] and status["key_configured"]
    assert (status["cost_per_query_usd"], status["free_queries"]) == (0.001, 2500)
    assert (status["today_used"], status["month_used"]) == (5, 5)
    assert (status["daily_max"], status["month_budget"], status["refresh_days"]) == (60, 900, 30)
    assert (status["models_cached"], status["models_due"], status["models_pending"]) == (2, 0, 1)
    assert status["prices"] == {"new": 4, "asking": 10, "sold": 6}  # the outlier is not counted
    assert status["rejected"]["kids"] == 3 and status["rejected"]["replica"] == 2
    assert status["rejected"]["lot"] == 2 and status["rejected"]["other_model"] == 4
    assert status["rejected"]["outlier"] == 1
    assert status["last_run"] == now.isoformat() and status["last_error"] is None
    # 3 models seen in the last 30 days x 2.5 queries per model (3 + 2) / 30-day cycle.
    assert status["expected_monthly_queries"] == 8
    assert KEY not in json.dumps(status)


async def test_external_status_when_off(session: Any) -> None:
    status = await external_status(session)
    assert set(status) == STATUS_KEYS
    assert (status["provider"], status["enabled"], status["key_configured"]) == ("none", False, False)
    assert status["prices"] == {"new": 0, "asking": 0, "sold": 0}
    assert status["rejected"] == {"kids": 0, "replica": 0, "lot": 0, "other_model": 0}
    assert (status["models_cached"], status["expected_monthly_queries"], status["last_run"]) == (0, 0, None)


# ---------------------------------------------------------------------------- CLI
def args(**kw: Any) -> argparse.Namespace:
    base = {
        "brand": None,
        "model": None,
        "dry_run": False,
        "due": False,
        "max_models": None,
        "status": False,
        "json": False,
    }
    return argparse.Namespace(**{**base, **kw})


async def test_cli_dry_run_stores_nothing_but_counts_queries(session: Any) -> None:
    fake = FakeSerper()
    run: Callable[..., Any] = cli.run
    code, report, text = await run(
        args(brand="Nike", model="air max 90", dry_run=True),
        provider=fake.provider(),
        settings=settings(),
        session=session,
    )
    assert code == 0 and report["model"] == "Air Max 90" and report["queries_used"] == 3
    assert report["kept"] == {"new": 4, "asking": 11, "sold": 6} and report["stored"] is None
    assert len(report["rejected_rows"]) == 15
    assert {"reason": "kids", "detail": "bambini"}.items() <= report["rejected_rows"][1].items()
    assert "prova: nulla salvato" in text and "bambini" in text and "venduto" in text
    assert text.startswith("Nike Air Max 90 - 3 query")
    assert [r["price_eur"] for r in report["kept_rows"] if r["outlier"]] == ["950.00"]
    assert (await session.execute(select(func.count()).select_from(ExternalPrice))).scalar() == 0
    assert (await session.execute(select(func.count()).select_from(ExternalSearch))).scalar() == 0
    budget = await get_state(session, BUDGET_KEY)
    assert budget is not None and budget["total_used"] == 3


async def test_cli_stores_one_model_and_reports_status(session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSerper()
    code, report, _ = await cli.run(
        args(brand="nike", model="Air Max 90"), provider=fake.provider(), settings=settings(), session=session
    )
    assert (
        code == 0 and report["stored"] == {"new": 4, "asking": 11, "sold": 6} and report["sold_synced"] == 6
    )
    row = await search_row(session, AM90)
    assert row.status == "ok" and row.queries_used == 3 and row.category_id is not None
    code, _, text = await cli.run(
        args(brand="unknown", model="x"), provider=fake.provider(), settings=settings(), session=session
    )
    assert code == 2 and "Marca sconosciuta" in text
    code, result, _ = await cli.run(
        args(brand="nike", model="Air Max 90"), settings=settings(serper_api_key=""), session=session
    )
    assert code == 2 and result["status"] == "disabled"
    code, status, _ = await cli.run(args(status=True), session=session)
    assert code == 0 and set(status) == STATUS_KEYS and status["prices"]["sold"] == 6
