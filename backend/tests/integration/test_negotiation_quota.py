"""How often the model may be asked to write negotiation messages: a cooldown per opportunity, a cap per user per day."""

import uuid
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import get_settings
from app.negotiation import quota as quota_mod
from app.negotiation.quota import NegotiationQuota

U1, U2, O1, O2 = (uuid.uuid4() for _ in range(4))


def gate(**kw: Any) -> NegotiationQuota:
    return NegotiationQuota(get_settings().model_copy(update=kw))


async def test_the_cooldown_is_per_user_and_opportunity_and_returns_when_the_call_was_not_made(
    clean_db: None,
) -> None:
    q = gate(negotiation_ai_cooldown_seconds=60, negotiation_ai_max_calls_per_day=0)
    assert (await q.reserve(U1, O1)).allowed
    wait = await q.reserve(U1, O1)
    assert not wait.allowed and wait.reason == "cooldown" and 1 <= wait.retry_after <= 60
    assert (await q.reserve(U1, O2)).allowed and (
        await q.reserve(U2, O1)
    ).allowed  # another item, another user
    await q.release(U1, O1)  # the model was not called after all
    assert (await q.reserve(U1, O1)).allowed


async def test_the_daily_cap_counts_per_user_and_a_refused_click_costs_nothing(clean_db: None) -> None:
    q = gate(negotiation_ai_cooldown_seconds=0, negotiation_ai_max_calls_per_day=2)
    assert (await q.reserve(U1, O1)).allowed and (await q.reserve(U1, O2)).allowed
    over = await q.reserve(U1, O1)
    assert not over.allowed and over.reason == "daily_cap" and over.retry_after >= 1
    assert (await q.reserve(U2, O1)).allowed  # the other user has their own count
    await q.release(U1, O1)
    assert (await q.reserve(U1, O1)).allowed  # a released click is given back
    assert not (await q.reserve(U1, O1)).allowed  # and the refused ones above did not eat into the cap


async def test_zero_means_unlimited_and_redis_is_not_touched(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> Any:
        raise AssertionError("redis must not be used")

    monkeypatch.setattr(quota_mod, "get_redis", boom)
    q = gate(negotiation_ai_cooldown_seconds=0, negotiation_ai_max_calls_per_day=0)
    for _ in range(5):
        assert (await q.reserve(U1, O1)).allowed
    await q.release(U1, O1)


async def test_when_redis_is_down_the_gate_fails_closed(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down:
        async def set(self, *a: Any, **k: Any) -> Any:
            raise RedisConnectionError("down")

        incr = delete = decr = ttl = set

    monkeypatch.setattr(quota_mod, "get_redis", lambda: Down())
    got = await gate(negotiation_ai_cooldown_seconds=60, negotiation_ai_max_calls_per_day=5).reserve(U1, O1)
    assert not got.allowed and got.reason == "redis" and got.retry_after > 0
    await gate(negotiation_ai_cooldown_seconds=60).release(U1, O1)  # releasing never raises
