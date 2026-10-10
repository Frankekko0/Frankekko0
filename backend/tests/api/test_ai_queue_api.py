"""The strong model's review, through the API: what waits and what the limiter allows, and the manual request."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import update

from app.ai import llm as llm_mod
from app.ai import service
from app.ai.budget import AiBudget
from app.ai.limiter import AiRateLimiter
from app.ai.llm import LLMClient
from app.db.models import Opportunity
from app.db.session import session_scope
from tests.api.test_api import API
from tests.integration.test_agent import seed
from tests.integration.test_ai_budget import FakeAnthropic, text_response
from tests.integration.test_ai_budget import settings as ai_settings
from tests.integration.test_ai_queue import answer, row


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
