"""Which brand a piece of text names, whatever the spelling: "LEVIS", "Levi Strauss & Co." and "Levi's" are one brand.

The matrix of consistency and the title/photo check compare what the listing says with what the label or the logo
says; comparing the raw strings called a correct label a contradiction ("H & M" against "H&M", "CP COMPANY" against
"C.P. Company"). Here both sides are reduced to the brand of the catalogue they name and compared as brands.
"""

from __future__ import annotations

import re

from app.identification.taxonomy import DEFAULT_TAXONOMY, BrandSpec, Taxonomy, fold, related_brands

_NON_WORD = re.compile(r"[^a-z0-9]+")
# A label that prints only the house emblem or a short form of the name.
LABEL_SHORT_FORMS = {"polo": "ralph-lauren", "polo sport": "ralph-lauren", "lauren": "ralph-lauren"}


def squash(text: str) -> str:
    """Folded, apostrophes dropped, every other run of punctuation a single space: "H & M" -> "h m", "Arc'teryx" ->
    "arcteryx", "C.P. Company" -> "c p company"."""
    return _NON_WORD.sub(" ", fold(text).replace("'", "")).strip()


_INDEXES: dict[int, tuple[tuple[re.Pattern[str], BrandSpec, str], ...]] = {}


def _index(taxonomy: Taxonomy) -> tuple[tuple[re.Pattern[str], BrandSpec, str], ...]:
    if (cached := _INDEXES.get(id(taxonomy))) is not None:
        return cached
    entries: dict[str, BrandSpec] = {}
    for brand in taxonomy.all_brands:
        for alias in (brand.name, *brand.aliases):
            key = squash(alias)
            # A one-letter or two-letter form ("h m", "rl") is read only when it is the whole text.
            if key:
                entries.setdefault(key, brand)
    ordered = sorted(entries.items(), key=lambda kv: len(kv[0]), reverse=True)
    if len(_INDEXES) > 8:
        _INDEXES.clear()
    built = tuple((re.compile(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])"), b, k) for k, b in ordered)
    _INDEXES[id(taxonomy)] = built
    return built


def resolve_brand(text: str | None, taxonomy: Taxonomy = DEFAULT_TAXONOMY) -> BrandSpec | None:
    """The catalogue brand ``text`` names (the longest alias wins), or ``None`` if it names none."""
    if not text:
        return None
    s = squash(text)
    if not s:
        return None
    for rx, brand, key in _index(taxonomy):
        if len(key) <= 3 and s != key:
            continue
        if rx.search(s):
            return brand
    if (slug := LABEL_SHORT_FORMS.get(s)) and (brand := taxonomy.brand_by_slug.get(slug)):
        return brand
    return None


def same_brand(a: str | None, b: str | None, taxonomy: Taxonomy = DEFAULT_TAXONOMY) -> bool | None:
    """True/False when both texts name a catalogue brand (a sub-brand counts as its family: "Tommy Jeans" is
    Tommy Hilfiger, "Polo" is Ralph Lauren); ``None`` when one of them names no known brand."""
    ba, bb = resolve_brand(a, taxonomy), resolve_brand(b, taxonomy)
    if ba is None or bb is None:
        return None
    return related_brands(ba.slug, bb.slug)
