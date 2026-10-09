"""Small real photos for tests: the server only ever sees bytes the browser uploaded."""

from __future__ import annotations

import hashlib
import random
from io import BytesIO

from PIL import Image, ImageDraw

from app.media.keys import image_key
from app.vision.analyzer import PhotoInput


def jpeg(seed: int = 0, size: tuple[int, int] = (64, 64), fmt: str = "JPEG") -> bytes:
    """A distinct, decodable image per seed (shapes on a coloured background)."""
    rnd = random.Random(seed)
    img = Image.new("RGB", size, tuple(rnd.randint(60, 220) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(8):
        x, y = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        d.ellipse([x, y, x + 20, y + 14], fill=tuple(rnd.randint(0, 255) for _ in range(3)))
    out = BytesIO()
    img.save(out, fmt)
    return out.getvalue()


def photo(
    position: int, data: bytes | None = None, *, key: str | None = None, seed: int | None = None
) -> PhotoInput:
    """One uploaded photo (``data`` given or generated from ``seed``, default the position)."""
    blob = data if data is not None else jpeg(position if seed is None else seed)
    return PhotoInput(
        position=position,
        key=key or image_key(f"/t/photo-{position}/f800.jpeg"),
        data=blob,
        sha256=hashlib.sha256(blob).hexdigest(),
        content_type="image/jpeg",
    )


def photos(n: int, *, start: int = 0) -> list[PhotoInput]:
    return [photo(i, seed=start + i) for i in range(n)]
