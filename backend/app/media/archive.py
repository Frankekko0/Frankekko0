"""Local copies of listing photos, taken when a listing is captured or analysed.

After a sale or a removal the original photo links can disappear; the copy keeps the tracking
page complete. Copies are for internal use only: they are served to signed-in users of this
FlipFinder and never republished.

Downloads are safe by construction: https only, allowed hosts only (Vinted's image CDN by
default), public IP addresses only, no redirects, image content types only, size limit. Files
are named by their SHA-256 (identical photos are stored once) under ``MEDIA_DIR``.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import ListingImage
from app.vision.analyzer import is_public_https_url

log = get_logger(__name__)
EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/avif": "avif",
}
MAX_ATTEMPTS = 3
PER_IMAGE_DELAY = 0.25  # gentle with the image host


@dataclass(frozen=True)
class Download:
    data: bytes | None
    content_type: str | None
    error: str | None


def media_root(settings: Settings | None = None) -> Path:
    return Path((settings or get_settings()).media_dir).resolve()


def host_allowed(url: str, settings: Settings) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in settings.image_archive_hosts)


async def download(client: httpx.AsyncClient, url: str, settings: Settings) -> Download:
    if not url.startswith("https://"):
        return Download(None, None, "skip:link non https o immagine generata (demo)")
    if not host_allowed(url, settings):
        return Download(None, None, "skip:host non consentito per l'archivio")
    if not await asyncio.to_thread(is_public_https_url, url):
        return Download(None, None, "indirizzo non pubblico")
    try:
        async with client.stream("GET", url, follow_redirects=False) as resp:
            if resp.status_code in (404, 410):
                return Download(None, None, f"immagine non più disponibile (HTTP {resp.status_code})")
            if resp.status_code != 200:
                return Download(None, None, f"HTTP {resp.status_code}")
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype not in EXTENSIONS:
                return Download(None, None, f"tipo non immagine ({ctype or 'sconosciuto'})")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > settings.image_archive_max_bytes:
                    return Download(None, None, "immagine troppo grande")
                chunks.append(chunk)
            return Download(b"".join(chunks), ctype, None)
    except httpx.HTTPError as exc:
        return Download(None, None, f"download non riuscito ({type(exc).__name__})")


def store(data: bytes, content_type: str, root: Path) -> tuple[str, str]:
    """Write once under <root>/<aa>/<sha256>.<ext>; returns (relative path, sha256)."""
    digest = hashlib.sha256(data).hexdigest()
    rel = f"{digest[:2]}/{digest}.{EXTENSIONS[content_type]}"
    path = root / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: a half-written file is never served
    return rel, digest


async def archive_listing_images(
    session: AsyncSession,
    listing_ids: list[uuid.UUID],
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, int]:
    settings = settings or get_settings()
    stats = {"ok": 0, "failed": 0, "skipped": 0}
    if not settings.image_archive_enabled or not listing_ids:
        return stats
    images = (
        (
            await session.execute(
                select(ListingImage)
                .where(
                    ListingImage.listing_id.in_(listing_ids),
                    or_(ListingImage.archive_status.is_(None), ListingImage.archive_status == "failed"),
                    ListingImage.archive_attempts < MAX_ATTEMPTS,
                )
                .order_by(ListingImage.listing_id, ListingImage.position)
            )
        )
        .scalars()
        .all()
    )
    if not images:
        return stats
    root = media_root(settings)
    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": "FlipFinder/1.0 (image archive)"}, transport=transport
    ) as client:
        for img in images:
            result = await download(client, img.url, settings)
            values: dict[str, object] = {"archive_attempts": img.archive_attempts + 1}
            if result.data is not None and result.content_type:
                rel, digest = await asyncio.to_thread(store, result.data, result.content_type, root)
                values |= {
                    "local_path": rel,
                    "sha256": digest,
                    "content_type": result.content_type,
                    "byte_size": len(result.data),
                    "archive_status": "ok",
                    "archive_error": None,
                    "archived_at": datetime.now(UTC),
                }
                stats["ok"] += 1
            elif result.error and result.error.startswith("skip:"):
                values |= {"archive_status": "skipped", "archive_error": result.error[5:][:200]}
                stats["skipped"] += 1
            else:
                values |= {"archive_status": "failed", "archive_error": (result.error or "errore")[:200]}
                stats["failed"] += 1
                log.warning("media.archive_failed", image_id=img.id, reason=result.error)
            await session.execute(update(ListingImage).where(ListingImage.id == img.id).values(**values))
            if result.data is not None:
                await asyncio.sleep(PER_IMAGE_DELAY)
    return stats


def resolve(rel: str, settings: Settings | None = None) -> Path | None:
    """Absolute path of an archived file, refusing anything outside the media directory."""
    root = media_root(settings)
    path = (root / rel).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


async def schedule_archive(listing_ids: list[uuid.UUID]) -> None:
    """Queue the photo copies of these listings (best effort: a queue outage never fails a capture)."""
    if not listing_ids or not get_settings().image_archive_enabled:
        return
    from app.workers.queue import enqueue

    ids = sorted({str(i) for i in listing_ids})
    try:
        for start in range(0, len(ids), 50):
            chunk = ids[start : start + 50]
            await enqueue(
                "archive_images",
                chunk,
                job_id=f"archive:{hashlib.sha256(''.join(chunk).encode()).hexdigest()}",
            )
    except Exception as exc:  # Redis down: the next capture or a refresh queues them again
        log.warning("media.archive_not_queued", error=type(exc).__name__, listings=len(ids))
