"""The circuit breaker leaves a failing provider alone, then tries it again."""

from app.ai.breaker import CircuitBreaker


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_it_opens_after_the_configured_number_of_consecutive_failures() -> None:
    clock = Clock()
    b = CircuitBreaker(failures=3, cooldown=60, clock=clock)
    for _ in range(2):
        assert b.allow()
        b.failure()
    assert b.state == "closed" and b.allow()
    b.failure()
    assert b.state == "open" and not b.allow()


def test_a_success_resets_the_count() -> None:
    b = CircuitBreaker(failures=3, cooldown=60, clock=Clock())
    b.failure()
    b.failure()
    b.success()
    b.failure()
    b.failure()
    assert b.state == "closed"


def test_after_the_cooldown_exactly_one_trial_call_is_allowed() -> None:
    clock = Clock()
    b = CircuitBreaker(failures=1, cooldown=60, clock=clock)
    b.failure()
    assert not b.allow()
    clock.t += 59
    assert not b.allow()
    clock.t += 2
    assert b.state == "half_open"
    assert b.allow()  # the trial
    assert not b.allow()  # nobody else while it is out


def test_a_good_trial_closes_it_and_a_bad_one_reopens_it() -> None:
    clock = Clock()
    b = CircuitBreaker(failures=1, cooldown=60, clock=clock)
    b.failure()
    clock.t += 61
    assert b.allow()
    b.success()
    assert b.state == "closed" and b.allow()

    b.failure()
    clock.t += 61
    assert b.allow()
    b.failure()
    assert b.state == "open" and not b.allow()
    clock.t += 61
    assert b.state == "half_open"
