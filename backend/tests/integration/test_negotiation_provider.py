"""The negotiation writer through the real model clients (Gemini over a fake HTTP server, Anthropic over a scripted
SDK): the request that leaves the app, and how a refused or failed call reaches the user as a template with a reason."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.ai import gemini
from app.ai.budget import AiBudget
from app.ai.gemini import GeminiClient
from app.ai.limiter import AiRateLimiter
from app.ai.llm import LLMClient
from app.core.config import Settings
from app.db.session import session_scope
from app.negotiation import drafts, writer
from app.negotiation.quota import NegotiationQuota
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


# ---- the user's click, through the real Gemini client and the real quota in Redis --------------------------------
def stop(text: str, reason: str = "STOP") -> httpx.Response:
    part = {"text": text}
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [part]}, "finishReason": reason}]})


# A request that LEFT the app and came back unusable or failed: it used the shared request budget
SENT = {
    "max_tokens": lambda: stop('{"first_offer": "Ciao', "MAX_TOKENS"),
    "not_json": lambda: stop("Certo! Ecco i messaggi richiesti."),
    "json_but_not_an_object": lambda: stop("[1, 2]"),
    "safety": lambda: httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]}),
    "prompt_blocked": lambda: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
    "rejected_400": lambda: httpx.Response(400, json={"error": {"message": "bad request"}}),
    "forbidden_403": lambda: httpx.Response(403, json={"error": {"message": "key not allowed"}}),
    "server_error_500": lambda: httpx.Response(500, json={"error": {"message": "boom"}}),
}


def negotiation_settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        ai_provider="gemini", ai_api_key="k-test", ai_model_cheap="gemini-lite", negotiation_ai_enabled=True
    )
    return Settings(**{**base, **kw})


async def click(
    cfg: Settings, client: GeminiClient, user: uuid.UUID, opp: uuid.UUID, *, tone: str = "polite"
) -> Any:
    """One 'Write with AI' click: the plan as the endpoint builds it, then ``drafts.draft`` with the real quota."""
    plan = plan_for()
    async with session_scope() as db:
        await drafts.draft(
            db, user, opp, plan, facts_for(plan), TITLE, client,
            tone=tone, regenerate=True, settings=cfg, quota=NegotiationQuota(cfg),
        )  # fmt: skip
    assert plan.messages == plan_for().messages  # the templates, whatever happened
    return plan.ai


@pytest.mark.parametrize("answer", SENT.values(), ids=SENT.keys())
async def test_a_request_that_was_sent_keeps_its_cooldown_even_when_the_answer_is_unusable(
    clean_db: None, monkeypatch: pytest.MonkeyPatch, answer: Callable[[], httpx.Response]
) -> None:
    requests: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(1)
        return answer()

    serve(monkeypatch, handler)
    cfg = negotiation_settings(negotiation_ai_cooldown_seconds=60, negotiation_ai_max_calls_per_day=0)
    client = GeminiClient(cfg, budget=AiBudget(cfg))
    user, opp = uuid.uuid4(), uuid.uuid4()
    outcomes = [(await click(cfg, client, user, opp))["fallback"] for _ in range(5)]
    assert len(requests) == 1, outcomes  # the cooldown held: one request left, four clicks were stopped
    assert outcomes[0] in ("no_answer", "unavailable") and outcomes[1:] == ["cooldown"] * 4


@pytest.mark.parametrize("answer", SENT.values(), ids=SENT.keys())
async def test_a_request_that_was_sent_keeps_its_place_in_the_daily_count(
    clean_db: None, monkeypatch: pytest.MonkeyPatch, answer: Callable[[], httpx.Response]
) -> None:
    requests: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(1)
        return answer()

    serve(monkeypatch, handler)
    cfg = negotiation_settings(negotiation_ai_cooldown_seconds=0, negotiation_ai_max_calls_per_day=2)
    client = GeminiClient(cfg, budget=AiBudget(cfg))
    user = uuid.uuid4()
    outcomes = [(await click(cfg, client, user, uuid.uuid4()))["fallback"] for _ in range(5)]
    assert len(requests) == 2, outcomes  # the cap of 2 held
    assert outcomes[2:] == ["daily_cap"] * 3
    # another user has their own count
    other = await click(cfg, client, uuid.uuid4(), uuid.uuid4())
    assert other["fallback"] != "daily_cap" and len(requests) == 3


async def test_what_the_model_could_not_use_is_told_apart_from_a_model_that_is_down(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = negotiation_settings(negotiation_ai_cooldown_seconds=0, negotiation_ai_max_calls_per_day=0)
    seen: dict[str, str] = {}
    current: list[Callable[[], httpx.Response]] = []
    serve(monkeypatch, lambda req: current[0]())
    for name, answer in SENT.items():
        current[:] = [answer]
        client = GeminiClient(cfg, budget=AiBudget(cfg))
        seen[name] = (await click(cfg, client, uuid.uuid4(), uuid.uuid4()))["fallback"]
    assert {
        seen[k] for k in ("max_tokens", "not_json", "json_but_not_an_object", "safety", "prompt_blocked")
    } == {"no_answer"}
    assert {seen[k] for k in ("rejected_400", "forbidden_403", "server_error_500")} == {"unavailable"}


async def test_a_request_that_never_left_gives_the_click_back(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai import gemini as gemini_mod
    from app.ai import llm as llm_mod

    requests: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(1)
        return httpx.Response(429, json={"error": {"details": [{"retryDelay": "40s"}]}})

    serve(monkeypatch, handler)
    cfg = negotiation_settings(
        negotiation_ai_cooldown_seconds=60,
        negotiation_ai_max_calls_per_day=2,
        ai_rpm_cheap=100,
        ai_rpd_cheap=1000,
    )
    limiter = AiRateLimiter(cfg)
    monkeypatch.setattr(gemini_mod, "get_limiter", lambda: limiter)
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: limiter)
    client = GeminiClient(cfg, budget=AiBudget(cfg))
    user, opp = uuid.uuid4(), uuid.uuid4()
    # the provider says 429 once and the limiter then holds the model back: nothing else leaves the app, and no
    # click was spent (neither the cooldown nor the daily count), so the user can write again as soon as it is free
    answers = [await click(cfg, client, user, opp) for _ in range(4)]
    assert len(requests) == 1
    assert [a["fallback"] for a in answers] == ["rate_limited"] * 4
    assert answers[0]["retry_after"] == 40 and 1 <= answers[1]["retry_after"] <= 40
    quota = NegotiationQuota(cfg)
    assert (await quota.reserve(user, opp)).allowed  # no cooldown left behind
    assert (await quota.reserve(user, uuid.uuid4())).allowed  # and the daily count is at 1 of 2, not 5


async def test_an_open_breaker_gives_the_click_back(clean_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(1)
        return stop(json.dumps(GOOD))

    serve(monkeypatch, handler)
    cfg = negotiation_settings(negotiation_ai_cooldown_seconds=60, negotiation_ai_max_calls_per_day=0)
    client = GeminiClient(cfg, budget=AiBudget(cfg))
    for _ in range(client.breaker.threshold):
        client.breaker.failure()
    assert not client.breaker.allow()
    user, opp = uuid.uuid4(), uuid.uuid4()
    first = await click(cfg, client, user, opp)
    assert first["fallback"] == "unavailable" and requests == []
    assert (await NegotiationQuota(cfg).reserve(user, opp)).allowed


async def test_the_negotiation_request_caps_the_models_thinking_so_the_answer_is_not_cut_off(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return stop(json.dumps(GOOD))

    serve(monkeypatch, handler)
    for model, expected in (("gemini-2.5-flash-lite", {"thinkingBudget": 512}), ("gemini-lite", None)):
        cfg = negotiation_settings(ai_model_cheap=model)
        plan = plan_for()
        await writer.write_messages(GeminiClient(cfg, budget=AiBudget(cfg)), plan, facts_for(plan), TITLE)
        assert bodies[-1]["generationConfig"].get("thinkingConfig") == expected, model
    assert writer.MAX_TOKENS - 512 >= 1024  # room left for five short messages
