"""Normalization of raw marketplace attributes into canonical values.

Pure functions (no I/O): condition phrases in five languages, Vinted-style size strings
("M / 38 / 10", "EU 42", "W32 | L32", "Taglia unica"), colors, materials, country codes and a
title fingerprint used by duplicate detection.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache

from app.core.logging import get_logger
from app.domain.enums import Condition
from app.identification.taxonomy import COLORS, CONDITION_LABELS, CONDITION_PHRASES, MATERIALS, fold

log = get_logger(__name__)

LETTER_SIZES = ("XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "XXXXL")
_LETTER_ALIASES = {"2XL": "XXL", "3XL": "XXXL", "4XL": "XXXXL"}
_ONE_SIZE = re.compile(r"\b(taglia unica|one size|onesize|tu|os|unica|einheitsgroesse|talla unica)\b")
_LETTER_RE = re.compile(r"(?<![A-Z0-9])(XXXXL|XXXL|XXL|XL|XXS|XS|2XL|3XL|4XL|S|M|L)(?![A-Z0-9])")
_WAIST_RE = re.compile(r"\bW\s?(\d{2})\b")
_EU_SHOE_RE = re.compile(r"\b(?:EU|EUR)?\s?(3[5-9]|4[0-9]|50)(?:[.,](5))?\b")
_UK_SHOE_RE = re.compile(r"\bUK\s?(\d{1,2})(?:[.,](5))?\b")
_US_SHOE_RE = re.compile(r"\bUS\s?(\d{1,2})(?:[.,](5))?\b")
_IT_MEN = {44: "XS", 46: "S", 48: "M", 50: "L", 52: "XL", 54: "XXL", 56: "XXXL"}
_IT_WOMEN = {38: "XS", 40: "S", 42: "M", 44: "L", 46: "XL", 48: "XXL"}
FOOTWEAR = {"sneakers", "footwear", "boots", "loafers", "sandals"}
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

# A condition field prints a label of a few words; running text is searched only for whole phrases.
_LABEL_MAX_LEN = 48
_LABEL_PREFIX = re.compile(r"^(?:condizioni?|condition|etat|zustand|estado|staat|stan)\s*[:\-]\s*")


def _shared_condition(raw: str) -> str | None:
    """The condition the shared parser configuration (``vinted_parser.json``, the same one the extension
    reads) gives a label: it knows every language's short labels ("Ottime", "Buone", "Très bon état")."""
    from app.acquisition.vinted_parser import load_config  # lazy: the parser imports this package

    for name, rx in load_config().conditions:
        if rx.search(raw):
            return name
    return None


@lru_cache(maxsize=512)
def _log_unrecognised(label: str) -> None:
    # Once per distinct label per process: the raw label stays on the listing (``condition_raw``), the
    # condition is "unknown" on purpose (never a guess), and this line says which label to teach the parser.
    log.warning("condition_label_unrecognised", label=label, reason="no_match_in_label_vocabulary")


def normalize_condition(raw: str | None) -> Condition:
    """The condition a field value or a short label means; ``unknown`` (and logged) when it is neither."""
    if not raw or not raw.strip():
        return Condition.UNKNOWN
    canonical = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if canonical in Condition._value2member_map_:
        return Condition(canonical)
    text = fold(raw)
    label = _LABEL_PREFIX.sub("", text).strip()
    is_label = len(label) <= _LABEL_MAX_LEN
    if is_label and (code := _shared_condition(raw) or CONDITION_LABELS.get(label)):
        return Condition(code)
    for value, phrases in CONDITION_PHRASES:
        for phrase in phrases:
            if phrase == text or re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text):
                return Condition(value)
    if is_label:
        _log_unrecognised(raw.strip()[:60])
    return Condition.UNKNOWN


