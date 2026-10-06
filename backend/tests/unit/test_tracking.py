"""Status transitions and adaptive check schedule (pure)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.enums import ListingStatus as S
from app.domain.enums import StatusEvidence as E
from app.tracking.schedule import MAX_INTERVAL, MIN_INTERVAL, ScheduleInput, check_interval
from app.tracking.status import Observation, StatusState, apply_observation

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def active(**kw) -> StatusState:
    return StatusState(
        status=S.ACTIVE,
        published_at=T0 - timedelta(days=4),
        last_active_at=T0,
        last_active_price=Decimal("20"),
        **kw,
    )


def test_sold_on_evidence_estimates_date_window_midpoint_and_days_to_sell() -> None:
    u = apply_observation(active(), Observation(T0 + timedelta(hours=10), E.PAGE, S.SOLD, Decimal("20")))
    assert u.status == S.SOLD and u.changed and u.closed
    assert u.sold_detected_at == T0 + timedelta(hours=10)
    assert u.sold_at == T0 + timedelta(hours=5)  # midpoint: last seen active .. detection
    assert u.last_active_price == Decimal("20")
    assert u.days_to_sell == Decimal("4.2")


def test_provider_sale_date_is_used_when_given() -> None:
    sold_at = T0 + timedelta(hours=1)
    u = apply_observation(
        active(), Observation(T0 + timedelta(days=2), E.PROVIDER, S.SOLD, provider_sold_at=sold_at)
    )
    assert u.sold_at == sold_at


def test_disappearance_is_a_removal_never_a_sale() -> None:
    u = apply_observation(active(), Observation(T0 + timedelta(days=1), E.NOT_FOUND))
    assert u.status == S.REMOVED and u.changed
    assert u.sold_at is None and u.days_to_sell is None
    assert u.removed_at == T0 + timedelta(days=1)
    # Seen again as gone: still removed, no new removal date.
    again = apply_observation(
        StatusState(status=S.REMOVED, removed_at=u.removed_at),
        Observation(T0 + timedelta(days=3), E.NOT_FOUND),
    )
    assert again.status == S.REMOVED and not again.changed and again.removed_at == u.removed_at


def test_sold_stays_sold_when_the_page_later_disappears() -> None:
    sold = StatusState(status=S.SOLD, published_at=T0 - timedelta(days=2), sold_at=T0, sold_detected_at=T0)
    for obs in (
        Observation(T0 + timedelta(days=5), E.NOT_FOUND),
        Observation(T0 + timedelta(days=5), E.PAGE, S.REMOVED),
    ):
        u = apply_observation(sold, obs)
        assert u.status == S.SOLD and not u.changed and u.sold_at == T0 and u.removed_at is None


def test_unreachable_check_changes_nothing_and_counts_a_failure() -> None:
    prev = active(check_failures=1)
    u = apply_observation(prev, Observation(T0 + timedelta(hours=3), E.UNREACHABLE))
    assert u.status == S.ACTIVE and not u.changed and not u.reachable
    assert u.check_failures == 2
    assert u.last_active_at == T0  # not refreshed by a failed check


def test_active_again_reopens_a_closed_listing() -> None:
    removed = StatusState(status=S.REMOVED, removed_at=T0)
    u = apply_observation(removed, Observation(T0 + timedelta(days=1), E.PAGE, S.ACTIVE, Decimal("18")))
    assert u.status == S.ACTIVE and u.changed and u.removed_at is None
    assert u.last_active_price == Decimal("18")


def test_reserved_and_unchanged_counters() -> None:
    u = apply_observation(active(), Observation(T0 + timedelta(hours=1), E.PAGE, S.RESERVED))
    assert u.status == S.RESERVED and u.changed and u.unchanged_checks == 0
    same = apply_observation(
        active(unchanged_checks=2), Observation(T0 + timedelta(hours=1), E.PAGE, S.ACTIVE)
    )
    assert not same.changed and same.unchanged_checks == 3


def test_unreadable_status_keeps_the_stored_one() -> None:
    u = apply_observation(active(), Observation(T0 + timedelta(hours=1), E.PAGE, S.UNKNOWN))
    assert u.status == S.ACTIVE and not u.changed


def test_first_sight_of_a_sold_listing_uses_the_observation_time() -> None:
    u = apply_observation(
        StatusState(status=S.UNKNOWN, published_at=T0 - timedelta(days=10)), Observation(T0, E.PAGE, S.SOLD)
    )
    assert u.status == S.SOLD and u.sold_at == T0 and u.days_to_sell == Decimal("10.0")


# ------------------------------------------------------------------ adaptive schedule
def interval(**kw) -> timedelta | None:
    defaults = {"status": S.ACTIVE, "now": T0, "published_at": T0 - timedelta(hours=3)}
    return check_interval(ScheduleInput(**{**defaults, **kw}))


def test_closed_listings_are_never_checked() -> None:
    assert interval(status=S.SOLD) is None
    assert interval(status=S.REMOVED) is None


def test_recent_listings_are_checked_more_often_than_old_ones() -> None:
    fresh = interval()
    week = interval(published_at=T0 - timedelta(days=7))
    stale = interval(published_at=T0 - timedelta(days=90))
    assert fresh == timedelta(hours=2)
    assert fresh < week < stale


def test_favourites_score_and_reserved_shorten_the_interval() -> None:
    base = interval(published_at=T0 - timedelta(days=7))
    assert interval(published_at=T0 - timedelta(days=7), favourite_count=25) == base / 2
    assert interval(published_at=T0 - timedelta(days=7), favourite_count=60) == base / 3
    assert interval(published_at=T0 - timedelta(days=7), flip_score=80) == base / 2
    assert interval(status=S.RESERVED, published_at=T0 - timedelta(days=30)) == timedelta(hours=6)


def test_nothing_new_stretches_and_failures_back_off_within_bounds() -> None:
    base = interval(published_at=T0 - timedelta(days=7))
    assert interval(published_at=T0 - timedelta(days=7), unchanged_checks=2) == base * 2
    assert interval(published_at=T0 - timedelta(days=7), unchanged_checks=50) == base * 4
    assert interval(published_at=T0 - timedelta(days=7), check_failures=1) == base * 2
    assert (
        interval(published_at=T0 - timedelta(days=200), unchanged_checks=50, check_failures=9) == MAX_INTERVAL
    )
    assert interval(favourite_count=99, flip_score=99) >= MIN_INTERVAL


@pytest.mark.parametrize("status", [S.ACTIVE, S.RESERVED, S.UNKNOWN])
def test_open_statuses_are_scheduled(status: S) -> None:
    assert interval(status=status) is not None
