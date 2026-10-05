"""Normalization of raw marketplace attributes into canonical values.

Pure functions (no I/O): condition phrases in five languages, Vinted-style size strings
("M / 38 / 10", "EU 42", "W32 | L32", "Taglia unica"), colors, materials, country codes and a
title fingerprint used by duplicate detection.
"""

from __future__ import annotations

import hashlib
import re

from app.domain.enums import Condition
from app.identification.taxonomy import COLORS, CONDITION_PHRASES, MATERIALS, fold

LETTER_SIZES = ("XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL")
_LETTER_ALIASES = {"2XL": "XXL", "3XL": "XXXL", "XXXXL": "XXXL", "4XL": "XXXL"}
_ONE_SIZE = re.compile(r"\b(taglia unica|one size|onesize|tu|os|unica|einheitsgroesse|talla unica)\b")
_LETTER_RE = re.compile(r"(?<![A-Z0-9])(XXXL|XXL|XL|XXS|XS|2XL|3XL|4XL|S|M|L)(?![A-Z0-9])")
_WAIST_RE = re.compile(r"\bW\s?(\d{2})\b")
_EU_SHOE_RE = re.compile(r"\b(?:EU|EUR)?\s?(3[5-9]|4[0-9]|50)(?:[.,](5))?\b")
_UK_SHOE_RE = re.compile(r"\bUK\s?(\d{1,2})(?:[.,](5))?\b")
_US_SHOE_RE = re.compile(r"\bUS\s?(\d{1,2})(?:[.,](5))?\b")
_IT_MEN = {44: "XS", 46: "S", 48: "M", 50: "L", 52: "XL", 54: "XXL", 56: "XXXL"}
_IT_WOMEN = {38: "XS", 40: "S", 42: "M", 44: "L", 46: "XL", 48: "XXL"}
FOOTWEAR = {"sneakers", "footwear"}
_SIZE_WORDS = (
    ("extra small", "XS"),
    ("extra large", "XL"),
    ("small", "S"),
    ("medium", "M"),
    ("large", "L"),
    ("piccola", "S"),
    ("media", "M"),
    ("grande", "L"),
)
WAIST_CATEGORIES = {"jeans", "trousers"}


def normalize_condition(raw: str | None) -> Condition:
    if not raw:
        return Condition.UNKNOWN
    text = fold(raw)
    for value, phrases in CONDITION_PHRASES:
        for phrase in phrases:
            if phrase == text or re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text):
                return Condition(value)
    return Condition.UNKNOWN


def condition_from_text(text: str) -> Condition:
    """Fallback when the structured field is missing: look for condition phrases in free text."""
    return normalize_condition(text)


def normalize_size(
    raw: str | None, category_slug: str | None = None, gender: str | None = None
) -> str | None:
    """Canonical size token: XS..XXXL, EU42 / EU42.5, W32, ONESIZE, or ``None`` if unknown."""
    if not raw:
        return None
    text = raw.strip()
    upper = text.upper()
    if _ONE_SIZE.search(fold(text)):
        return "ONESIZE"

    is_footwear = category_slug in FOOTWEAR
    if is_footwear:
        if m := _UK_SHOE_RE.search(upper):
            eu = int(m.group(1)) + 34 + (0.5 if m.group(2) else 0)
            return f"EU{_fmt_half(eu)}"
        if m := _US_SHOE_RE.search(upper):
            eu = int(m.group(1)) + 33.5 + (0.5 if m.group(2) else 0)
            return f"EU{_fmt_half(eu)}"
        if m := _EU_SHOE_RE.search(upper):
            return f"EU{m.group(1)}{'.5' if m.group(2) else ''}"

    if m := _WAIST_RE.search(upper):
        return f"W{m.group(1)}"

    if m := _LETTER_RE.search(upper):
        token = m.group(1)
        return _LETTER_ALIASES.get(token, token)

    words = fold(text)
    for word, token in _SIZE_WORDS:
        if re.search(rf"(?<!\w){word}(?!\w)", words):
            return token

    if m := re.fullmatch(r"\s*(\d{2})(?:[.,]5)?\s*", text):
        n = int(m.group(1))
        if category_slug in WAIST_CATEGORIES and 24 <= n <= 42:
            return f"W{n}"
        if is_footwear and 35 <= n <= 50:
            return f"EU{n}"
        if gender == "women" and n in _IT_WOMEN:
            return _IT_WOMEN[n]
        if n in _IT_MEN:
            return _IT_MEN[n]
        return f"IT{n}"
    return None


def _fmt_half(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def size_distance(a: str | None, b: str | None) -> int | None:
    """Ordinal distance between two canonical sizes of the same system; ``None`` if incomparable."""
    if not a or not b:
        return None
    if a == b:
        return 0
    if a in LETTER_SIZES and b in LETTER_SIZES:
        return abs(LETTER_SIZES.index(a) - LETTER_SIZES.index(b))
    for prefix, step in (("EU", 1.0), ("W", 1.0)):
        if a.startswith(prefix) and b.startswith(prefix):
            try:
                return round(abs(float(a[len(prefix) :]) - float(b[len(prefix) :])) / step)
            except ValueError:
                return None
    return None


def _vocab_match(raw: str | None, vocab: dict[str, tuple[str, ...]]) -> str | None:
    if not raw:
        return None
    text = fold(raw)
    best: tuple[int, str] | None = None
    for canonical, words in vocab.items():
        for word in words:
            w = fold(word)
            if (best is None or len(w) > best[0]) and re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text):
                best = (len(w), canonical)
    return best[1] if best else None


def normalize_color(raw: str | None) -> str | None:
    return _vocab_match(raw, COLORS)


def normalize_material(raw: str | None) -> str | None:
    return _vocab_match(raw, MATERIALS)


def normalize_country(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if len(raw) == 2 and raw.isalpha():
        return raw.upper()
    names = {
        "italia": "IT",
        "italy": "IT",
        "france": "FR",
        "francia": "FR",
        "espana": "ES",
        "spagna": "ES",
        "spain": "ES",
        "germany": "DE",
        "germania": "DE",
        "deutschland": "DE",
        "belgium": "BE",
        "netherlands": "NL",
        "portugal": "PT",
        "austria": "AT",
    }
    return names.get(fold(raw))


_FP_STRIP = re.compile(r"[^a-z0-9 ]+")
_FP_STOP = {"tg", "taglia", "size", "the", "di", "da", "con", "e", "a", "in", "uomo", "donna"}


def title_tokens(title: str) -> list[str]:
    text = _FP_STRIP.sub(" ", fold(title))
    return [t for t in text.split() if t not in _FP_STOP and len(t) > 1]


def title_fingerprint(title: str) -> str:
    """Order-insensitive fingerprint of a title, for exact-duplicate lookups."""
    tokens = sorted(set(title_tokens(title)))
    return hashlib.sha1(" ".join(tokens).encode(), usedforsecurity=False).hexdigest()[:32]