def condition_from_text(text: str) -> Condition:
    """Fallback when the structured field is missing: a condition phrase in free text.

    Only whole phrases count ("ottime condizioni", "never worn"); a stray "good" or "gut" is not read."""
    if not text:
        return Condition.UNKNOWN
    folded = fold(text)
    for value, phrases in CONDITION_PHRASES:
        for phrase in phrases:
            if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", folded):
                return Condition(value)
    return Condition.UNKNOWN


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
        if is_footwear and (35 <= n <= 50 or (gender == "kids" and 17 <= n <= 38)):
            return f"EU{n}{'.5' if re.search(r'[.,]5', text) else ''}"
        if category_slug in WAIST_CATEGORIES and (n <= 34 or (gender != "women" and n <= 42)) and n >= 24:
            return f"W{n}"
        if gender == "women" and n in _IT_WOMEN:
            return _IT_WOMEN[n]
        if n in _IT_MEN:
            return _IT_MEN[n]
        return f"IT{n}"
    return None


def size_is_ambiguous(raw: str | None, category_slug: str | None, gender: str | None) -> bool:
    """A bare number whose meaning depends on what the item is (a shoe size, an Italian clothing size, a
    waist in inches): it is a guess at the scale, however clearly the field was filled in."""
    if not raw or not re.fullmatch(r"\s*\d{2}(?:[.,]5)?\s*", raw):
        return False
    n = int(raw.strip()[:2])
    if category_slug in FOOTWEAR:
        return False
    if category_slug in WAIST_CATEGORIES:
        return n > 34 and gender is None
    if gender is None and (n in _IT_MEN or n in _IT_WOMEN):
        return True
    return category_slug is None and 35 <= n <= 50


def _fmt_half(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:.1f}"


@lru_cache(maxsize=4096)
def size_distance(a: str | None, b: str | None) -> int | None:
    """Ordinal distance between two canonical sizes of the same system; ``None`` if incomparable.

    A bare number whose scale was not known when it was captured is stored as ``IT42``; for a shoe that is
    the same scale as ``EU42`` (a card has no category, the item page does), so the two are compared."""
    if not a or not b:
        return None
    if a == b:
        return 0
    if a in LETTER_SIZES and b in LETTER_SIZES:
        return abs(LETTER_SIZES.index(a) - LETTER_SIZES.index(b))
    for prefix in ("EU", "W"):
        if a.startswith(prefix) and b.startswith(prefix):
            return _numeric_distance(a[len(prefix) :], b[len(prefix) :])
    shoe = {"EU", "IT"}
    if a[:2] in shoe and b[:2] in shoe and a[:2] != b[:2]:
        return _numeric_distance(a[2:], b[2:])
    return None


def _numeric_distance(a: str, b: str) -> int | None:
    try:
        return round(abs(float(a) - float(b)))
    except ValueError:
        return None


_WORD = re.compile(r"\w+")
_VOCAB_PATTERNS: dict[int, list[tuple[frozenset[str], re.Pattern[str], str]]] = {}


def _vocab_patterns(vocab: dict[str, tuple[str, ...]]) -> list[tuple[frozenset[str], re.Pattern[str], str]]:
    """Folded, compiled vocabulary, longest word first (built once per vocabulary).

    Each entry carries the word's tokens: a word bounded by non-word characters can only match
    if all its tokens occur in the text, so a subset test skips most regex calls exactly.
    """
    key = id(vocab)
    if key not in _VOCAB_PATTERNS:
        words = [(fold(word), canonical) for canonical, ws in vocab.items() for word in ws]
        # Stable sort: among equally long words the vocabulary order still decides.
        words.sort(key=lambda wc: -len(wc[0]))
        _VOCAB_PATTERNS[key] = [
            (frozenset(_WORD.findall(w)), re.compile(rf"(?<!\w){re.escape(w)}(?!\w)"), c) for w, c in words
        ]
    return _VOCAB_PATTERNS[key]


def _vocab_hits(text: str, vocab: dict[str, tuple[str, ...]]) -> list[tuple[int, int, str]]:
    """(start, end, canonical) of every vocabulary word found in the folded ``text`` that is not inside a
    longer word of the vocabulary starting at the same place, in order of appearance."""
    tokens = frozenset(_WORD.findall(text))
    found: list[tuple[int, int, str]] = []
    for gate, pattern, canonical in _vocab_patterns(vocab):  # longest word first
        if gate <= tokens and (m := pattern.search(text)):
            found.append((m.start(), m.end(), canonical))
    kept: list[tuple[int, int, str]] = []
    for start, end, canonical in sorted(found, key=lambda h: (h[0], -(h[1] - h[0]))):
        if not any(k[0] <= start and end <= k[1] for k in kept):
            kept.append((start, end, canonical))
    return kept


