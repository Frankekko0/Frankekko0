"""Request caps for the model provider: per-minute window, per-day counter in the quota time zone, cooldown."""

import pytest

from app.ai.limiter import AiRateLimiter
from app.core.config import Settings
from app.core.redis import get_redis


@pytest.fixture(autouse=True)
async def clean_keys() -> None:
    r = get_redis()
    for k in await r.keys("ff:ai:*"):
        await r.delete(k)


def limiter(**kw: object) -> AiRateLimiter:
    return AiRateLimiter(Settings(ai_provider="gemini", **kw))  # type: ignore[arg-type]


async def test_unlimited_never_touches_redis() -> None:
    lim = AiRateLimiter(Settings(ai_provider="anthropic"))
    assert (await lim.acquire("m", "strong")).allowed


async def test_per_minute_window() -> None:
    lim = limiter(ai_rpm_strong=2, ai_rpd_strong=0)
    assert (await lim.acquire("m", "strong")).allowed and (await lim.acquire("m", "strong")).allowed
    third = await lim.acquire("m", "strong")
    assert not third.allowed and third.reason == "rpm" and 1 <= third.retry_after <= 60
    assert (await lim.acquire("other", "strong")).allowed  # per model


async def test_per_day_cap_counts_admitted_calls_only() -> None:
    lim = limiter(ai_rpm_strong=0, ai_rpd_strong=3)
    for _ in range(3):
        assert (await lim.acquire("m", "strong")).allowed
    d = await lim.acquire("m", "strong")
    assert not d.allowed and d.reason == "rpd" and d.retry_after > 0
    snap = await lim.snapshot("m", "strong")
    assert snap["rpd_left"] == 0


async def test_cooldown_blocks_every_caller() -> None:
    lim = limiter(ai_rpm_cheap=100, ai_rpd_cheap=1000)
    await lim.penalize("m", 30)
    d = await lim.acquire("m", "cheap")
    assert not d.allowed and d.reason == "cooldown" and 1 <= d.retry_after <= 30


def test_day_resets_at_midnight_pacific() -> None:
    from datetime import UTC, datetime

    lim = limiter()
    day, left = lim._day(datetime(2026, 10, 10, 6, 59, tzinfo=UTC))  # 23:59 PDT the day before
    assert day == "2026-10-09" and 50 <= left <= 70
    day2, _ = lim._day(datetime(2026, 10, 10, 7, 1, tzinfo=UTC))
    assert day2 == "2026-10-10"


def test_gemini_defaults_are_cautious_and_anthropic_is_unlimited() -> None:
    assert Settings(ai_provider="gemini").ai_limits("strong") == (5, 100)
    assert Settings(ai_provider="anthropic").ai_limits("strong") == (0, 0)
    assert Settings(ai_provider="gemini", ai_rpm_strong=0).ai_limits("strong") == (0, 100)


async def test_peek_says_what_acquire_would_without_counting() -> None:
    lim = limiter(ai_rpm_strong=2, ai_rpd_strong=5)
    for _ in range(10):  # asking any number of times uses nothing up
        assert (await lim.peek("m", "strong")).allowed
    snap = await lim.snapshot("m", "strong")
    assert snap["rpm_left"] == 2 and snap["rpd_left"] == 5
    assert (await lim.acquire("m", "strong")).allowed and (await lim.acquire("m", "strong")).allowed
    seen = await lim.peek("m", "strong")
    assert not seen.allowed and seen.reason == "rpm" and 1 <= seen.retry_after <= 60
    again = await lim.snapshot("m", "strong")
    assert again["rpm_left"] == 0 and again["rpd_left"] == 3  # the refused peek was not counted either
    assert (await lim.peek("other", "strong")).allowed  # per model


async def test_peek_names_the_same_wait_as_the_refusal_it_predicts() -> None:
    lim = limiter(ai_rpm_strong=1, ai_rpd_strong=0)
    assert (await lim.acquire("m", "strong")).allowed
    seen, refused = await lim.peek("m", "strong"), await lim.acquire("m", "strong")
    assert (seen.allowed, seen.reason) == (False, "rpm") == (refused.allowed, refused.reason)
    assert abs(seen.retry_after - refused.retry_after) <= 1


async def test_peek_sees_the_day_cap_and_the_cooldown() -> None:
    day = limiter(ai_rpm_strong=0, ai_rpd_strong=1)
    assert (await day.acquire("m", "strong")).allowed
    seen = await day.peek("m", "strong")
    assert not seen.allowed and seen.reason == "rpd" and seen.retry_after > 0
    cool = limiter(ai_rpm_cheap=100, ai_rpd_cheap=1000)
    await cool.penalize("c", 30)
    held = await cool.peek("c", "cheap")
    assert not held.allowed and held.reason == "cooldown" and 1 <= held.retry_after <= 30


async def test_peek_of_an_unlimited_model_never_touches_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai import limiter as mod

    def boom() -> None:
        raise AssertionError("redis touched")

    monkeypatch.setattr(mod, "get_redis", boom)
    assert (await AiRateLimiter(Settings(ai_provider="anthropic")).peek("m", "strong")).allowed


async def test_peek_fails_closed_when_redis_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    from app.ai import limiter as mod

    class Down:
        async def ttl(self, key: str) -> int:
            raise RedisConnectionError("down")

    monkeypatch.setattr(mod, "get_redis", lambda: Down())
    seen = await limiter(ai_rpm_strong=2, ai_rpd_strong=0).peek("m", "strong")
    assert not seen.allowed and seen.reason == "redis"
