"""The strong model's review, through the API: what waits and what the limiter allows, and the manual request."""

import uuid
from datetime import timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import pytest
from sqlalchemy import text, update

from app.ai import llm as llm_mod
from app.ai import service
from app.ai.budget import AiBudget
from app.ai.limiter import AiRateLimiter
from app.ai.llm import LLMClient
from app.db.models import Listing, Opportunity
from app.db.session import session_scope
from tests.api.test_api import API
from tests.conftest import NOW
from tests.integration.test_agent import seed
from tests.integration.test_ai_budget import FakeAnthropic, text_response
from tests.integration.test_ai_budget import settings as ai_settings
from tests.integration.test_ai_queue import answer, reanalyse, row


async def test_usage_shows_the_request_caps_and_the_queue(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    async with session_scope() as s:
        await s.execute(
            update(Opportunity).values(flip_score=90)
        )  # the threshold of the tests is the default 80
        await s.execute(
            update(Opportunity)
            .where(Opportunity.id == uuid.UUID(ids["buy"]))
            .values(ai_attempts=8, ai_last_error="bad_answer")
        )
    body = (await auth_client.get(f"{API}/ai/usage")).json()
    strong = body["limits"]["strong"]
    assert strong["model"] == body["models"]["strong"] and "rpm_left" in strong and "cooldown_s" in strong
    assert body["limits"]["cheap"]["model"] == body["models"]["cheap"]
    queue = body["analyst_queue"]
    assert queue["min_flip_score"] == 80.0
    assert queue["pending"] == len(ids) and queue["exhausted"] == 1 and queue["reviewed"] == 0
    assert queue["ready"] == len(ids) - 1 and queue["deferred"] == 0
    assert queue["last_errors"] == {"bad_answer": 1}


def model(monkeypatch: pytest.MonkeyPatch, **cfg_kw: Any) -> tuple[LLMClient, FakeAnthropic, AiRateLimiter]:
    cfg = ai_settings(**cfg_kw)
    fake = FakeAnthropic(text_response(answer("CONSIDER")))
    llm = LLMClient(cfg, budget=AiBudget(cfg))
    llm._client = fake  # type: ignore[assignment]
    limiter = AiRateLimiter(cfg)
    monkeypatch.setattr(service, "get_llm", lambda: llm)
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: limiter)
    return llm, fake, limiter


async def test_the_manual_analysis_says_when_the_model_cannot_answer_and_stores_nothing(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await seed(make_listing)
    llm, fake, limiter = model(monkeypatch, ai_rpm_strong=1, ai_rpd_strong=0)
    before = await row(ids["strong"])

    assert (
        await limiter.acquire(llm.model_for("strong"), "strong")
    ).allowed  # this minute's only request is gone
    denied = await auth_client.post(f"{API}/opportunities/{ids['strong']}/ai-analysis")
    assert denied.status_code == 429 and 1 <= int(denied.headers["retry-after"]) <= 60
    assert "limite di richieste" in denied.json()["error"]["message"] and fake.calls == []
    after = await row(ids["strong"])
    assert (
        after.ai_provider == "rules" and after.ai_analysis == before.ai_analysis
    )  # not the rules' text, "fresh"
    assert after.ai_analyzed_at == before.ai_analyzed_at


async def test_the_manual_analysis_stores_the_review_for_the_current_analysis(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await seed(make_listing)
    _, fake, _ = model(monkeypatch)
    ok = await auth_client.post(f"{API}/opportunities/{ids['strong']}/ai-analysis")
    assert ok.status_code == 200 and ok.json()["provider"] == "claude" and ok.json()["verdict"] == "CONSIDER"
    assert len(fake.calls) == 1
    stored = await row(ids["strong"])
    assert stored.ai_provider == "claude" and stored.ai_for_analysis_id == stored.analysis_id
    assert stored.decision_verdict == "WATCHLIST"  # the analyst's caution, on record


async def test_the_manual_analysis_is_refused_with_a_conflict_when_the_listing_was_reanalysed_meanwhile(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await seed(make_listing)
    _, fake, _ = model(monkeypatch)
    original = fake._create
    before = await row(ids["strong"])

    async def reanalysed_meanwhile(**kwargs: Any) -> Any:
        async with session_scope() as s:
            await s.execute(
                update(Listing).where(Listing.id == before.listing_id).values(price=Listing.price - D("2"))
            )
        await reanalyse(before.listing_id, NOW + timedelta(hours=1))
        return await original(**kwargs)

    fake.beta.messages.create = reanalysed_meanwhile  # type: ignore[assignment]
    conflict = await auth_client.post(f"{API}/opportunities/{ids['strong']}/ai-analysis")
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "analysis_changed"
    assert "Riprova" in conflict.json()["error"]["message"]
    after = await row(ids["strong"])
    assert after.analysis_id != before.analysis_id
    assert (
        after.ai_provider == "rules" and after.ai_for_analysis_id is None
    )  # nothing of the old analysis stored
    assert after.decision_verdict == "STRONG_BUY" and "reviewed_from" not in after.decision


async def test_the_manual_analysis_holds_no_connection_while_the_model_answers(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await seed(make_listing)
    _, fake, _ = model(monkeypatch)
    original = fake._create
    seen: list[int] = []

    async def probing(**kwargs: Any) -> Any:
        async with session_scope() as s:  # sessions open and not idle: the request's own would show up here
            busy = await s.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE state <> 'idle' AND pid <> pg_backend_pid()"
                    " AND datname = current_database()"
                )
            )
            seen.append(int(busy.scalar_one()))
        return await original(**kwargs)

    fake.beta.messages.create = probing  # type: ignore[assignment]
    ok = await auth_client.post(f"{API}/opportunities/{ids['strong']}/ai-analysis")
    assert ok.status_code == 200 and seen == [0]


async def test_the_manual_analysis_of_an_unknown_opportunity_is_a_404_and_calls_nothing(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(make_listing)
    _, fake, _ = model(monkeypatch)
    missing = await auth_client.post(f"{API}/opportunities/{uuid.uuid4()}/ai-analysis")
    assert missing.status_code == 404 and fake.calls == []
