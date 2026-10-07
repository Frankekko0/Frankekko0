"""Strict match of a search result to the exact model searched.

A result is kept only if it is the model itself, as a single adult item: the brand is named, the
model is named in the title, no other model of the same brand (from the taxonomy) or a different
number of the same family ("Air Max 95" for "Air Max 90") is named, and it is not a kids' item, a
replica, a lot or an accessory. Every rejection has a reason, counted per model
(``external_searches.results``) so the filter can be audited. Implausible prices are rejected
afterwards, per model, in log space (``app.market.cleaning``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.identification.taxonomy import DISTINCTIVE_MODEL_KEYWORDS, SUSPICIOUS_PATTERNS, Taxonomy, fold

# Rejection reasons, in the order they are checked.
REASONS = (
    "brand",  # the brand is not named
    "model",  # the model is not named in the title
    "other_model",  # another model of the brand, another number, a collaboration
    "replica",
    "kids",
    "lot",
    "accessory",
    "not_item",  # a search/category page, not a single item
    "source",  # Vinted (our own source) or an excluded shop
    "no_price",
    "currency",  # a currency outside the fixed rate table
    "price",  # absurd price (below MIN_PRICE_EUR or above MAX_PRICE_EUR)
    "outlier",  # implausible for the model (log-space outlier among its prices)
)
FOOTWEAR = {"footwear", "sneakers"}
FOOTBALL = {"football-shirts"}
MIN_PRICE_EUR = 5
MAX_PRICE_EUR = 10_000
# Collaborations and special editions priced far from the model itself.
COLLABS = (
    "off white",
    "off-white",
    "travis scott",
    "sacai",
    "fear of god",
    "a ma maniere",
    "union la",
    "comme des garcons",
    "cdg",
    "fragment",
    "kaws",
    "patta",
    "atmos",
    "undercover",
    "tiffany",
    "louis vuitton",
    "dior",
    "ambush",
    "stranger things",
    "x supreme",
)


def norm(text: str | None) -> str:
    """Folded text with punctuation as spaces ("Air-Max 90!" -> "air max 90"); "&" and "'" kept
    (H&M, Levi's)."""
    return " ".join(re.sub(r"[^\w&']+", " ", fold(text or "")).split())


def _phrase(p: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w&]){re.escape(p)}(?![\w&])")


def _spans(patterns: Iterable[re.Pattern[str]], text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for p in patterns for m in p.finditer(text)]


@dataclass(frozen=True)
class ModelSpec:
    """What the matcher knows about the model searched."""

    brand_slug: str
    brand_name: str
    model_name: str
    category_slug: str | None
    aliases: tuple[str, ...]  # normalised brand aliases
    keywords: tuple[str, ...]  # normalised phrases naming the model
    others: tuple[tuple[str, tuple[str, ...]], ...] = ()  # other lines of the brand
    other_brands: tuple[str, ...] = ()  # normalised names of the other brands
    distinctive: bool = False  # the model name alone implies the brand

    @property
    def footwear(self) -> bool:
        return self.category_slug in FOOTWEAR

    @property
    def brand_patterns(self) -> list[re.Pattern[str]]:
        return [_phrase(a) for a in self.aliases]

    @property
    def model_patterns(self) -> list[re.Pattern[str]]:
        return [_phrase(k) for k in self.keywords]


def model_spec(
    taxonomy: Taxonomy, brand_slug: str, model_name: str, category_slug: str | None = None
) -> ModelSpec | None:
    """The spec of ``brand_slug``/``model_name`` from the taxonomy (``None`` for an unknown brand).
    A model that is not a taxonomy line is matched by its own name."""
    brand = taxonomy.brand_by_slug.get(brand_slug)
    if brand is None:
        return None
    folded = fold(model_name)
    line = next((ln for ln in brand.lines if fold(ln.name) == folded), None)
    keywords = {norm(model_name)}
    if line is not None:
        keywords.update(norm(k) for k in line.keywords)
        category_slug = category_slug or line.category
    keywords.discard("")
    others = tuple(
        (ln.name, tuple(sorted({norm(k) for k in (*ln.keywords, ln.name)} - keywords)))
        for ln in brand.lines
        if fold(ln.name) != folded
    )
    aliases = {norm(a) for a in (*brand.aliases, brand.name)} - {""}
    other_brands = tuple(
        sorted(
            {
                norm(b.name)
                for b in taxonomy.all_brands
                if b.slug != brand_slug and len(norm(b.name)) >= 4 and norm(b.name) not in aliases
            }
        )
    )
    return ModelSpec(
        brand_slug=brand_slug,
        brand_name=brand.name,
        model_name=line.name if line is not None else model_name,
        category_slug=category_slug,
        aliases=tuple(sorted(aliases, key=len, reverse=True)),
        keywords=tuple(sorted(keywords, key=len, reverse=True)),
        others=others,
        other_brands=other_brands,
        distinctive=any(fold(k) in DISTINCTIVE_MODEL_KEYWORDS for k in (line.keywords if line else ())),
    )


