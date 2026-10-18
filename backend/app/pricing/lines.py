"""What makes two listings "the same product line", independent of the model catalog.

The model catalog knows only a few dozen lines; most titles carry a line name it does not know
("Nike Revolution 6", "Adidas Forum"). Pricing such an item against everything else of the brand
(an Air Max next to a running shoe) is the failure behind most wrong "buy" calls, so the comparables
are tiered (see ``app.pricing.comparables``) and the tier of an unknown line is decided here, on the
words of the title that are left once everything the taxonomy already names is taken away: brand,
category, colour, material, audience, condition, size and the usual selling chatter.

Also here, small and additive: the *audience* (kids or adult: a child's jacket is not an adult one
whatever the brand and model) and the *variant* (low / mid / high: a Jordan 1 Low is worth a fraction
of a High, yet both read as the same model string). Everything is pure and memoised; listing text is
untrusted and only ever compared, never executed or trusted as a fact.
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.identification.taxonomy import (
    COLORS,
    CONDITION_PHRASES,
    DEFAULT_TAXONOMY,
    GENDER_KEYWORDS,
    MATERIALS,
    fold,
)

_TOKEN = re.compile(r"[a-z0-9]+")
_DIGITS = re.compile(r"\d+")

# Words that say nothing about the line: connectors, selling chatter, size and season words.
_GENERIC = frozenset(
    """
    di da del della delle dei degli con senza per in su tra fra e ed o a il lo la le i gli un una uno al alla ai
    dal dalla nel nella the and of with for from by new used like very good great nice rare ok
    bellissime bellissimo bellissima bellissimi perfette perfetto perfetta perfetti ottime ottimo ottima ottimi
    nuove nuovo nuova nuovi usate usato usata usati indossato indossata indossate indossati
    originale originali original authentic autentico autentica autentiche autentici garantito garantita
    pari paio paia coppia pezzo pezzi stock lotto 100
    spedizione spedito spedita spedisco gratis gratuita immediata veloce tracciata tracciabile
    vendo vendesi vendita regalo offerta offerte sconto sconti prezzo prezzi trattabile fisso ultimo ultima
    taglia taglie tg size misura misure numero num nr n eu it uk us fr de cm mm xs s m l xl xxl xxxl xxxxl 2xl 3xl 4xl
    unica tu os w
    vintage limited limitata limitato edition edizione collezione collection stagione season
    fw ss aw autunno inverno primavera estate
    condizioni condizione stato buone buono ottimo seminuovo come nuovo perfetto
    colore colori
    running corsa ginnastica trail training sport sportive sportivo casual
    bianche bianchi nere rosse rossi gialle gialli verdi grigie grigi azzurre azzurri marroni blu rosa viola beige
    """.split()
)


def _build_stop() -> frozenset[str]:
    """Everything the taxonomy already names (brands, categories, colours, ...), as folded tokens."""
    words: list[str] = []
    for brand in DEFAULT_TAXONOMY.all_brands:
        words += [brand.name, brand.slug.replace("-", " "), *brand.aliases]
    line_tokens: set[str] = set()  # a category keyword that is also a line keyword stays a line word
    for brand in DEFAULT_TAXONOMY.all_brands:
        for line in brand.lines:
            for kw in line.keywords:
                line_tokens.update(_TOKEN.findall(fold(kw)))
    cat_words: list[str] = []
    for cat in DEFAULT_TAXONOMY.categories:
        cat_words += [cat.name, cat.slug.replace("-", " "), *cat.keywords]
    for vocab in (COLORS, MATERIALS, GENDER_KEYWORDS):
        for ws in vocab.values():
            words += list(ws)
    for _, phrases in CONDITION_PHRASES:
        words += list(phrases)
    out = set(_GENERIC)
    for w in words:
        out.update(_TOKEN.findall(fold(w)))
    for w in cat_words:
        out.update(t for t in _TOKEN.findall(fold(w)) if t not in line_tokens)
    return frozenset(out)


_STOP = _build_stop()

_KIDS = re.compile(
    r"(?<!\w)(?:bambino|bambina|bambini|bambine|bimbo|bimba|bimbi|kids?|enfants?|nino|nina|ninos|junior|"
    r"boys?|girls?|ragazzo|ragazza|ragazzi|ragazze|neonato|neonata)(?!\w)"
)
_HEIGHT = re.compile(r"(?<!\w)(high|hi|mid|low)(?!\w)")
_FOOTWEAR_PARENTS = frozenset({"footwear"})
_FOOTWEAR_CATEGORIES = frozenset({"sneakers", "footwear"})


@lru_cache(maxsize=131072)
def audience_of(title: str, gender: str | None) -> str | None:
    """"kids" or "adult"; ``None`` when nothing says (never guessed: unknown stays unknown).

    A kids' word in the title wins over the gender field (a child's item filed as "men" by a
    careless seller is still a child's item); otherwise the gender field decides.
    """
    if gender == "kids" or _KIDS.search(fold(title)):
        return "kids"
    if gender in ("men", "women", "unisex"):
        return "adult"
    return None


@lru_cache(maxsize=131072)
def variant_of(title: str, category: str | None, parent: str | None) -> str | None:
    """"low" | "mid" | "high" for footwear whose title states exactly one of them, else ``None``.

    Only footwear: "high" or "low" in the title of a jacket is just a word. Two different heights
    in one title are ambiguous and read as unknown.
    """
    if category not in _FOOTWEAR_CATEGORIES and parent not in _FOOTWEAR_PARENTS:
        return None
    found = {"high" if m == "hi" else m for m in _HEIGHT.findall(fold(title))}
    return next(iter(found)) if len(found) == 1 else None


def profile_extras(
    title: str, gender: str | None, category: str | None, parent: str | None
) -> dict[str, str | None]:
    """The ``audience`` and ``variant`` fields of an ``ItemProfile``, derived from what the listing says."""
    return {"audience": audience_of(title, gender), "variant": variant_of(title, category, parent)}


@lru_cache(maxsize=131072)
def line_tokens(title: str, size: str | None = None, brand: str | None = None) -> frozenset[str]:
    """The distinctive words of a title: what is left of it once the known vocabulary is taken away.

    ``size`` (the listing's normalised size, e.g. "EU 42") removes the size number from the title;
    ``brand`` (slug) removes brand words of brands the taxonomy does not know. Single letters are
    dropped, digits kept (a line number is part of the line: "Revolution 6").
    """
    size_numbers = set(_DIGITS.findall(fold(size))) if size else set()
    brand_words = set(_TOKEN.findall(fold(brand.replace("-", " ")))) if brand else set()
    return frozenset(
        t
        for t in _TOKEN.findall(fold(title))
        if t not in _STOP
        and t not in size_numbers
        and t not in brand_words
        and (len(t) > 1 or t.isdigit())
    )


# Share of the smaller key that must be shared for two titles to be the same line: both words of
# a two-word line, two of three, ... ("revolution 6" is not "revolution 5", "blazer mid" is not "blazer low").
LINE_OVERLAP = 0.6


def _numbers(key: frozenset[str]) -> frozenset[str]:
    return frozenset(t for t in key if any(ch.isdigit() for ch in t))


def line_overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Shared share of the smaller key, 0..1 (0 when either is empty).

    Model numbers and codes decide: "air max 97" is not "air max 90" (two of three words in common, yet
    another shoe), so when both keys carry numbers and none is shared the lines are different.
    """
    if not a or not b:
        return 0.0
    na, nb = _numbers(a), _numbers(b)
    if na and nb and not na & nb:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def same_line(a: frozenset[str], b: frozenset[str]) -> bool:
    return line_overlap(a, b) >= LINE_OVERLAP


def title_words(title: str) -> frozenset[str]:
    """All folded word tokens of a title (to look for a model's words in a listing the catalog left unnamed)."""
    return _words(title)


@lru_cache(maxsize=131072)
def _words(title: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(fold(title)))


@lru_cache(maxsize=4096)
def model_words(model: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(fold(model)))
