"""Local copies of listing photos, uploaded by the browser extension.

The server never contacts Vinted (decision Q3-B): the extension, which has already loaded the photos to
show the listing to the user, sends them here. A copy is the original file stored once under its
SHA-256 (identical photos share one file) in ``MEDIA_DIR``; it is what the photo analysis and the OCR
read, and it is the only thing sent to the AI model (as bytes, never as a Vinted address). Copies are
for internal use: they are served to signed-in users of this FlipFinder and never republished.

Uploads are validated like any untrusted input: size cap, a content type the file really has, a
decodable image of sane dimensions (no decompression bombs). A photo is only accepted for a listing
and a position the server knows from an item capture; nothing creates rows for unknown photos.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import Listing, ListingImage
from app.vision.phash import dhash

log = get_logger(__name__)
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
FORMAT_TYPE = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}
MAX_PIXELS = 60_000_000  # ~7700 x 7700: far more than a phone photo, far less than a decompression bomb
MIN_SIDE = 16


@dataclass(frozen=True)
class Validated:
    ok: bool
    content_type: str | None = None
    width: int | None = None
    height: int | None = None
    phash: str | None = None
    error: str | None = None


def media_root(settings: Settings | None = None) -> Path:
    return Path((settings or get_settings()).media_dir).resolve()


def validate_image(data: bytes, declared_type: str | None, settings: Settings | None = None) -> Validated:
    """Accept only a real image of a known type, of reasonable size and dimensions."""
    settings = settings or get_settings()
    if not data:
        return Validated(False, error="file vuoto")
    if len(data) > settings.image_archive_max_bytes:
        return Validated(False, error="file troppo grande")
    declared = (declared_type or "").split(";")[0].strip().lower()
    if declared not in EXTENSIONS:
        return Validated(False, error=f"tipo non consentito ({declared or 'sconosciuto'})")
    try:
        with Image.open(BytesIO(data)) as probe:
            fmt, (w, h) = probe.format or "", probe.size
            if w * h > MAX_PIXELS:
                return Validated(False, error="dimensioni eccessive")
            if min(w, h) < MIN_SIDE:
                return Validated(False, error="immagine troppo piccola")
            probe.load()
            real = FORMAT_TYPE.get(fmt)
            if real is None or real != declared:
                return Validated(False, error=f"il file non è {declared}")
            return Validated(True, real, w, h, dhash(probe))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return Validated(False, error="immagine non decodificabile")


def image_facts(data: bytes) -> dict[str, object]:
    """Perceptual hash and size of a stored photo (empty when it cannot be decoded)."""
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            return {"phash": dhash(img), "width": img.width, "height": img.height}
    except Exception:  # corrupt or unsupported file: the copy is kept, the hash is not
        return {}


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


def resolve(rel: str, settings: Settings | None = None) -> Path | None:
    """Absolute path of an archived file, refusing anything outside the media directory."""
    root = media_root(settings)
    path = (root / rel).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


async def read_copy(rel: str | None, settings: Settings | None = None) -> bytes | None:
    """The bytes of a stored copy, or ``None`` when there is none."""
    if not rel:
        return None
    path = resolve(rel, settings)
    return await asyncio.to_thread(path.read_bytes) if path else None


# ------------------------------------------------------------------ what the server asks the browser for
async def photos_wanted(
    session: AsyncSession, listing_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """For each listing, the photos without a local copy yet, in gallery order."""
    if not listing_ids or not get_settings().image_archive_enabled:
        return {}
    rows = (
        (
            await session.execute(
                select(ListingImage)
                .where(
                    ListingImage.listing_id.in_(listing_ids),
                    ListingImage.removed_at.is_(None),
                    ListingImage.local_path.is_(None),
                )
                .order_by(ListingImage.listing_id, ListingImage.position)
            )
        )
        .scalars()
        .all()
    )
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r.listing_id, []).append(
            {"image_key": r.image_key, "position": r.position, "url": r.url}
        )
    return out


async def photo_state(session: AsyncSession, vinted_id: str) -> tuple[uuid.UUID, int, int] | None:
    """(listing id, photos with a local copy, photos the listing has) for a captured Vinted item."""
    listing_id = (
        await session.execute(
            select(Listing.id).where(Listing.provider == "vinted", Listing.external_id == vinted_id)
        )
    ).scalar_one_or_none()
    if listing_id is None:
        return None
    total, have = (
        await session.execute(
            select(func.count(), func.count(ListingImage.local_path)).where(
                ListingImage.listing_id == listing_id, ListingImage.removed_at.is_(None)
            )
        )
    ).one()
    return listing_id, int(have), int(total)


@dataclass(frozen=True)
class UploadResult:
    stored: bool
    remaining: int = 0
    sha256: str | None = None
    error: str | None = None
    listing_id: uuid.UUID | None = None


async def register_upload(
    session: AsyncSession,
    vinted_id: str,
    image_key: str,
    data: bytes,
    declared_type: str | None,
    settings: Settings | None = None,
) -> UploadResult:
    """Store a photo the browser sent and attach it to the photo row the server already knows."""
    settings = settings or get_settings()
    row = (
        await session.execute(
            select(ListingImage, Listing.id)
            .join(Listing, Listing.id == ListingImage.listing_id)
            .where(
                Listing.provider == "vinted",
                Listing.external_id == vinted_id,
                ListingImage.image_key == image_key,
                ListingImage.removed_at.is_(None),
            )
        )
    ).first()
    if row is None:
        return UploadResult(False, error="foto sconosciuta per questo annuncio")
    image, listing_id = row
    check = await asyncio.to_thread(validate_image, data, declared_type, settings)
    if not check.ok or check.content_type is None:
        return UploadResult(False, error=check.error, listing_id=listing_id)
    rel, digest = await asyncio.to_thread(store, data, check.content_type, media_root(settings))
    await session.execute(
        update(ListingImage)
        .where(ListingImage.id == image.id)
        .values(
            local_path=rel,
            sha256=digest,
            content_type=check.content_type,
            byte_size=len(data),
            width=check.width,
            height=check.height,
            phash=check.phash,
            archive_status="ok",
            archive_error=None,
            archived_at=datetime.now(UTC),
        )
    )
    left = (await photos_wanted(session, [listing_id])).get(listing_id, [])
    # Identical photos in the listings of the same seller are the evidence of a repost.
    from app.media.reposts import link_visual_reposts

    await link_visual_reposts(session, [listing_id])
    return UploadResult(
        True,
        remaining=len([p for p in left if p["image_key"] != image_key]),
        sha256=digest,
        listing_id=listing_id,
    )