# ---------------------------------------------------------------------------- noise markers
_NEGATION = re.compile(r"\b(non|no|not|nessun[oa]?|niente|zero|mai|never|kein[e]?|pas|ni)\b(\s+\w+){0,2}\s*$")
_REPLICA = [re.compile(p) for p, _ in SUSPICIOUS_PATTERNS] + [
    re.compile(r"\bcustom made\b|\bcustomi[sz]ed\b|\bcustomizzat[oaie]\b|\bpersonalizzat[oaie]\b"),
    re.compile(r"\bispirat[oaie]\b|\binspired by\b|\bunauthorized\b"),
]
# Page furniture, not a description of the item ("Visualizza articoli simili" on eBay).
_FURNITURE = re.compile(r"\b(articoli|oggetti|prodotti|annunci) simili\b|\bsimilar (items|products)\b")
# "stile Nike", "tipo Air Max", "like Nike": a lookalike, only when the brand or model follows.
_LOOKALIKE = r"\b(?:stile|tipo|like|style|simil|genere|modello tipo)\s+(?:\w+\s+)?"

_KIDS_WORDS = re.compile(
    r"\b(bambin[oaie]|bimb[oaie]|kids?|junior|jr|boys?|girls?|enfants?|kinder\w*|nin[oa]s?|"
    r"toddlers?|neonat[oaie]|infants?|youth|grade school|little kids|big kids|"
    r"baby(?!\s+(?:blue|pink|blu|rosa|celeste|azzurro)))\b"
)
_KIDS_AGE_TITLE = re.compile(r"\b\d{1,2}\s*(?:-\s*\d{1,2}\s*)?(?:anni|mesi|years?|ans|jahre|anos|months?)\b")
_KIDS_AGE_SNIPPET = re.compile(
    r"\b(?:taglia|tg|eta|age|size|talla|taille)\s*:?\s*\d{1,2}\s*(?:-\s*\d{1,2}\s*)?(?:anni|mesi|years?|ans|jahre|anos)\b"
)
_KIDS_Y = re.compile(r"\b\d{1,2}(?:[.,]5)?\s?y\b|\by\s?o\b|\b\d{1,2}(?:[.,]5)?c\b")
_KIDS_SHOE_MARKS = re.compile(r"\b(gs|ps|td)\b")
_SHOE_SIZE = re.compile(
    r"\b(?:eu|eur|taglia|tg|size|numero|misura|n|nr|gr|talla|pointure)\s*:?\s*(\d{2}(?:[.,]5)?)\b(?!\s*(?:uk|us|cm))"
    r"|\b(\d{2}(?:[.,]5)?)\s*(?:eu|eur)\b"
)
_CLOTHING_AGE_SIZE = re.compile(r"\b(?:taglia|tg)\s*:?\s*(\d{1,2})\b(?!\s*(?:uk|us|it|eu|fr|de|w|l))")

_LOT = re.compile(
    r"\b(lott[oi]|bundle|konvolut|lot of|lot de|set di|set of|multipack|stock)\b"
    r"|\b\d+\s*-?\s*pack\b|\bpack\s+(?:da|di|of|de)\s+\d+\b"
    r"|\bx\s?[2-9]\b|\b[2-9]\s?x\b"
    r"|\b[2-9]\d?\s*(?:pezzi|paia|pairs|pcs|pieces|stuck|unita|articoli|capi|items)\b"
)
_STOCK_OK = re.compile(r"\b(in|out of|esaurit\w*)\s+stock\b|\bstock\s?x\b")
_KIT = re.compile(r"\bkit\b")
_ACCESSORY = re.compile(
    r"\b(lacci|laces|shoelaces|adesiv[oi]|stickers?|toppa|toppe|portachiavi|keychain|keyring|"
    r"solette|soletta|insoles|plantari|charms?|jibbitz|scatola vuota|solo scatola|solo la scatola|"
    r"box only|only box|empty box|solo lacci|only laces|ricambio|replacement)\b"
)
_ACCESSORY_WITH = re.compile(r"\b(con|with|e|and|incl\w*|inclus\w*|plus|piu|extra)\s*(?:\w+\s+){0,1}$")


@dataclass
class Match:
    ok: bool
    reason: str | None = None
    detail: str | None = None
    score: float = 0.0
    info: dict[str, Any] = field(default_factory=dict)


