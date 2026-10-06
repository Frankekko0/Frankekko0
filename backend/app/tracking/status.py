"""Listing status transitions (pure).

Rules:

* ``sold`` needs positive evidence: the page or card shows it, a "your favourite has been sold"
  email, or a provider field. A listing that is simply gone (page not found) becomes ``removed``
  and **no sale is inferred**: it never feeds the sold-price and time-to-sell statistics.
* When a sale is first observed, the sale moment is unknown: it happened between the last time
  the listing was seen active and now. ``sold_at`` is the midpoint of that window (or the
  provider's date when it gives one), ``days_to_sell`` is measured from publication.
* An unreachable check (blocked, network error) never changes the status: it only counts as a
  failure, so the scheduler backs off.
* ``sold`` and ``removed`` are closed. A later observation that the listing is active again (a
  cancelled sale, a temporarily hidden item) re-opens it: the newest real observation wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.enums import CLOSED_STATUSES, ListingStatus, StatusEvidence


@dataclass(frozen=True)
class StatusState:
    """What is stored about a listing's lifecycle before a new observation."""

    status: ListingStatus
    published_at: datetime | None = None
    last_active_at: datetime | None = None
    last_active_price: Decimal | None = None
    sold_at: datetime | None = None
    sold_detected_at: datetime | None = None
    removed_at: datetime | None = None
    check_failures: int = 0
    unchanged_checks: int = 0


@dataclass(frozen=True)
class Observation:
    observed_at: datetime
    evidence: StatusEvidence
    status: ListingStatus | None = None  # None for not_found / unreachable
    price: Decimal | None = None
    provider_sold_at: datetime | None = None  # only when the source states the sale date
    note: str | None = None  # readable context for the snapshot (e.g. "email: price reduced")


@dataclass(frozen=True)
class StatusUpdate:
    status: ListingStatus
    changed: bool
    last_active_at: datetime | None
    last_active_price: Decimal | None
    sold_at: datetime | None
    sold_detected_at: datetime | None
    removed_at: datetime | None
    days_to_sell: Decimal | None
    check_failures: int
    unchanged_checks: int
    reachable: bool
    note: str

    @property
    def closed(self) -> bool:
        return self.status in CLOSED_STATUSES


def _days(start: datetime | None, end: datetime | None) -> Decimal | None:
    if start is None or end is None or end < start:
        return None
    return Decimal(str(round((end - start).total_seconds() / 86400, 1)))


def apply_observation(prev: StatusState, obs: Observation) -> StatusUpdate:
    """Fold one observation into the stored lifecycle state."""
    base = {
        "last_active_at": prev.last_active_at,
        "last_active_price": prev.last_active_price,
        "sold_at": prev.sold_at,
        "sold_detected_at": prev.sold_detected_at,
        "removed_at": prev.removed_at,
    }

    if obs.evidence == StatusEvidence.UNREACHABLE:
        return StatusUpdate(
            status=prev.status,
            changed=False,
            days_to_sell=_days(prev.published_at, prev.sold_at),
            check_failures=prev.check_failures + 1,
            unchanged_checks=prev.unchanged_checks,
            reachable=False,
            note="Controllo non riuscito: stato invariato.",
            **base,
        )

    if obs.evidence == StatusEvidence.NOT_FOUND:
        new_status = ListingStatus.SOLD if prev.status == ListingStatus.SOLD else ListingStatus.REMOVED
        changed = new_status != prev.status
        return StatusUpdate(
            status=new_status,
            changed=changed,
            days_to_sell=_days(prev.published_at, prev.sold_at),
            check_failures=0,
            unchanged_checks=0 if changed else prev.unchanged_checks + 1,
            reachable=True,
            note=(
                "Annuncio non più disponibile: segnato come rimosso (vendita non dedotta)."
                if changed
                else "Annuncio ancora non disponibile."
            ),
            **{**base, "removed_at": prev.removed_at or obs.observed_at}
            if new_status == ListingStatus.REMOVED
            else base,
        )

    status = obs.status or ListingStatus.UNKNOWN
    changed = status != prev.status

    if status in (ListingStatus.ACTIVE, ListingStatus.RESERVED):
        base["last_active_at"] = obs.observed_at
        if obs.price is not None:
            base["last_active_price"] = obs.price
        if prev.status in CLOSED_STATUSES:
            # Seen open again: the newest real observation wins (cancelled sale, re-shown item).
            base.update(sold_at=None, sold_detected_at=None, removed_at=None)
        note = "Riservato." if status == ListingStatus.RESERVED else "Attivo."
        return StatusUpdate(
            status=status,
            changed=changed,
            days_to_sell=None,
            check_failures=0,
            unchanged_checks=0 if changed else prev.unchanged_checks + 1,
            reachable=True,
            note=note,
            **base,
        )

    if status == ListingStatus.SOLD:
        if prev.status == ListingStatus.SOLD and prev.sold_at is not None:
            return StatusUpdate(
                status=status,
                changed=False,
                days_to_sell=_days(prev.published_at, prev.sold_at),
                check_failures=0,
                unchanged_checks=prev.unchanged_checks + 1,
                reachable=True,
                note="Venduto.",
                **base,
            )
        if obs.provider_sold_at is not None:
            sold_at = obs.provider_sold_at
            note = "Venduto (data fornita dalla fonte)."
        elif prev.last_active_at is not None and prev.last_active_at <= obs.observed_at:
            sold_at = prev.last_active_at + (obs.observed_at - prev.last_active_at) / 2
            note = "Venduto: data stimata a metà tra l'ultima volta visto attivo e la rilevazione."
        else:
            sold_at = obs.observed_at
            note = "Venduto: data della rilevazione (mai visto attivo prima)."
        if base["last_active_price"] is None and obs.price is not None:
            base["last_active_price"] = obs.price
        base.update(sold_at=sold_at, sold_detected_at=obs.observed_at, removed_at=None)
        return StatusUpdate(
            status=status,
            changed=True,
            days_to_sell=_days(prev.published_at, sold_at),
            check_failures=0,
            unchanged_checks=0,
            reachable=True,
            note=note,
            **base,
        )

    if status == ListingStatus.REMOVED:
        new_status = ListingStatus.SOLD if prev.status == ListingStatus.SOLD else ListingStatus.REMOVED
        changed = new_status != prev.status
        if new_status == ListingStatus.REMOVED:
            base["removed_at"] = prev.removed_at or obs.observed_at
        return StatusUpdate(
            status=new_status,
            changed=changed,
            days_to_sell=_days(prev.published_at, prev.sold_at),
            check_failures=0,
            unchanged_checks=0 if changed else prev.unchanged_checks + 1,
            reachable=True,
            note="Rimosso dal venditore (vendita non dedotta).",
            **base,
        )

    # UNKNOWN: the page was read but the status could not be determined.
    return StatusUpdate(
        status=prev.status,
        changed=False,
        days_to_sell=_days(prev.published_at, prev.sold_at),
        check_failures=prev.check_failures,
        unchanged_checks=prev.unchanged_checks + 1,
        reachable=True,
        note="Stato non leggibile dalla pagina: invariato.",
        **base,
    )
