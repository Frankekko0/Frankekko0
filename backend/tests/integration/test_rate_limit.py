"""Rate limiting: at most ``limit`` requests in any 60 seconds, also across a minute boundary."""

import pytest

from app.core import rate_limit
from app.core.redis import get_redis


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1_800_000_000.0]  # a whole minute: 59.5 s is just before the boundary
    monkeypatch.setattr(rate_limit.time, "time", lambda: now[0])
    return now


async def test_a_burst_across_the_minute_boundary_is_still_limited(clock: list[float]) -> None:
    await get_redis().delete("ff:rl:t-boundary")
    clock[0] += 59.5
    first = [await rate_limit.hit("t-boundary", 10) for _ in range(6)]
    clock[0] += 1.0  # next minute: a fixed window would start again from zero here
    second = [await rate_limit.hit("t-boundary", 10) for _ in range(6)]
    allowed = [ok for ok, _, _ in first + second]
    assert allowed == [True] * 10 + [False] * 2
    # The oldest accepted request leaves the window 59 s from now: that is the wait.
    assert second[-1][2] == 59


async def test_requests_are_accepted_again_once_the_oldest_leave_the_window(clock: list[float]) -> None:
    await get_redis().delete("ff:rl:t-slide")
    for _ in range(3):
        assert (await rate_limit.hit("t-slide", 3))[0]
        clock[0] += 10
    assert not (await rate_limit.hit("t-slide", 3))[0]  # 3 in the last 30 s
    clock[0] += 31  # the first one is now 61 s old
    ok, count, _ = await rate_limit.hit("t-slide", 3)
    assert ok and count == 3
    # Refused requests are not stored: hammering does not extend the wait or grow the log.
    for _ in range(50):
        await rate_limit.hit("t-slide", 3)
    assert await get_redis().zcard("ff:rl:t-slide") == 3
