"""Tracking policy: which captures mark a listing as tracked and which listings get checked."""

from __future__ import annotations

from datetime import datetime

from app.domain.enums import OPEN_STATUSES, AcquisitionMode, ListingStatus, StatusEvidence
from app.tracking.schedule import ScheduleInput, next_check_at

# Captures that express an explicit intent on one listing: the listing becomes tracked (periodic
# status checks). Cards seen while scrolling and whole search pages are market data: they are
# updated whenever they are seen again, and tracked only when the user asks.
TRACKING_MODES = frozenset(
    {
        AcquisitionMode.EXTENSION_DEEP,
        AcquisitionMode.LINK_IMPORT,
        AcquisitionMode.MANUAL_FORM,
        AcquisitionMode.BOOKMARKLET,
        AcquisitionMode.EMAIL,
    }
)


def evidence_for(mode: AcquisitionMode) -> StatusEvidence:
    return StatusEvidence.PROVIDER if mode == AcquisitionMode.PROVIDER_SCAN else StatusEvidence.PAGE


def is_scheduled(status: str, tracked_at: datetime | None, acquisition_mode: str) -> bool:
    """Open listings that are tracked, or that come from the configured provider (market data
    the provider can re-read), get periodic checks. Closed ones never do."""
    if ListingStatus(status) not in OPEN_STATUSES:
        return False
    return tracked_at is not None or acquisition_mode == AcquisitionMode.PROVIDER_SCAN


def schedule(
    *,
    status: str,
    tracked_at: datetime | None,
    acquisition_mode: str,
    now: datetime,
    published_at: datetime | None,
    favourite_count: int | None,
    flip_score: int | None = None,
    unchanged_checks: int = 0,
    check_failures: int = 0,
) -> datetime | None:
    if not is_scheduled(status, tracked_at, acquisition_mode):
        return None
    return next_check_at(
        ScheduleInput(
            status=ListingStatus(status),
            now=now,
            published_at=published_at,
            favourite_count=favourite_count or 0,
            flip_score=flip_score,
            unchanged_checks=unchanged_checks,
            check_failures=check_failures,
        )
    )
