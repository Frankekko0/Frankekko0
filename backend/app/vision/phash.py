"""Perceptual hashing (dHash) and simple photo-quality metrics with Pillow.

dHash compares adjacent pixel brightness on a 9x8 grayscale thumbnail, producing a 64-bit hash
that survives resizing, recompression and small edits: two photos of the same item re-uploaded
in a repost differ by only a few bits (Hamming distance), while different items differ by ~32.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageFilter, ImageStat


def dhash(image: Image.Image, size: int = 8) -> str:
    gray = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = pixels[row * (size + 1) + col]
            right = pixels[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def sharpness(image: Image.Image) -> float:
    """0..1 proxy for focus: variance of the edge map of a 256px thumbnail."""
    thumb = image.convert("L")
    thumb.thumbnail((256, 256))
    edges = thumb.filter(ImageFilter.FIND_EDGES)
    variance = ImageStat.Stat(edges).var[0]
    return max(0.0, min(1.0, variance / 1500.0))


def brightness(image: Image.Image) -> float:
    thumb = image.convert("L")
    thumb.thumbnail((128, 128))
    return ImageStat.Stat(thumb).mean[0] / 255.0


def load_image(data: bytes) -> Image.Image:
    img = Image.open(BytesIO(data))
    img.load()
    return img


MIN_SIDE = 400  # px: below this labels and codes cannot be read
# Detail kept at full resolution vs a small thumbnail: a sharp photo keeps fine edges
# (>= 0.35 on every content tried), a blurred one loses them (<= 0.28). Content-independent,
# unlike the plain edge strength (a plain fabric has few edges even when in focus).
MIN_DETAIL_RATIO = 0.30
DARK, BRIGHT = 0.12, 0.95


def _edge_variance(image: Image.Image, side: int) -> float:
    thumb = image.convert("L")
    thumb.thumbnail((side, side))
    return float(ImageStat.Stat(thumb.filter(ImageFilter.FIND_EDGES)).var[0])


def detail_ratio(image: Image.Image) -> float | None:
    """Fine detail (up to 1024 px) over coarse detail (256 px); None for nearly flat images."""
    coarse = _edge_variance(image, 256)
    if coarse < 50:
        return None
    return _edge_variance(image, 1024) / coarse


def looks_like_screenshot(image: Image.Image) -> bool:
    """Phone screenshot: very tall frame with a flat status-bar strip on top."""
    w, h = image.size
    if w == 0 or h / w < 1.9:
        return False
    strip = image.convert("L").crop((0, 0, w, max(1, round(h * 0.035))))
    return ImageStat.Stat(strip).stddev[0] < 6


def photo_quality(image: Image.Image) -> dict[str, object]:
    """Local check of one full-resolution photo: can details (labels, codes) be verified on it?"""
    w, h = image.size
    ratio = detail_ratio(image)
    bright = brightness(image)
    reason = None
    if min(w, h) < MIN_SIDE:
        reason = "troppo piccola"
    elif ratio is not None and ratio < MIN_DETAIL_RATIO:
        reason = "sfocata"
    elif bright < DARK:
        reason = "troppo scura"
    elif bright > BRIGHT:
        reason = "sovraesposta"
    return {
        "width": w,
        "height": h,
        "sharpness": round(ratio, 3) if ratio is not None else None,
        "brightness": round(bright, 3),
        "usable": reason is None,
        "reason": reason,
        "screenshot": looks_like_screenshot(image),
    }