def _vocab_match(raw: str | None, vocab: dict[str, tuple[str, ...]]) -> str | None:
    """Canonical value of the first vocabulary word in ``raw`` (the longest one where several start at the
    same place: "blu marino" is navy, not blue). Vinted prints a multi-valued field in the order the seller
    chose, so the first value is the primary one ("Bianco, Blu marino" is white)."""
    if not raw:
        return None
    hits = _vocab_hits(fold(raw), vocab)
    return hits[0][2] if hits else None


def normalize_color(raw: str | None) -> str | None:
    return _vocab_match(raw, COLORS)


# "effetto cashmere", "simil pelle", "faux fur": the word that follows is what it imitates, not what it is made of.
_MATERIAL_QUALIFIERS = frozenset(
    [
        "effetto",
        "aspetto",
        "look",
        "style",
        "stile",
        "tipo",
        "simil",
        "finto",
        "finta",
        "imitazione",
        "like",
        "effect",
        "faux",
        "eco",
        "ecologico",
        "ecologica",
        "sintetico",
        "sintetica",
        "vegan",
        "vegano",
        "vegana",
    ]
)
_PCT_AFTER_WORD = re.compile(r"^\s*[:=]?\s*(\d{1,3})\s*%")
_PCT_BEFORE_WORD = re.compile(r"(\d{1,3})\s*%\s*(?:di\s+|de\s+|of\s+)?$")


def normalize_material(raw: str | None) -> str | None:
    """The main material in a field or a text: the fibre with the biggest share when percentages are
    written ("30% lana 70% poliestere" is polyester), else the first one named. A word that only says what
    the item imitates ("effetto cashmere") is not a material, and imitation leather is not leather."""
    if not raw:
        return None
    text = fold(raw)
    shares: dict[str, int] = {}
    first: str | None = None
    for start, end, canonical in _vocab_hits(text, MATERIALS):
        before = _WORD.findall(text[:start])[-2:]
        if any(w in _MATERIAL_QUALIFIERS for w in before):
            if canonical == "leather" and first is None:
                first = "faux_leather"
            continue
        first = first or canonical
        pct = _PCT_BEFORE_WORD.search(text[:start]) or _PCT_AFTER_WORD.match(text[end:])
        if pct:
            shares[canonical] = shares.get(canonical, 0) + int(pct.group(1))
    if shares:
        return max(shares, key=lambda m: shares[m])
    return first


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


# ------------------------------------------------------------------ market (the Vinted domain a listing was seen on)
_MARKET_CURRENCY = {
    "pl": "PLN", "cz": "CZK", "sk": "EUR", "se": "SEK", "dk": "DKK", "hu": "HUF", "ro": "RON", "uk": "GBP",
    "com": "USD",
}  # fmt: skip


_HOST = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//(?:[^/?#@]*@)?([^/?#:]+)", re.I)


def market_from_url(url: str | None) -> str | None:
    """The market of a Vinted listing from the domain of its page: ``vinted.it`` -> ``it``,
    ``www.vinted.co.uk`` -> ``uk``, ``vinted.com`` -> ``com``. ``None`` for anything that is not a Vinted
    page. The market decides which prices are comparable: an Italian item is priced on Italian sales."""
    if not url:
        return None
    m = _HOST.match(url.strip() if "//" in url else f"//{url.strip()}")
    host = m.group(1).lower() if m else ""
    parts = host.split(".")
    if "vinted" not in parts[:-1]:
        return None
    tld = ".".join(parts[parts.index("vinted") + 1 :])
    tld = {"co.uk": "uk"}.get(tld, tld)
    return tld if re.fullmatch(r"[a-z]{2,3}", tld) else None


def market_currency(market: str | None) -> str:
    """The currency a market's prices are in (euro in the euro-area markets)."""
    return _MARKET_CURRENCY.get(market or "", "EUR")


_MARKET_COUNTRY = {"uk": "GB", "com": "US"}


def market_country(market: str | None) -> str | None:
    """ISO country code of a market: ``it`` -> ``IT``, ``uk`` -> ``GB``."""
    if not market:
        return None
    return _MARKET_COUNTRY.get(market, market.upper() if len(market) == 2 else None)
