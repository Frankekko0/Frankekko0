"""Internal copies of listing photos (signed-in users only, never public)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.deps import DB, CurrentUser
from app.core.errors import NotFoundError
from app.db.models import ListingImage
from app.media.archive import resolve

router = APIRouter(tags=["media"])


@router.get("/media/{image_id}", response_class=FileResponse)
async def archived_image(image_id: int, user: CurrentUser, db: DB) -> FileResponse:
    img = await db.get(ListingImage, image_id)
    path = resolve(img.local_path) if img and img.local_path else None
    if path is None:
        raise NotFoundError("Copia dell'immagine non disponibile.")
    return FileResponse(
        path,
        media_type=img.content_type or "application/octet-stream",
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )
