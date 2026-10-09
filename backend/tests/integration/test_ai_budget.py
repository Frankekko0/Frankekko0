"""AI spend is counted and capped; the model client obeys the cap and the circuit breaker."""

import json
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from sqlalchemy import select, text

from app.ai.breaker import CircuitBreaker
from app.ai.budget import AiBudget
from app.ai.llm import LLMClient
from app.core.config import Settings
from app.db.models import AiUsage, Event
from app.db.session import session_scope
from tests.conftest import NOW


def settings(**kw: Any) -> Settings:
    return Settings(ai_api_key="test-key", **kw)  # type: ignore[arg-type]


def text_response(
    payload: dict[str, Any], tokens: tuple[int, int] = (1000, 500), model: str = "claude-sonnet-5-5"
) -> Any:
    return SimpleNamespace(
        stop_reason="end_turn",
        stop_details=None,
        model=model,
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


class FakeAnthropic:
    """Stands in for ``anthropic.AsyncAnthropic``: scripted answers, counts the calls."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def client_with(fake: FakeAnthropic, cfg: Settings, **kw: Any) -> LLMClient:
    c = LLMClient(cfg, budget=kw.pop("budget", AiBudget(cfg)), breaker=kw.pop("breaker", None))
    c._client = fake  # type: ignore[assignment]
    return c


CONNECTION_ERROR = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))


# ------------------------------------------------------------------ the budget
async def test_a_call_is_recorded_with_its_cost(clean_db: None) -> None:
    cfg = settings()
    b = AiBudget(cfg)
    cost = await b.record(
        purpose="deal_analysis",
        model="claude-sonnet-5-5",
        tier="strong",
        input_tokens=10_000,
        output_tokens=2_000,
        ref="x",
        now=NOW,
    )
    assert cost == D("0.060000")  # 10k x $3/M + 2k x $15/M
    cheap = await b.record(
        purpose="triage",
        model="claude-haiku-5-5",
        tier="cheap",
        input_tokens=10_000,
        output_tokens=2_000,
        now=NOW,
    )
    assert cheap == D("0.020000")  # 10k x $1/M + 2k x $5/M
    st = await b.status(NOW)
    assert st.day_spent == D("0.08") and st.month_spent == D("0.08") and st.calls_today == 2
    assert st.by_purpose == {"deal_analysis": D("0.06"), "triage": D("0.02")}


async def test_the_daily_cap_stops_spending_and_is_logged_once(clean_db: None) -> None:
    cfg = settings(ai_daily_budget_usd=D("0.10"), ai_call_reserve_usd=D("0.02"))
    b = AiBudget(cfg)
    assert (await b.check("x", NOW)).allowed
    await b.record(
        purpose="x", model="m", tier="strong", input_tokens=20_000, output_tokens=2_000, now=NOW
    )  # 0.09
    assert not (await b.check("x", NOW)).allowed  # 0.01 left < the 0.02 reserve
    refused = await b.check("x", NOW)
    assert not refused.allowed and "giornaliero" in (refused.reason or "")
    async with session_scope() as s:
        events = (await s.execute(select(Event).where(Event.kind == "ai.budget_stop"))).scalars().all()
    assert len(events) == 1 and events[0].payload["cap"] == "giornaliero"


async def test_the_monthly_cap_holds_even_when_the_day_is_fresh(clean_db: None) -> None:
    cfg = settings(
        ai_daily_budget_usd=D("5"), ai_monthly_budget_usd=D("0.095"), ai_call_reserve_usd=D("0.01")
    )
    b = AiBudget(cfg)
    earlier, later = NOW, NOW + timedelta(days=10)  # same month, different day
    await b.record(
        purpose="x", model="m", tier="strong", input_tokens=30_000, output_tokens=0, now=earlier
    )  # 0.09
    check = await b.check("x", later)
    assert not check.allowed and "mensile" in (check.reason or "")


async def test_a_zero_cap_switches_paid_calls_off(clean_db: None) -> None:
    assert not (await AiBudget(settings(ai_daily_budget_usd=D("0"))).check("x", NOW)).allowed


async def test_an_unreadable_budget_refuses_the_call(clean_db: None) -> None:
    def broken() -> Any:
        raise RuntimeError("database down")

    check = await AiBudget(settings(), scope=broken).check("x")  # type: ignore[arg-type]
    assert not check.allowed and "illeggibile" in (check.reason or "")


# ------------------------------------------------------------------ the model client
async def test_a_structured_call_is_paid_for_and_recorded(clean_db: None) -> None:
    cfg = settings()
    fake = FakeAnthropic(text_response({"ok": True}))
    out = await client_with(fake, cfg).structured(
        system="s", content=[], schema={}, purpose="deal_analysis", ref="L1"
    )
    assert out == {"ok": True} and len(fake.calls) == 1
    assert fake.calls[0]["model"] == cfg.ai_model
    async with session_scope() as s:
        row = (await s.execute(select(AiUsage))).scalar_one()
    assert (row.purpose, row.tier, row.ref, row.input_tokens, row.output_tokens) == (
        "deal_analysis",
        "strong",
        "L1",
        1000,
        500,
    )
    assert row.cost_usd == D("0.010500")


async def test_the_cheap_tier_uses_the_cheap_model_and_price(clean_db: None) -> None:
    cfg = settings()
    fake = FakeAnthropic(text_response({"ok": 1}, model="claude-haiku-5-5"))
    await client_with(fake, cfg).structured(system="s", content=[], schema={}, tier="cheap")
    assert fake.calls[0]["model"] == cfg.ai_model_cheap
    assert fake.calls[0]["output_config"]["effort"] == "low"
    async with session_scope() as s:
        row = (await s.execute(select(AiUsage))).scalar_one()
    assert row.tier == "cheap" and row.cost_usd == D("0.003500")


async def test_when_the_cap_is_reached_the_model_is_not_called(clean_db: None) -> None:
    cfg = settings(ai_daily_budget_usd=D("0.05"), ai_call_reserve_usd=D("0.06"))
    fake = FakeAnthropic(text_response({"ok": True}))
    out = await client_with(fake, cfg).structured(system="s", content=[], schema={})
    assert out is None and fake.calls == []  # stopped before spending, not after


async def test_spending_stops_automatically_once_the_cap_is_crossed(clean_db: None) -> None:
    cfg = settings(ai_daily_budget_usd=D("0.03"), ai_call_reserve_usd=D("0.005"))
    fake = FakeAnthropic(text_response({"ok": True}, tokens=(5_000, 1_000)))  # 0.03 per call
    c = client_with(fake, cfg)
    assert await c.structured(system="s", content=[], schema={}) == {"ok": True}
    assert await c.structured(system="s", content=[], schema={}) is None
    assert len(fake.calls) == 1


async def test_a_provider_that_keeps_failing_is_left_alone_then_retried(clean_db: None) -> None:
    class Clock:
        t = 0.0

        def __call__(self) -> float:
            return self.t

    clock = Clock()
    cfg = settings()
    fake = FakeAnthropic(CONNECTION_ERROR, CONNECTION_ERROR, text_response({"ok": True}))
    c = client_with(fake, cfg, breaker=CircuitBreaker(failures=2, cooldown=30, clock=clock))
    assert await c.structured(system="s", content=[], schema={}) is None
    assert await c.structured(system="s", content=[], schema={}) is None
    assert len(fake.calls) == 2 and c.breaker.state == "open"
    assert await c.structured(system="s", content=[], schema={}) is None
    assert len(fake.calls) == 2  # open: not even tried
    clock.t = 31
    assert await c.structured(system="s", content=[], schema={}) == {"ok": True}  # the trial worked
    assert c.breaker.state == "closed"


async def test_failed_calls_cost_nothing(clean_db: None) -> None:
    fake = FakeAnthropic(CONNECTION_ERROR)
    assert await client_with(fake, settings()).structured(system="s", content=[], schema={}) is None
    async with session_scope() as s:
        assert (await s.execute(text("SELECT count(*) FROM ai_usage"))).scalar_one() == 0


async def test_a_disabled_client_never_touches_the_budget(clean_db: None) -> None:
    c = LLMClient(Settings(ai_api_key=None))
    assert not c.enabled
    assert await c.structured(system="s", content=[], schema={}) is None
    assert await c.converse(system="s", messages=[], tools=[]) is None


async def test_a_tool_turn_returns_the_calls_to_make(clean_db: None) -> None:
    answer = SimpleNamespace(
        stop_reason="tool_use",
        model="claude-haiku-5-5",
        content=[
            SimpleNamespace(type="text", text="Controllo i numeri."),
            SimpleNamespace(type="tool_use", id="tu_1", name="finance_calc", input={"purchase_price": 20}),
        ],
        usage=SimpleNamespace(input_tokens=800, output_tokens=120),
    )
    fake = FakeAnthropic(answer)
    turn = await client_with(fake, settings()).converse(
        system="s", messages=[{"role": "user", "content": "x"}], tools=[{"name": "finance_calc"}]
    )
    assert turn is not None and turn.stop_reason == "tool_use"
    assert [(t.id, t.name, t.input) for t in turn.tool_calls] == [
        ("tu_1", "finance_calc", {"purchase_price": 20})
    ]
    assert turn.blocks[1] == {
        "type": "tool_use",
        "id": "tu_1",
        "name": "finance_calc",
        "input": {"purchase_price": 20},
    }
    assert turn.cost_usd == D("0.0001") * 0 + turn.cost_usd and turn.cost_usd > 0
    assert (
        fake.calls[0]["tools"] == [{"name": "finance_calc"}]
        and fake.calls[0]["model"] == settings().ai_model_cheap
    )


@pytest.mark.parametrize("field", ["ai_daily_budget_usd", "ai_monthly_budget_usd"])
def test_the_defaults_are_conservative(field: str) -> None:
    assert getattr(Settings(), field) <= D("20")
