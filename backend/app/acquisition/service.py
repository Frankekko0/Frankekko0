"""Acquisition orchestration: link imports, per-listing refresh with fallback, notification emails.

Refresh fallback (best first), as listed by :func:`app.tracking.refresh.refresh_modes`:

1. the configured provider (an authorized feed) re-reads the listing;
2. the server reads the public page (opt-in, polite, stops at the first block);
3. otherwise the listing stays due and the browser extension refreshes it when the user browses
   Vinted (passively when the page is opened, or slowly in the background if enabled);
4. notification emails confirm sales and price drops whenever they arrive.

Every attempt that involves a user-facing source is logged in ``acquisition_attempts`` with a
readable message, successful or not.
"""

from __future__ import annotations

import email
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from email import policy
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.identity import title_from_slug
from app.acquisition.public_fetch import FetchOutcome, PublicPageFetcher
from app.acquisition.vinted_parser import EmailItem, items_in_email, parse_item_html
from app.core.logging import get_logger
from app.db.models import Listing
from app.domain.enums import AcquisitionMode, CaptureLevel, ListingStatus, StatusEvidence
from app.ingestion.service import IngestionService
from app.marketplace.base import ProviderListing
from app.marketplace.registry import get_provider
from app.opportunities.pipeline import AnalysisPipeline
from app.tracking.refresh import refresh_modes
from app.tracking.service import Attempt, TrackingService, record_attempts
from app.tracking.status import Observation

log = get_logger(__name__)


# ------------------------------------------------------------------ links
@dataclass
class LinkImportResult:
    created: list[uuid.UUID] = field(default_factory=list)
    existing: list[uuid.UUID] = field(default_factory=list)
    ids_by_vinted: dict[str, uuid.UUID] = field(default_factory=dict)


def link_listing(url: str, vinted_id: str) -> ProviderListing:
    """A record from the link alone: Vinted ID, URL and a title from the URL slug. Price and status
    are unknown until another mode reads the page (the record says so: capture level "link")."""
    return ProviderListing(
        external_id=vinted_id,
        url=url,
        title=title_from_slug(url),
        price=Decimal(0),
        status=ListingStatus.UNKNOWN,
        capture_level=CaptureLevel.LINK,
        raw={"source": "link"},
    )


async def import_links(
    session: AsyncSession,
    links: list[tuple[str, str]],
    mode: AcquisitionMode = AcquisitionMode.LINK_IMPORT,
    track: bool = True,
    now: datetime | None = None,
) -> LinkImportResult:
    """Create records for unknown links; known ones keep their data (a link never overwrites a
    price or a status) and become tracked, due for a refresh now."""
    now = now or datetime.now(UTC)
    out = LinkImportResult()
    if not links:
        return out
    ids = [vid for _, vid in links]
    rows = (
        await session.execute(
            select(Listing.id, Listing.external_id, Listing.tracked_at, Listing.status).where(
                Listing.provider == "vinted", Listing.external_id.in_(ids)
            )
        )
    ).all()
    known = {r.external_id: r for r in rows}
    for r in rows:
        out.existing.append(r.id)
        out.ids_by_vinted[r.external_id] = r.id
        if track and r.tracked_at is None:
            due = now if ListingStatus(r.status) not in (ListingStatus.SOLD, ListingStatus.REMOVED) else None
            await session.execute(
                update(Listing).where(Listing.id == r.id).values(tracked_at=now, next_check_at=due)
            )
    new = [link_listing(url, vid) for url, vid in links if vid not in known]
    if new:
        res = await IngestionService(session, "vinted", mode, track=track).ingest(new, now=now)
        out.created = list(res.new_ids)
        out.ids_by_vinted |= res.ids_by_external
        if track:
            # Due now: the first refresh fills in price, status and details.
            await session.execute(
                update(Listing).where(Listing.id.in_(res.new_ids)).values(next_check_at=now)
            )
    return out


# ------------------------------------------------------------------ refresh
@dataclass
class RefreshResult:
    outcome: str  # updated | unchanged | not_found | queued | blocked | error | closed
    message: str
    mode: str | None = None
    status: str | None = None
    retry_after: int | None = None
    needs_extension: bool = False

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


async def _analyze_if_needed(
    session: AsyncSession, listing_ids: list[uuid.UUID], mode: AcquisitionMode
) -> None:
    if listing_ids:
        await AnalysisPipeline(session).analyze_many(listing_ids, mode=mode)


async def refresh_listing(
    session: AsyncSession,
    listing: Listing,
    now: datetime | None = None,
    fetcher: PublicPageFetcher | None = None,
) -> RefreshResult:
    """Re-read one listing with the best available mode (see module docstring)."""
    now = now or datetime.now(UTC)
    if ListingStatus(listing.status) in (ListingStatus.SOLD, ListingStatus.REMOVED):
        return RefreshResult(
            "closed", "Articolo chiuso (venduto o rimosso): non viene più controllato.", status=listing.status
        )
    modes = refresh_modes(listing)
    if not modes:
        return RefreshResult(
            "queued",
            "Nessuna modalità automatica disponibile per questa fonte: aggiornalo importandolo di nuovo.",
            status=listing.status,
        )

    provider = await get_provider(session) if AcquisitionMode.PROVIDER_SCAN in modes else None
    if provider is not None:
        attempt = Attempt(
            AcquisitionMode.PROVIDER_SCAN, "refresh", listing_id=listing.id, vinted_id=listing.external_id
        )
        try:
            pl = await provider.get_listing(listing.external_id)
        except Exception as exc:  # provider-specific network errors
            attempt.fail(
                "error", "Fonte dati non raggiungibile: controllo rimandato.", error=type(exc).__name__
            )
            await TrackingService(session).observe(
                {listing.id: Observation(now, StatusEvidence.UNREACHABLE)}, AcquisitionMode.PROVIDER_SCAN
            )
            await record_attempts(session, [attempt])
            return RefreshResult(
                "error", attempt.message or "", AcquisitionMode.PROVIDER_SCAN, listing.status
            )
        if pl is None:
            upd = await TrackingService(session).observe(
                {listing.id: Observation(now, StatusEvidence.NOT_FOUND)}, AcquisitionMode.PROVIDER_SCAN
            )
            attempt.fail(
                "not_found", "Non più presente presso la fonte: segnato come rimosso (vendita non dedotta)."
            )
            await record_attempts(session, [attempt])
            return RefreshResult(
                "not_found",
                attempt.message or "",
                AcquisitionMode.PROVIDER_SCAN,
                upd[listing.id].status.value,
            )
        res = await IngestionService(session, listing.provider, AcquisitionMode.PROVIDER_SCAN).ingest(
            [pl], now=now
        )
        await _analyze_if_needed(session, res.updated_ids, AcquisitionMode.PROVIDER_SCAN)
        upd = res.status_updates[listing.id]
        await record_attempts(session, [attempt])
        changed = upd.changed or bool(res.price_changes)
        return RefreshResult(
            "updated" if changed else "unchanged",
            "Aggiornato dalla fonte dati." if changed else "Nessuna novità dalla fonte dati.",
            AcquisitionMode.PROVIDER_SCAN,
            upd.status.value,
        )

    if AcquisitionMode.PUBLIC_FETCH in modes:
        fetcher = fetcher or PublicPageFetcher()
        result = await fetcher.fetch_item(listing.url)
        attempt = Attempt(
            AcquisitionMode.PUBLIC_FETCH,
            "refresh",
            listing_id=listing.id,
            vinted_id=listing.external_id,
            http_status=result.http_status,
        )
        if result.has_page:
            parsed = parse_item_html(result.html or "", listing.url, now)
            if parsed.complete:
                res = await IngestionService(session, "vinted", AcquisitionMode.PUBLIC_FETCH).ingest(
                    [parsed.to_provider_listing()], now=now
                )
                await _analyze_if_needed(session, res.updated_ids, AcquisitionMode.PUBLIC_FETCH)
                upd = res.status_updates[listing.id]
                await record_attempts(session, [attempt])
                return RefreshResult(
                    "updated",
                    "Pagina letta e analisi aggiornata.",
                    AcquisitionMode.PUBLIC_FETCH,
                    upd.status.value,
                )
            if parsed.status == ListingStatus.REMOVED:
                result_obs = Observation(
                    now, StatusEvidence.NOT_FOUND, note="La pagina dice che l'annuncio non è più disponibile."
                )
                upd = await TrackingService(session).observe(
                    {listing.id: result_obs}, AcquisitionMode.PUBLIC_FETCH
                )
                attempt.fail(
                    "not_found", "Annuncio non più disponibile: segnato come rimosso (vendita non dedotta)."
                )
                await record_attempts(session, [attempt])
                return RefreshResult(
                    "not_found",
                    attempt.message or "",
                    AcquisitionMode.PUBLIC_FETCH,
                    upd[listing.id].status.value,
                )
            attempt.fail(
                "error",
                "Pagina letta ma dati non riconosciuti (mancano: "
                + ", ".join(parsed.missing)
                + "). Probabile cambio di pagina di Vinted: aggiornare vinted_parser.json.",
                parser_sources=parsed.sources,
            )
            await TrackingService(session).observe(
                {listing.id: Observation(now, StatusEvidence.UNREACHABLE)}, AcquisitionMode.PUBLIC_FETCH
            )
            await record_attempts(session, [attempt])
            return RefreshResult(
                "error",
                attempt.message or "",
                AcquisitionMode.PUBLIC_FETCH,
                listing.status,
                needs_extension=True,
            )
        if result.outcome == FetchOutcome.NOT_FOUND:
            upd = await TrackingService(session).observe(
                {listing.id: Observation(now, StatusEvidence.NOT_FOUND)}, AcquisitionMode.PUBLIC_FETCH
            )
            attempt.fail(
                "not_found", "Annuncio non trovato (404): segnato come rimosso, vendita non dedotta."
            )
            await record_attempts(session, [attempt])
            return RefreshResult(
                "not_found", attempt.message or "", AcquisitionMode.PUBLIC_FETCH, upd[listing.id].status.value
            )
        if result.outcome.attempted:  # blocked or network error: back off, status untouched
            attempt.fail("blocked" if result.outcome == FetchOutcome.BLOCKED else "error", result.message)
            await TrackingService(session).observe(
                {listing.id: Observation(now, StatusEvidence.UNREACHABLE)}, AcquisitionMode.PUBLIC_FETCH
            )
            await record_attempts(session, [attempt])
            return RefreshResult(
                "blocked" if result.outcome == FetchOutcome.BLOCKED else "error",
                result.message,
                AcquisitionMode.PUBLIC_FETCH,
                listing.status,
                needs_extension=True,
            )
        # Not attempted (paused, waiting, cap, robots): fall through to the extension.
        fallback_note = result.message
    else:
        fallback_note = None

    # No server-side mode right now: the extension refreshes it when the user browses Vinted.
    await session.execute(update(Listing).where(Listing.id == listing.id).values(next_check_at=now))
    message = "Apri l'annuncio su Vinted con l'estensione attiva: verrà aggiornato appena lo vedi."
    return RefreshResult(
        "queued",
        f"{fallback_note} {message}" if fallback_note else message,
        AcquisitionMode.EXTENSION_REFRESH,
        listing.status,
        needs_extension=True,
    )