def _replica(text: str, spec: ModelSpec) -> str | None:
    text = _FURNITURE.sub(" ", text)
    for pattern in _REPLICA:
        for m in pattern.finditer(text):
            if not _NEGATION.search(text[max(0, m.start() - 25) : m.start()]):
                return m.group(0)
    targets = "|".join(re.escape(x) for x in (*spec.aliases, *spec.keywords))
    if targets and (m := re.search(rf"{_LOOKALIKE}(?:{targets})(?![\w&])", text)):
        return m.group(0)
    return None


def _kids(title: str, snippet: str, spec: ModelSpec) -> str | None:
    both = f"{title} {snippet}".strip()
    if m := _KIDS_WORDS.search(both):
        return m.group(0)
    if m := _KIDS_AGE_TITLE.search(title):
        return m.group(0)
    if m := _KIDS_AGE_SNIPPET.search(snippet):
        return m.group(0)
    if m := _KIDS_Y.search(both):
        return m.group(0)
    if spec.footwear:
        if m := _KIDS_SHOE_MARKS.search(title):
            return m.group(0)
        for m in _SHOE_SIZE.finditer(both):
            value = float((m.group(1) or m.group(2)).replace(",", "."))
            if 16 <= value < 35:
                return m.group(0)
    else:
        # Ages 8-16 ("taglia 12"); smaller numbers are adult sizes for some brands (Moncler 0-7).
        for m in _CLOTHING_AGE_SIZE.finditer(both):
            if 8 <= int(m.group(1)) <= 16:
                return m.group(0)
    return None


def _lot(text: str, spec: ModelSpec) -> str | None:
    for m in _LOT.finditer(text):
        if m.group(0) == "stock" and _STOCK_OK.search(text[max(0, m.start() - 12) : m.end() + 3]):
            continue
        return m.group(0)
    if spec.category_slug not in FOOTBALL and (m := _KIT.search(text)):
        return m.group(0)
    return None


def _accessory(title: str) -> str | None:
    for m in _ACCESSORY.finditer(title):
        if not _ACCESSORY_WITH.search(title[max(0, m.start() - 20) : m.start()]):
            return m.group(0)
    return None


def _number_mismatch(title: str, spec: ModelSpec) -> str | None:
    """ "air max 95" or "air max 1" in a result for "Air Max 90" (years such as "nuptse 1996" are
    not model numbers)."""
    for kw in spec.keywords:
        tokens = kw.split()
        if len(tokens) < 2 or not tokens[-1].isdigit():
            continue
        stem, number = " ".join(tokens[:-1]), tokens[-1]
        for m in re.finditer(rf"(?<![\w&]){re.escape(stem)}\s*(\d+)([a-z]?)(?![\w&])", title):
            other = m.group(1)
            if other == number or (len(other) == 4 and 1950 <= int(other) <= 2035):
                continue
            return m.group(0)
    return None


def match_offer(spec: ModelSpec, title: str, snippet: str | None = None) -> Match:
    """Accept or reject one result for ``spec``; the score (0.5-1.0) says how directly it names
    the model."""
    t, s = norm(title), norm(snippet)
    raw = f"{fold(title)} {fold(snippet or '')}"
    brand_title = next(
        (a for a, p in zip(spec.aliases, spec.brand_patterns, strict=True) if p.search(t)), None
    )
    brand_snippet = next(
        (a for a, p in zip(spec.aliases, spec.brand_patterns, strict=True) if p.search(s)), None
    )
    model_kw = next((k for k, p in zip(spec.keywords, spec.model_patterns, strict=True) if p.search(t)), None)
    if not (brand_title or brand_snippet or (spec.distinctive and model_kw)):
        return Match(False, "brand")
    own = _spans(spec.model_patterns, t) + _spans(spec.brand_patterns, t)
    for name, keywords in spec.others:
        for start, end in _spans([_phrase(k) for k in keywords], t):
            if not any(a <= start and end <= b for a, b in own):
                return Match(False, "other_model", name)
    if detail := _number_mismatch(t, spec):
        return Match(False, "other_model", detail)
    if model_kw is None:
        return Match(False, "model")
    for other in (*spec.other_brands, *COLLABS):
        o = norm(other)
        if o and _phrase(o).search(t) and not any(o in a for a in spec.aliases):
            return Match(False, "other_model", other)
    if detail := _replica(raw, spec):
        return Match(False, "replica", detail)
    if detail := _kids(t, s, spec):
        return Match(False, "kids", detail)
    if detail := _lot(f"{t} {s}", spec):
        return Match(False, "lot", detail)
    if detail := _accessory(t):
        return Match(False, "accessory", detail)
    score = 1.0
    if not brand_title:
        score -= 0.15
    if model_kw != norm(spec.model_name):
        score -= 0.1
    return Match(
        True,
        score=round(max(score, 0.5), 2),
        info={"brand": brand_title or brand_snippet, "model": model_kw},
    )
