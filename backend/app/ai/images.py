"""Photos as a model provider accepts them: the image types it reads and the size of one request.

The photos are the bytes the browser uploaded (never an address on Vinted). Gemini reads png, jpeg, webp, heic
and heif inline (a GIF is a 400) and caps a whole request at 20 MB, base64 included. So a photo of another type
is converted to JPEG, and when the gallery would not fit, the biggest photos are shrunk step by step (side and
quality) until each is within its share of the budget. Small photos of an accepted type are sent untouched.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps

from app.core.logging import get_logger

log = get_logger(__name__)

FORMAT_TYPE = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}
# After the first size (side, quality) of a photo: smaller and smaller, only while the request does not fit.
SHRINK_STEPS = ((1280, 85), (1120, 82), (1024, 80), (900, 76), (768, 72), (640, 68), (512, 65))


@dataclass(frozen=True)
class ImageLimits:
    """What one provider takes: the image types, and the encoded bytes of all the photos of a request."""

    accepted: tuple[str, ...]
    budget: int | None = None  # None: no cap that a gallery of photos can reach


ANTHROPIC_IMAGES = ImageLimits(("image/jpeg", "image/png", "image/webp", "image/gif"))
# Gemini's inline cap is 20 MB per request: leave room for the prompt and the schema.
GEMINI_IMAGES = ImageLimits(("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"), 14_000_000)


def _flat_rgb(img: Image.Image) -> Image.Image:
    """RGB for JPEG: transparency goes on white (not black), the first frame of an animation is used."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return img.convert("RGB")


def _jpeg(img: Image.Image, max_side: int, quality: int) -> bytes:
    small = _flat_rgb(ImageOps.exif_transpose(img))  # a phone photo keeps its orientation in the EXIF
    small.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = BytesIO()
    small.save(out, "JPEG", quality=quality)
    return out.getvalue()


def _first(
    data: bytes, content_type: str | None, limits: ImageLimits, max_side: int, max_bytes: int, q: int
) -> tuple[str, bytes] | None:
    """One photo as it goes out first: untouched when its type is accepted and it is small enough."""
    try:
        with Image.open(BytesIO(data)) as img:
            sniffed = FORMAT_TYPE.get(img.format or "")
            media = (
                content_type
                if content_type in limits.accepted
                else sniffed
                if sniffed in limits.accepted
                else None
            )
            if media is None or max(img.size) > max_side or len(data) > max_bytes:
                return "image/jpeg", _jpeg(img, max_side, q)
            return media, data
    except Exception:
        log.info("vision.prepare_failed")
        # Not decodable here: send it as it is only when the provider takes its declared type.
        return (content_type, data) if content_type in limits.accepted and content_type else None


def _encoded_size(raw: int) -> int:
    return 4 * ((raw + 2) // 3)


def fit_images(
    blobs: Sequence[tuple[bytes, str | None]],
    limits: ImageLimits,
    *,
    max_side: int = 1568,
    max_bytes: int = 3_500_000,
    quality: int = 88,
) -> list[tuple[str, str] | None]:
    """``(media type, base64)`` of each photo for a provider, in order; ``None`` for one that cannot be sent
    (not an image the provider takes and not one we can convert)."""
    photos = [_first(data, ctype, limits, max_side, max_bytes, quality) for data, ctype in blobs]
    present = [i for i, x in enumerate(photos) if x is not None]
    if limits.budget is not None and present:
        total = _total(photos)
        if total > limits.budget:
            share = _share([_encoded_size(len(photos[i][1])) for i in present], limits.budget)  # type: ignore[index]
            for i in present:
                current = photos[i]
                if current is None or _encoded_size(len(current[1])) <= share:
                    continue
                for side, q in SHRINK_STEPS:
                    try:
                        with Image.open(BytesIO(blobs[i][0])) as img:
                            smaller = _jpeg(img, side, q)
                    except Exception:
                        break
                    photos[i] = ("image/jpeg", smaller)
                    if _encoded_size(len(smaller)) <= share:
                        break
            log.info("vision.images_shrunk", photos=len(present), before=total, after=_total(photos))
    return [None if x is None else (x[0], base64.b64encode(x[1]).decode()) for x in photos]


def _share(sizes: list[int], budget: int) -> int:
    """What each photo that must shrink may take: the budget left once the small ones are counted as they are."""
    share = budget // len(sizes)
    while True:
        over = [x for x in sizes if x > share]
        if not over:
            return share
        better = (budget - sum(x for x in sizes if x <= share)) // len(over)
        if better <= share:
            return share
        share = better


def _total(photos: Sequence[tuple[str, bytes] | None]) -> int:
    return sum(_encoded_size(len(x[1])) for x in photos if x is not None)