async def refresh_due_public(session: AsyncSession, now: datetime | None = None) -> RefreshResult | None:
    """One step of the periodic server-side refresh (the job runs every minute): the most overdue
    tracked Vinted listing, if the public fetch is enabled and allowed right now."""
    fetcher = PublicPageFetcher()
    if not fetcher.s.vinted_public_fetch_enabled:
        return None
    now = now or datetime.now(UTC)
    listing = (
        await session.execute(
            select(Listing)
            .where(
                Listing.provider == "vinted", Listing.next_check_at <= now, Listing.tracked_at.is_not(None)
            )
            .order_by(Listing.next_check_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if listing is None:
        return None
    return await refresh_listing(session, listing, now, fetcher)


# ------------------------------------------------------------------ emails
@dataclass
class EmailSummary:
    messages: int = 0
    ignored: int = 0
    sold: int = 0
    price_drops: int = 0
    new_items: int = 0
    created: int = 0

    def as_dict(self) -> dict[str, int]:
        return self.__dict__.copy()


def parse_email_bytes(raw: bytes) -> tuple[str | None, list[EmailItem]]:
    msg = email.message_from_bytes(raw, policy=policy.default)
    sender = str(msg.get("From") or "")
    subject = str(msg.get("Subject") or "")
    html_part = msg.get_body(("html",))
    text_part = msg.get_body(("plain",))
    html = html_part.get_content() if html_part is not None else ""
    text = text_part.get_content() if text_part is not None else ""
    return (str(msg.get("Message-ID") or "") or None), items_in_email(sender, subject, html, text)


async def apply_email_items(
    session: AsyncSession, items: list[EmailItem], now: datetime | None = None
) -> EmailSummary:
    """Favourite sold -> confirmed sale; price reduced -> the listing is on sale at the new price;
    new item from a followed member -> a link record (market data, not tracked)."""
    now = now or datetime.now(UTC)
    summary = EmailSummary()
    if not items:
        return summary
    tracked_kinds = [i for i in items if i.kind in ("sold", "price_drop")]
    links = await import_links(
        session, [(i.url, i.vinted_id) for i in tracked_kinds], AcquisitionMode.EMAIL, track=True, now=now
    )
    others = [i for i in items if i.kind == "new_item"]
    other_links = await import_links(
        session, [(i.url, i.vinted_id) for i in others], AcquisitionMode.EMAIL, track=False, now=now
    )
    summary.created = len(links.created) + len(other_links.created)
    summary.new_items = len(others)
    observations: dict[uuid.UUID, Observation] = {}
    attempts: list[Attempt] = []
    for i in tracked_kinds:
        lid = links.ids_by_vinted.get(i.vinted_id)
        if lid is None:
            continue
        if i.kind == "sold":
            observations[lid] = Observation(
                now, StatusEvidence.EMAIL, ListingStatus.SOLD, note="Email Vinted: articolo venduto."
            )
            summary.sold += 1
        else:
            observations[lid] = Observation(
                now,
                StatusEvidence.EMAIL,
                ListingStatus.ACTIVE,
                price=i.price,
                note="Email Vinted: prezzo ribassato.",
            )
            summary.price_drops += 1
        attempts.append(
            Attempt(
                AcquisitionMode.EMAIL,
                "email",
                listing_id=lid,
                vinted_id=i.vinted_id,
                message=f"Email: {i.kind}",
            )
        )
    if observations:
        updates = await TrackingService(session).observe(observations, AcquisitionMode.EMAIL)
        # A price drop on an analysable listing: re-run its analysis with the new price.
        repriced = [
            lid
            for lid, o in observations.items()
            if o.price is not None and updates.get(lid) and updates[lid].status == ListingStatus.ACTIVE
        ]
        if repriced:
            rows = (
                (
                    await session.execute(
                        select(Listing.id).where(
                            Listing.id.in_(repriced), Listing.capture_level != CaptureLevel.LINK
                        )
                    )
                )
                .scalars()
                .all()
            )
            await _analyze_if_needed(session, list(rows), AcquisitionMode.EMAIL)
    await record_attempts(session, attempts)
    return summary


async def process_email_bytes(
    session: AsyncSession, messages: list[bytes], now: datetime | None = None
) -> EmailSummary:
    total = EmailSummary()
    for raw in messages:
        total.messages += 1
        try:
            _mid, items = parse_email_bytes(raw)
        except Exception as exc:  # malformed message: skip it, keep going
            log.warning("email.unreadable", error=type(exc).__name__)
            total.ignored += 1
            continue
        if not items:
            total.ignored += 1
            continue
        s = await apply_email_items(session, items, now)
        for k in ("sold", "price_drops", "new_items", "created"):
            setattr(total, k, getattr(total, k) + getattr(s, k))
    return total
