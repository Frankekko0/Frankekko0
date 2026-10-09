"""The "da verificare" rules (pure)."""

from datetime import timedelta

import pytest

from app.domain.enums import ListingStatus
from app.tracking.verification import VerifyThresholds, is_buyable, needs_verification
from tests.conftest import NOW

TH = VerifyThresholds(active=timedelta(hours=48), reserved=timedelta(hours=24))


@pytest.mark.parametrize(
    ("status", "hours_ago", "expected"),
    [
        ("active", 47.9, False),
        ("active", 48, True),
        ("active", 200, True),
        ("reserved", 23.9, False),
        ("reserved", 24, True),
        # Closed or unreadable states are not "stale": they never claim to be available.
        ("sold", 5000, False),
        ("removed", 5000, False),
        ("unknown", 5000, False),
        ("to_verify", 5000, False),
    ],
)
def test_only_listings_claiming_to_be_available_go_stale(status, hours_ago, expected) -> None:
    verified = NOW - timedelta(hours=hours_ago)
    assert needs_verification(status, verified, verified, NOW, TH) is expected


def test_a_record_never_verified_counts_from_its_last_sighting() -> None:
    assert needs_verification("active", None, NOW - timedelta(hours=50), NOW, TH)
    assert not needs_verification("active", None, NOW - timedelta(hours=1), NOW, TH)
    # A recent verification wins over an old sighting field.
    assert not needs_verification("active", NOW - timedelta(hours=1), NOW - timedelta(days=9), NOW, TH)


def test_only_an_active_listing_is_buyable() -> None:
    assert is_buyable(ListingStatus.ACTIVE)
    for s in ("reserved", "sold", "removed", "unknown", "to_verify"):
        assert not is_buyable(s)
