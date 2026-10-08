"""Acquisition modes: link import, notification emails, status of every mode."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.acquisition.identity import vinted_links
from app.acquisition.public_fetch import PublicPageFetcher
from app.acquisition.service import import_links, process_email_bytes
from app.acquisition.vinted_parser import load_config
from app.api.deps import DB, CurrentUser
from app.core.cache import NS_FEED, cache
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimit
from app.db.models import AcquisitionAttempt, Listing, SystemState

router = APIRouter(tags=["acquisition"])
MAX_EMAIL_BYTES = 2 * 1024 * 1024
MAX_LINKS = 500


class LinksIn(BaseModel):
    text: str = Field(
        min_length=1, max_length=100_000, description="Links, one per line or anywhere in the text"
    )


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


@router.post("/listings/import/links", response_model=dict[str, Any], status_code=201)
async def import_link_list(body: LinksIn, user: CurrentUser, db: DB) -> dict[str, Any]:
    """Paste a link or a list of links. Each becomes a tracked record (dedup by Vinted ID): known
    listings keep their data, new ones start with the link only and are filled in by the first
    refresh (server read if enabled, otherwise the extension when you open them)."""
    links = vinted_links(body.text)[:MAX_LINKS]
    if not links:
        return {
            "found": 0,
            "created": 0,
            "existing": 0,
            "items": [],
            "message": "Nessun link di annuncio Vinted trovato.",
        }
    res = await import_links(db, links)
    await db.commit()
    await cache.bump(NS_FEED)
    return {
        "found": len(links),
        "created": len(res.created),
        "existing": len(res.existing),
        "items": [
            {"vinted_id": vid, "listing_id": str(res.ids_by_vinted[vid]), "url": url} for url, vid in links
        ],
        "public_fetch_enabled": get_settings().vinted_public_fetch_enabled,
    }


email_limit = RateLimit("email-upload", per_minute=60)


@router.post("/acquisition/email", response_model=dict[str, Any], dependencies=[Depends(email_limit)])
async def upload_email(request: Request, user: CurrentUser, db: DB) -> dict[str, Any]:
    """Upload one Vinted notification email (.eml, sent as the raw request body). "Your favourite
    has been sold" confirms the sale; "price reduced" records the new price."""
    length = int(request.headers.get("content-length") or 0)
    if length > MAX_EMAIL_BYTES:
        raise PayloadTooLargeError("Email troppo grande (massimo 2 MB).")
    raw = await request.body()
    if len(raw) > MAX_EMAIL_BYTES:
        raise PayloadTooLargeError("Email troppo grande (massimo 2 MB).")
    summary = await process_email_bytes(db, [raw])
    await db.commit()
    if summary.sold or summary.price_drops or summary.created:
        await cache.bump(NS_FEED)
    return summary.as_dict()


@router.get("/acquisition/status", response_model=dict[str, Any])
async def acquisition_status(user: CurrentUser, db: DB) -> dict[str, Any]:
    """Which acquisition modes are active, with their recent activity (transparency page)."""
    s = get_settings()
    by_mode = dict(
        (
            await db.execute(
                select(Listing.acquisition_mode, func.count()).group_by(Listing.acquisition_mode)
            )
        ).all()
    )
    recent_failures = (
        (
            await db.execute(
                select(AcquisitionAttempt)
                .where(AcquisitionAttempt.outcome.not_in(("ok", "unchanged", "skipped")))
                .order_by(AcquisitionAttempt.started_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    email_state = await db.get(SystemState, "email_import")
    extension_state = await db.get(SystemState, "extension_sync")
    due = (
        await db.execute(
            select(func.count()).where(
                Listing.provider == "vinted",
                Listing.tracked_at.is_not(None),
                Listing.next_check_at <= func.now(),
            )
        )
    ).scalar_one()
    return {
        "provider": {
            "name": s.marketplace_provider if s.marketplace_provider != "none" else None,
            "listings": by_mode.get("provider_scan", 0),
        },
        "extension": {
            "listings": sum(
                by_mode.get(k, 0)
                for k in (
                    "extension_item",
                    "extension_card",
                    "extension_deep",
                    "extension_refresh",
                    "extension_scan",
                )
            ),
            "last_sync": (extension_state.value or {}).get("last_sync") if extension_state else None,
        },
        "public_fetch": await PublicPageFetcher(s).status(),
        "email": {
            "enabled": s.email_import_enabled,
            "listings": by_mode.get("email", 0),
            **(
                {
                    k: v
                    for k, v in (email_state.value or {}).items()
                    if k in ("last_run", "last_error", "last_summary")
                }
                if email_state
                else {}
            ),
        },
        "manual": {
            k: by_mode.get(k, 0) for k in ("manual_form", "link_import", "batch_import", "bookmarklet")
        },
        "tracked_due_now": due,
        "parser_config_version": load_config().version,
        "recent_failures": [
            {
                "at": a.started_at.isoformat(),
                "mode": a.mode,
                "outcome": a.outcome,
                "message": a.message,
                "vinted_id": a.vinted_id,
                "http_status": a.http_status,
            }
            for a in recent_failures
        ],
    }
