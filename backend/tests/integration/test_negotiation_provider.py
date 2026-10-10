"""The negotiation writer through the real model clients (Gemini over a fake HTTP server, Anthropic over a scripted
SDK): the request that leaves the app, and how a refused or failed call reaches the user as a template with a reason."""

import json
from typing import Any

import httpx
import pytest

from app.ai import gemini
from app.ai.budget import AiBudget
from app.ai.gemini import GeminiClient
from app.ai.limiter import AiRateLimiter
from app.ai.llm import LLMClient
from app.core.config import Settings
from app.negotiation import writer
from tests.integration.test_ai_budget import FakeAnthropic, client_with, text_response
from tests.integration.test_ai_budget import settings as anthropic_settings
from tests.unit.test_negotiation_guard import GOOD, facts_for, plan_for
from tests.unit.test_negotiation_writer import TITLE


def serve(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real = httpx.AsyncClient
    monkeypatch.setattr(
        gemini.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )


def gemini_client(**kw: Any) -> GeminiClient:
    cfg = Settings(ai_provider="gemini", ai_api_key="k-test", ai_model_cheap="gemini-lite", **kw)
    return GeminiClient(cfg, budget=AiBudget(cfg))


async def test_gemini_gets_one_request_without_amounts_and_its_answer_is_checked(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append({"url": str(req.url), "body": json.loads(req.content)})
        part = {"text": json.dumps(GOOD)}
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [part]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 120},
            },
        )

    serve(monkeypatch, handler)
    plan = plan_for()
    result = await writer.write_messages(gemini_client(), plan, facts_for(plan), TITLE, ref="opp")
    assert result.accepted == 5 and result.fallback is None and "20 €" in result.messages["first_offer"]

    (call,) = seen
    assert "gemini-lite:generateContent" in call["url"]  # the cheap tier
    body = call["body"]
    assert body["generationConfig"]["maxOutputTokens"] >= 2048
    assert set(body["generationConfig"]["responseSchema"]["properties"]) == set(GOOD)
    user_text = " ".join(p["text"] for p in body["contents"][0]["parts"])
    assert "annuncio_non_fidato" in user_text and "{ITEM}" in body["systemInstruction"]["parts"][0]["text"]
    facts_part = body["contents"][0]["parts"][0]["text"]
    assert not any(c.isdigit() for c in facts_part) and "€" not in facts_part


async def test_a_gemini_quota_error_comes_back_as_a_rate_limit_with_the_wait_and_the_templates(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, lambda req: httpx.Response(429, json={"error": {"details": [{"retryDelay": "12s"}]}}))
    plan = plan_for()
    client = gemini_client()
    result = await writer.write_messages(client, plan, facts_for(plan), TITLE)
    assert result.fallback == "rate_limited" and result.retry_after == 12.0
    assert result.messages == plan.messages and result.accepted == 0
    assert client.breaker.allow()  # a quota error is not an outage


async def test_the_shared_request_caps_defer_the_negotiation_call_too(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai import gemini as gemini_mod
    from app.ai import llm as llm_mod

    cfg = Settings(
        ai_provider="gemini", ai_api_key="k", ai_model_cheap="gemini-lite", ai_rpm_cheap=1, ai_rpd_cheap=0
    )
    limiter = AiRateLimiter(cfg)
    monkeypatch.setattr(gemini_mod, "get_limiter", lambda: limiter)
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: limiter)
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        part = {"text": json.dumps(GOOD)}
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [part]}, "finishReason": "STOP"}]}
        )

    serve(monkeypatch, handler)
    client = GeminiClient(cfg, budget=AiBudget(cfg))
    plan = plan_for()
    first = await writer.write_messages(client, plan, facts_for(plan), TITLE)
    second = await writer.write_messages(client, plan, facts_for(plan), TITLE)
    assert first.accepted == 5 and second.fallback == "rate_limited" and second.retry_after
    assert second.messages == plan.messages and len(calls) == 1  # the second one never left the app


async def test_anthropic_gets_the_same_request_through_the_sdk_and_a_truncated_answer_is_no_answer(
    clean_db: None,
) -> None:
    cfg = anthropic_settings()
    fake = FakeAnthropic(text_response(GOOD, model="claude-haiku-5-5"))
    plan = plan_for()
    result = await writer.write_messages(client_with(fake, cfg), plan, facts_for(plan), TITLE)
    assert result.accepted == 5
    (call,) = fake.calls
    assert call["model"] == cfg.ai_model_cheap and call["max_tokens"] >= 2048
    schema = call["output_config"]["format"]["schema"]
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(GOOD)
    blocks = call["messages"][0]["content"]
    assert not any(c.isdigit() for c in blocks[0]["text"]) and "annuncio_non_fidato" in blocks[1]["text"]

    truncated = text_response(GOOD, model="claude-haiku-5-5")
    truncated.stop_reason = "max_tokens"
    llm: LLMClient = client_with(FakeAnthropic(truncated), cfg)
    again = await writer.write_messages(llm, plan, facts_for(plan), TITLE)
    assert again.fallback == "no_answer" and again.messages == plan.messages
