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
