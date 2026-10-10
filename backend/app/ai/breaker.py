"""Circuit breaker for the model provider: stop calling a provider that keeps failing.

Closed: calls go through. After ``failures`` consecutive failures it opens: calls are refused
without being made (the caller falls back to its rules) until ``cooldown`` seconds have passed.
Then one trial call is allowed (half-open): success closes it, failure opens it again.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class CircuitBreaker:
    def __init__(
        self, failures: int = 5, cooldown: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.threshold = failures
        self.cooldown = cooldown
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_out = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half_open" if self._clock() - self._opened_at >= self.cooldown else "open"

    def allow(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self._trial_out:
            self._trial_out = True  # exactly one trial call while half-open
            return True
        return False

    def release(self) -> None:
        """The call that held the half-open trial slot ended without a verdict (quota, rejected request): give it back."""
        self._trial_out = False

    def success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._trial_out = False

    def failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold or self._opened_at is not None:
            self._opened_at = self._clock()
            self._trial_out = False
