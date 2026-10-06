"""Adaptive check schedule (pure).

The next check of an open listing depends on how likely it is to change soon:

* recent listings change fast (most sales happen in the first days), old ones rarely;
* many favourites, a reserved status or a high opportunity score mean a sale may be close;
* every check that finds nothing new stretches the interval (up to 4x);
* failed checks back off exponentially (up to 16x), so a blocked source is not hammered.

Closed listings (sold, removed) are never checked again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.enums import CLOSED_STATUSES, ListingStatus

MIN_INTERVAL = timedelta(minutes=30)
MAX_INTERVAL = timedelta(days=14)


@dataclass(frozen=True)
class ScheduleInput:
    status: ListingStatus
    now: datetime
    published_at: datetime | None
    favourite_count: int = 0
    flip_score: int | None = None
    unchanged_checks: int = 0
    check_failures: int = 0


def base_interval(age: timedelta | None) -> timedelta:
    if age is None:
        return timedelta(hours=24)
    if age < timedelta(days=1):
        return timedelta(hours=2)
    if age < timedelta(days=3):
        return timedelta(hours=6)
    if age < timedelta(days=14):
        return timedelta(hours=24)
    if age < timedelta(days=60):
        return timedelta(days=3)
    return timedelta(days=7)


def check_interval(inp: ScheduleInput) -> timedelta | None:
    """Time until the next check, or ``None`` when the listing is closed."""
    if inp.status in CLOSED_STATUSES:
        return None
    age = inp.now - inp.published_at if inp.published_at else None
    interval = base_interval(age)
    if inp.favourite_count >= 50:
        interval /= 3
    elif inp.favourite_count >= 20:
        interval /= 2
    if inp.flip_score is not None and inp.flip_score >= 75:
        interval /= 2
    if inp.status == ListingStatus.RESERVED:
        interval = min(interval, timedelta(hours=6))
    interval *= min(4.0, 1 + 0.5 * max(0, inp.unchanged_checks))
    interval *= 2 ** min(4, max(0, inp.check_failures))
    return max(MIN_INTERVAL, min(MAX_INTERVAL, interval))


def next_check_at(inp: ScheduleInput) -> datetime | None:
    interval = check_interval(inp)
    return inp.now + interval if interval is not None else None
