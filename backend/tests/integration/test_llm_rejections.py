"""A request the provider rejects is not an outage; a refused trial call gives the breaker's slot back; ``admission``
answers "would a call go through" without using anything up."""

from typing import Any

import anthropic
import httpx
import pytest

from app.ai import llm as llm_mod
from app.ai.breaker import CircuitBreaker
from app.ai.limiter import Admission
from app.ai.llm import AiDeferred, LLMClient
from app.core.redis import get_redis
from tests.integration.test_ai_budget import CONNECTION_ERROR, FakeAnthropic, client_with, text_response
from tests.integration.test_ai_budget import settings as ai_settings


def status_error(status: int, retry_after: str | None = None) -> anthropic.APIStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(
        status, request=httpx.Request("POST", "https://api.anthropic.com"), headers=headers
    )
    cls = {
        400: anthropic.BadRequestError,
        413: anthropic.APIStatusError,
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
        529: anthropic.APIStatusError,
    }[status]
    return cls("x", response=response, body=None)


class Clock:
    t = 0.0

    def __call__(self) -> float:
        return self.t


async def ask(c: LLMClient) -> dict[str, Any] | None:
    return await c.structured(system="s", content=[], schema={}, raise_on_defer=True)


@pytest.mark.parametrize("status", [400, 413])
async def test_a_request_the_provider_rejects_is_rejected_not_an_outage(clean_db: None, status: int) -> None:
    breaker = CircuitBreaker(failures=1, cooldown=30)  # one real failure would open it
    c = client_with(FakeAnthropic(status_error(status)), ai_settings(), breaker=breaker)
    with pytest.raises(AiDeferred) as e:
        await ask(c)
    assert e.value.reason == "rejected" and e.value.retry_after >= 3600  # asking again cannot help
    assert breaker.state == "closed"  # a wrong request says nothing about the provider being up


@pytest.mark.parametrize("status", [500, 529])
async def test_a_server_error_is_still_an_outage(clean_db: None, status: int) -> None:
    breaker = CircuitBreaker(failures=1, cooldown=30)
    c = client_with(FakeAnthropic(status_error(status)), ai_settings(), breaker=breaker)
    with pytest.raises(AiDeferred) as e:
        await ask(c)
    assert e.value.reason == "api_error" and breaker.state == "open"


@pytest.mark.parametrize("status", [400, 429])
async def test_a_trial_call_that_ends_without_a_verdict_gives_the_half_open_slot_back(
    clean_db: None, status: int
) -> None:
    clock = Clock()
    fake = FakeAnthropic(
        CONNECTION_ERROR, CONNECTION_ERROR, status_error(status), text_response({"ok": True})
    )
    c = client_with(fake, ai_settings(), breaker=CircuitBreaker(failures=2, cooldown=60, clock=clock))
    for _ in range(2):  # two connection errors open the breaker
        with pytest.raises(AiDeferred):
            await ask(c)
    assert c.breaker.state == "open"
    clock.t = 61
    assert c.breaker.state == "half_open"
    with pytest.raises(AiDeferred) as e:  # the trial call: a 4xx / a 429, neither a success nor an outage
        await ask(c)
    assert e.value.reason in ("rejected", "rate_limited") and len(fake.calls) == 3
    # The slot is back: the next call is a trial again and, the provider being healthy, it closes the breaker.
    # (Without the fix every later call in this process is refused with "breaker_open" for good.)
    clock.t = 62
    assert await ask(c) == {"ok": True}
    assert len(fake.calls) == 4 and c.breaker.state == "closed"
    await get_redis().delete(f"ff:ai:cooldown:{c.model_for('strong')}")  # the 429 left a shared cooldown


# ------------------------------------------------------------------ admission: would a call go through?
class Limiter:
    def __init__(self, adm: Admission) -> None:
        self.adm = adm
        self.peeks = 0
        self.acquired = 0

    async def peek(self, model: str, tier: str) -> Admission:
        self.peeks += 1
        return self.adm

    async def acquire(self, model: str, tier: str) -> Admission:
        self.acquired += 1
        return self.adm


async def test_admission_reports_a_closed_cap_without_taking_a_place(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    lim = Limiter(Admission(False, 9.0, "rpd"))
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: lim)
    got = await client_with(FakeAnthropic(), ai_settings()).admission("strong", "vision")
    assert (got.allowed, got.reason, got.retry_after) == (False, "rpd", 9.0)
    assert (lim.peeks, lim.acquired) == (1, 0)


async def test_admission_sees_an_open_breaker_but_does_not_take_the_trial_slot(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    lim = Limiter(Admission(True))
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: lim)
    clock = Clock()
    c = client_with(
        FakeAnthropic(), ai_settings(), breaker=CircuitBreaker(failures=1, cooldown=60, clock=clock)
    )
    assert (await c.admission()).allowed
    c.breaker.failure()
    held = await c.admission()
    assert (held.allowed, held.reason, held.retry_after) == (False, "breaker_open", 60.0)
    clock.t = 61  # half-open: a trial call would go through, and asking must not use that call up
    for _ in range(3):
        assert (await c.admission()).allowed
    assert c.breaker.allow() and not c.breaker.allow()  # the one trial slot is still there to be taken


async def test_admission_sees_the_spend_budget(clean_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "get_limiter", lambda: Limiter(Admission(True)))
    c = client_with(FakeAnthropic(), ai_settings(ai_daily_budget_usd=0))
    got = await c.admission("strong", "vision")
    assert (got.allowed, got.reason) == (False, "budget")
