"""Cross-process request caps for the model provider (Redis).

The USD budget cannot protect a free tier (cost is always 0), so calls are also counted: per model, per minute
(sliding window) and per day (reset at midnight in ``AI_QUOTA_TZ``, Pacific time for Gemini). A 429 from the
provider puts the model in a shared cooldown. Attempts are counted, not only successes, because a refused call may
still burn quota. If Redis is down the gate fails CLOSED: staying under a free quota matters more than one call.
Limits of 0 mean unlimited and Redis is never touched.
"""

from __future__ import annotations

import contextlib
import math
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from redis.exceptions import RedisError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.rate_limit import _SLIDING_WINDOW
from app.core.redis import get_redis

log = get_logger(__name__)


@dataclass(frozen=True)
class Admission:
    allowed: bool
    retry_after: float = 0.0
    reason: str = ""  # rpm | rpd | cooldown | redis


class AiRateLimiter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _day(self, now: datetime | None = None) -> tuple[str, float]:
        tz = ZoneInfo(self.settings.ai_quota_tz)
        local = (now or datetime.now(UTC)).astimezone(tz)
        midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return local.date().isoformat(), max(1.0, (midnight - local).total_seconds())

    async def peek(self, model: str, tier: str) -> Admission:
        """Would ``acquire`` let a request through right now? Read-only: nothing is counted, so asking does not use
        up the quota. It is a hint, not a reservation (another process may take the slot before the real call):
        work that is only worth doing when a call follows asks first, and the call still goes through ``acquire``."""
        rpm, rpd = self.settings.ai_limits(tier)
        if rpm <= 0 and rpd <= 0:
            return Admission(True)
        redis = get_redis()
        try:
            cool = await redis.ttl(f"ff:ai:cooldown:{model}")
            if cool and cool > 0:
                return Admission(False, float(cool), "cooldown")
            day, secs_left = self._day()
            if rpd > 0 and int(await redis.get(f"ff:ai:rpd:{model}:{day}") or 0) >= rpd:
                return Admission(False, secs_left, "rpd")
            if rpm > 0:
                now_ms = int(time.time() * 1000)
                key = f"ff:ai:rpm:{model}"
                since = f"({now_ms - 60000}"  # as ``acquire`` keeps it: entries older than 60 s are gone
                if int(await redis.zcount(key, since, "+inf")) >= rpm:
                    oldest = await redis.zrangebyscore(key, since, "+inf", start=0, num=1, withscores=True)
                    wait_ms = float(oldest[0][1]) + 60000 - now_ms if oldest else 1000.0
                    return Admission(False, max(1.0, math.ceil(wait_ms / 1000)), "rpm")
        except RedisError as exc:
            log.warning("ai.limiter_redis_unavailable", error=str(exc))
            return Admission(False, 30.0, "redis")
        return Admission(True)

    async def acquire(self, model: str, tier: str) -> Admission:
        rpm, rpd = self.settings.ai_limits(tier)
        if rpm <= 0 and rpd <= 0:
            return Admission(True)
        redis = get_redis()
        try:
            cool = await redis.ttl(f"ff:ai:cooldown:{model}")
            if cool and cool > 0:
                return Admission(False, float(cool), "cooldown")
            day, secs_left = self._day()
            dkey = f"ff:ai:rpd:{model}:{day}"
            if rpd > 0 and int(await redis.get(dkey) or 0) >= rpd:
                return Admission(False, secs_left, "rpd")
            if rpm > 0:
                now_ms = int(time.time() * 1000)
                ok, _count, wait_ms = await redis.eval(  # type: ignore[misc]
                    _SLIDING_WINDOW, 1, f"ff:ai:rpm:{model}", str(now_ms), "60000", str(rpm),
                    f"{now_ms}:{uuid.uuid4().hex[:8]}",
                )  # fmt: skip
                if not int(ok):
                    return Admission(False, max(1.0, math.ceil(int(wait_ms) / 1000)), "rpm")
            if rpd > 0:
                n = await redis.incr(dkey)
                if n == 1:
                    await redis.expire(dkey, int(secs_left) + 86400)
        except RedisError as exc:
            log.warning("ai.limiter_redis_unavailable", error=str(exc))
            return Admission(False, 30.0, "redis")
        return Admission(True)

    async def penalize(self, model: str, seconds: float) -> None:
        """The provider said 429: nobody calls this model for a while, in any process."""
        with contextlib.suppress(RedisError):
            await get_redis().set(f"ff:ai:cooldown:{model}", "1", ex=max(1, math.ceil(seconds)))

    async def snapshot(self, model: str, tier: str) -> dict[str, Any]:
        rpm, rpd = self.settings.ai_limits(tier)
        out: dict[str, Any] = {
            "model": model,
            "rpm_limit": rpm,
            "rpd_limit": rpd,
            "rpm_left": None,
            "rpd_left": None,
            "cooldown_s": 0,
        }
        if rpm <= 0 and rpd <= 0:
            return out
        try:
            redis = get_redis()
            now_ms = int(time.time() * 1000)
            used_rpm = int(await redis.zcount(f"ff:ai:rpm:{model}", now_ms - 60000, "+inf"))
            day, _ = self._day()
            used_rpd = int(await redis.get(f"ff:ai:rpd:{model}:{day}") or 0)
            cool = int(await redis.ttl(f"ff:ai:cooldown:{model}"))
        except RedisError:
            out["rpm_left"] = out["rpd_left"] = 0
            return out
        out["rpm_left"] = max(0, rpm - used_rpm) if rpm > 0 else None
        out["rpd_left"] = max(0, rpd - used_rpd) if rpd > 0 else None
        out["cooldown_s"] = max(0, cool)
        return out


_limiter: AiRateLimiter | None = None


def get_limiter() -> AiRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = AiRateLimiter()
    return _limiter
