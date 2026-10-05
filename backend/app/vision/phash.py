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
