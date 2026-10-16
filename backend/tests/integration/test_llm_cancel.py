"""A call that ends without a verdict (cancelled by the job's timeout, or an unexpected error) gives back the breaker's
half-open trial slot, for both providers. Without it one such call leaves the breaker half-open for good: every later
call in the process is refused with "breaker_open" and the provider is never asked again until a restart."""

import asyncio
from typing import Any

import httpx
import pytest

from app.ai import gemini
from app.ai.breaker import CircuitBreaker
from app.ai.budget import AiBudget
from app.ai.gemini import GeminiClient
from app.ai.llm import AiDeferred, LLMClient
from app.core.config import Settings
from tests.integration.test_ai_budget import CONNECTION_ERROR, FakeAnthropic, client_with, text_response
from tests.integration.test_ai_budget import settings as ai_settings
from tests.integration.test_llm_rejections import Clock, ask

ENDINGS = [asyncio.CancelledError(), RuntimeError("unexpected")]


class Scripted(FakeAnthropic):
    """Like ``FakeAnthropic`` but it can also raise what is not an ``Exception`` (a cancellation)."""

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.mark.parametrize("ending", ENDINGS, ids=["cancelled", "unexpected"])
async def test_anthropic_trial_call_cancelled_or_crashed_gives_the_half_open_slot_back(
    clean_db: None, ending: BaseException
) -> None:
    clock = Clock()
    fake = Scripted(CONNECTION_ERROR, CONNECTION_ERROR, ending, text_response({"ok": True}))
    c: LLMClient = client_with(
        fake, ai_settings(), breaker=CircuitBreaker(failures=2, cooldown=60, clock=clock)
    )
    for _ in range(2):  # two connection errors open the breaker
        with pytest.raises(AiDeferred):
            await ask(c)
    clock.t = 61
    assert c.breaker.state == "half_open"
    with pytest.raises(type(ending)):  # the trial call is cut short: no verdict
        await ask(c)
    clock.t = 62
    assert await ask(c) == {"ok": True}  # the slot is back: a new trial goes out and closes the breaker
    assert len(fake.calls) == 4 and c.breaker.state == "closed"


@pytest.mark.parametrize("ending", ENDINGS, ids=["cancelled", "unexpected"])
async def test_gemini_trial_call_cancelled_or_crashed_gives_the_half_open_slot_back(
    clean_db: None, monkeypatch: pytest.MonkeyPatch, ending: BaseException
) -> None:
    calls: list[int] = []
    good = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}, "finishReason": "STOP"}]}

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) <= 2:
            return httpx.Response(500, json={})
        if len(calls) == 3:
            raise ending
        return httpx.Response(200, json=good)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        gemini.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    clock = Clock()
    cfg = Settings(ai_provider="gemini", ai_api_key="k-test", ai_rpm_strong=0, ai_rpd_strong=0)
    c = GeminiClient(cfg, budget=AiBudget(cfg), breaker=CircuitBreaker(failures=2, cooldown=60, clock=clock))
    for _ in range(2):
        with pytest.raises(AiDeferred):
            await ask(c)
    clock.t = 61
    assert c.breaker.state == "half_open"
    with pytest.raises(type(ending)):
        await ask(c)
    clock.t = 62
    assert await ask(c) == {"ok": True}
    assert len(calls) == 4 and c.breaker.state == "closed"
