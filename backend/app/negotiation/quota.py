"""How often the user may ask the model to write negotiation messages (Redis).

Writing is on demand only, one request per click. On a free model tier cost is always 0, so the USD budget never
stops anything: this module caps the clicks instead, per user per day (``NEGOTIATION_AI_MAX_CALLS_PER_DAY``) and per
opportunity (``NEGOTIATION_AI_COOLDOWN_SECONDS``), on top of the per-model request caps every call passes through
(``app.ai.limiter``). A limit of 0 means unlimited. If Redis is down the gate fails CLOSED: the templates answer.
"""

from __future__ import annotations

import contextlib
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.exceptions import RedisError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.redis import get_redis

log = get_logger(__name__)


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    reason: str = ""  # daily_cap | cooldown | redis
    retry_after: float = 0.0


def _day(now: datetime | None = None) -> tuple[str, float]:
    """The UTC day and the seconds left in it."""
    now = now or datetime.now(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.date().isoformat(), max(1.0, (midnight - now).total_seconds())


class NegotiationQuota:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @staticmethod
    def _keys(user_id: uuid.UUID, opportunity_id: uuid.UUID) -> tuple[str, str]:
        day, _ = _day()
        return f"ff:neg:day:{user_id}:{day}", f"ff:neg:cool:{user_id}:{opportunity_id}"

    async def reserve(self, user_id: uuid.UUID, opportunity_id: uuid.UUID) -> QuotaDecision:
        """Count one model call for this user and opportunity, or say why not. Call ``release`` when the call
        turns out not to have been made."""
        cap = self.settings.negotiation_ai_max_calls_per_day
        cool = self.settings.negotiation_ai_cooldown_seconds
        if cap <= 0 and cool <= 0:
            return QuotaDecision(True)
        day_key, cool_key = self._keys(user_id, opportunity_id)
        redis = get_redis()
        try:
            if cool > 0 and not await redis.set(cool_key, "1", ex=cool, nx=True):
                return QuotaDecision(False, "cooldown", float(max(1, await redis.ttl(cool_key))))
            if cap > 0:
                n = await redis.incr(day_key)
                if n == 1:
                    await redis.expire(day_key, int(_day()[1]) + 86400)
                if n > cap:
                    await redis.decr(day_key)
                    if cool > 0:
                        await redis.delete(cool_key)
                    return QuotaDecision(False, "daily_cap", math.ceil(_day()[1]))
        except RedisError as exc:
            log.warning("negotiation.quota_redis_unavailable", error=str(exc))
            return QuotaDecision(False, "redis", 30.0)
        return QuotaDecision(True)

    async def release(self, user_id: uuid.UUID, opportunity_id: uuid.UUID) -> None:
        """The model was not called after all (a request cap or an outage refused it): give the click back."""
        cap, cool = (
            self.settings.negotiation_ai_max_calls_per_day,
            self.settings.negotiation_ai_cooldown_seconds,
        )
        if cap <= 0 and cool <= 0:
            return
        day_key, cool_key = self._keys(user_id, opportunity_id)
        with contextlib.suppress(RedisError):
            redis = get_redis()
            if cool > 0:
                await redis.delete(cool_key)
            if cap > 0:
                await redis.decr(day_key)
