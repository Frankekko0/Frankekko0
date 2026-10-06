"""Which acquisition modes can refresh a listing right now (fallback order)."""

from __future__ import annotations

from app.core.config import get_settings
from app.db.models import Listing
from app.domain.enums import CLOSED_STATUSES, AcquisitionMode, ListingStatus


def refresh_modes(listing: Listing) -> list[str]:
    """Modes able to re-read this listing, best first. Empty for closed listings."""
    if ListingStatus(listing.status) in CLOSED_STATUSES:
        return []
    settings = get_settings()
    if listing.provider != "vinted" and settings.marketplace_provider == "feed":
        return [AcquisitionMode.PROVIDER_SCAN.value]
    modes: list[str] = []
    if listing.provider == "vinted":
        modes.append(AcquisitionMode.EXTENSION_REFRESH.value)
        if settings.vinted_public_fetch_enabled:
            modes.append(AcquisitionMode.PUBLIC_FETCH.value)
        if settings.email_import_enabled:
            modes.append(AcquisitionMode.EMAIL.value)
    return modes
