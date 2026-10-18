"""Product Identification Engine.

Turns a noisy listing (incomplete title, free-text description, optional structured fields,
optional image analysis) into a structured product identity with per-attribute certainty:

* ``certain``       - given by the marketplace in a structured field (or read on a label);
* ``probable``      - inferred from title/description/photos with reasonable evidence;
* ``unverifiable``  - unknown or not confirmable.

Every attribute also says where it comes from (``provenance``): ``declared`` (a structured field the seller
filled in), ``observed`` (read on the photos) or ``inferred`` (our reading of free text). A guess is not a
fact: a brand found only in a description, behind a short or ordinary-word alias, as a misspelling or from a
model name that many brands share, is kept apart as ``brand_guess`` and is never the brand the comparables
are filtered on. Contradictions between the fields and the text are listed and lower the confidence.

It never claims authenticity: the best it states is "no red flags found"; suspicious wording
or visual concerns are surfaced as risk signals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from app.analysis.defects import extract_defects
from app.analysis.lots import lot_pieces
from app.domain.enums import Certainty
from app.identification.taxonomy import (
    CATALOG_ID_CATEGORIES,
    CATALOG_SLUG_RULES,
    CLAIM_CUES,
    CRUMB_GROUPS,
    CRUMB_ROOT_GENDER,
    CRUMB_RULES,
    DEFAULT_TAXONOMY,
    DISTINCTIVE_MODEL_KEYWORDS,
    DOCUMENT_WORDS,
    FOOTBALL_CONTEXT,
    FOOTBALL_TEAMS,
    GENDER_KEYWORDS,
    GENERIC_CATEGORY_KEYWORDS,
    NEGATION_WORDS,
    PRODUCT_ALIASES,
    PRODUCT_CODE_PATTERNS,
    SEASON_PATTERN,
    SUSPICIOUS_PATTERNS,
    VINTAGE_PATTERN,
    WEAK_MODEL_KEYWORDS,
    BrandSpec,
    Taxonomy,
    catalog_code,
    fold,
    plausible_product_code,
    related_brands,
)
from app.ingestion.normalizer import (
    normalize_color,
    normalize_material,
    normalize_size,
    size_distance,
    size_is_ambiguous,
)
from app.vision.types import ImageAnalysis

SHORT_ALIAS_LEN = 3
# Below this a brand is a guess: it is shown, it lowers the confidence, and it filters nothing.
FIRM_BRAND = 0.8
SUB_LINES = {
    "polo ralph lauren": "Polo Ralph Lauren",
    "polo by ralph lauren": "Polo Ralph Lauren",
    "tommy jeans": "Tommy Jeans",
    "adidas originals": "Adidas Originals",
    "nike sportswear": "Nike Sportswear",
    "carhartt wip": "Carhartt WIP",
}
WINTER = {"puffer-jackets", "coats", "knitwear", "fleece", "boots"}
SUMMER = {"polo-shirts", "t-shirts", "shorts", "swimwear", "sandals"}
# A brand field that says there is no brand: it is "absent", not "a brand outside the catalogue".
NO_BRAND_FIELDS = frozenset(
    {"altro", "other", "otro", "autre", "sonstige", "senza marca", "senza marchio", "no brand", "unbranded",
     "non specificato", "sconosciuto", "unknown", "vintage", "generico", "generic", "-", "n/a", "na"}
)  # fmt: skip
PROVENANCE = {"provider_field": "declared", "image": "observed", "image_label": "observed"}
# Words next to a brand in a description that make it a comparison or an accessory ("abbinabile a pantaloni Tommy"),
# or that say it is the item's brand ("Marca Moncler", "Ralph Lauren originale").
_COMPARISON_BEFORE = frozenset(
    [
        "abbinabile",
        "abbinato",
        "abbinata",
        "simile",
        "come",
        "tipo",
        "stile",
        "style",
        "like",
        "compatibile",
        "adatto",
        "adatta",
        "ispirato",
        "ispirata",
        "rispetto",
        "confronto",
        "invece",
        "oppure",
        "vs",
        "versus",
        "ne",
        "non",
        "no",
        "not",
        "senza",
        "paragonabile",
        "pari",
        "insieme",
    ]
)
_AFTER_NEGATING = re.compile(
    r"\b(?:non inclus\w+|not included|escluse?|esclusi?|solo (?:nelle|in) foto|only in (?:the )?photos?)\b"
)
_BRAND_CUES = frozenset(
    [
        "marca",
        "brand",
        "marque",
        "marke",
        "marchio",
        "firmato",
        "firmata",
        "by",
        "originale",
        "original",
        "authentic",
        "autentico",
        "autentica",
        "genuine",
        "griffato",
        "griffata",
        "di",
    ]
)
_STRONG_CUES = _BRAND_CUES - {"di"}


@dataclass
class Attribute:
    value: str | None = None
    certainty: Certainty = Certainty.UNVERIFIABLE
    source: str | None = None
    confidence: float = 0.0

    @property
    def provenance(self) -> str | None:
        """``declared`` (a field the seller filled in), ``observed`` (read on the photos) or ``inferred``."""
        if self.value is None:
            return None
        return PROVENANCE.get(self.source or "", "inferred")

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "certainty": self.certainty.value,
            "source": self.source,
            "provenance": self.provenance,
            "confidence": round(self.confidence, 2),
        }

    @property
    def known(self) -> bool:
        return self.value is not None


@dataclass
class ListingText:
    """Inputs of the identification engine (decoupled from ORM and provider DTOs)."""

    title: str
    description: str = ""
    brand_field: str | None = None
    category_field: str | None = None
    subcategory_field: str | None = None
    size_field: str | None = None
    condition: str | None = None
    color_field: str | None = None
    material_field: str | None = None
    # The marketplace's category entries as catalogue ids and slugs ("5492-polo-shirts"): the same on every
    # domain, whatever the language of the page.
    category_codes: tuple[str, ...] = ()


@dataclass
class IdentificationResult:
    brand: Attribute = field(default_factory=Attribute)
    brand_name: str | None = None
    # A brand the text suggests without proving it (see the module docstring): never used as a filter.
    brand_guess: Attribute = field(default_factory=Attribute)
    brand_guess_name: str | None = None
    line: Attribute = field(default_factory=Attribute)
    model: Attribute = field(default_factory=Attribute)
    category: Attribute = field(default_factory=Attribute)
    gender: Attribute = field(default_factory=Attribute)
    color: Attribute = field(default_factory=Attribute)
    size: Attribute = field(default_factory=Attribute)
    material: Attribute = field(default_factory=Attribute)
    seasonality: Attribute = field(default_factory=Attribute)
    season: Attribute = field(default_factory=Attribute)
    team: Attribute = field(default_factory=Attribute)
    product_code: Attribute = field(default_factory=Attribute)
    authenticity: Attribute = field(
        default_factory=lambda: Attribute("not_verifiable", Certainty.UNVERIFIABLE)
    )
    is_vintage: bool = False
    suspicious_terms: list[str] = field(default_factory=list)
    defect_terms: list[str] = field(default_factory=list)
    # Each declared defect: kind, severity and the phrase it was read from (provenance "declared").
    defect_hits: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    # Where the fields and the text disagree: {field, declared, found, where, severity}.
    contradictions: list[dict[str, str]] = field(default_factory=list)
    # Hidden opportunities and seller claims: misspelled_brand, category_mismatch, claimed_brand,
    # unknown_brand, claimed_original_price (never verified facts).
    flags: dict[str, Any] = field(default_factory=dict)
    confidence: int = 0

    @property
    def product_key(self) -> str | None:
        if not (self.brand.value and self.category.value):
            return None
        model = fold(self.model.value) if self.model.value else "-"
        gender = self.gender.value or "-"
        return f"{self.brand.value}|{self.category.value}|{model}|{gender}"

    def canonical_name(self, taxonomy: Taxonomy = DEFAULT_TAXONOMY) -> str:
        parts: list[str] = []
        if self.brand_name:
            parts.append(self.brand_name)
        if self.model.value:
            parts.append(self.model.value)
        if self.season.value:
            parts.append(self.season.value)
        if self.category.value and (cat := taxonomy.category_by_slug.get(self.category.value)):
            parts.append(cat.name)
        return " ".join(parts) or "Prodotto non identificato"

    def as_dict(self) -> dict[str, Any]:
        return {
            "brand": self.brand.as_dict() | {"name": self.brand_name},
            "brand_guess": self.brand_guess.as_dict() | {"name": self.brand_guess_name},
            "line": self.line.as_dict(),
            "model": self.model.as_dict(),
            "category": self.category.as_dict(),
            "gender": self.gender.as_dict(),
            "color": self.color.as_dict(),
            "size": self.size.as_dict(),
            "material": self.material.as_dict(),
            "seasonality": self.seasonality.as_dict(),
            "season": self.season.as_dict(),
            "team": self.team.as_dict(),
            "product_code": self.product_code.as_dict(),
            "authenticity": self.authenticity.as_dict(),
            "is_vintage": self.is_vintage,
            "suspicious_terms": self.suspicious_terms,
            "defect_terms": self.defect_terms,
            "defect_hits": self.defect_hits,
            "evidence": self.evidence,
            "contradictions": self.contradictions,
            "flags": self.flags,
            "confidence": self.confidence,
        }


_WORD = re.compile(r"\w+")
# "pagata 250€", "prezzo di listino 180 euro", "retail 300 €" in the (folded) description.
CLAIMED_PRICE = re.compile(
    r"(?:pagat[oaie]|prezzo (?:di |d')?(?:listino|originale|negozio|acquisto)|costo originale|retail|"
    r"costava|acquistat[oaie] a|paid|original price|rrp)\D{0,20}?(\d{2,5}(?:[.,]\d{1,2})?)\s?(?:€|eur|euro)"
)
# An explicit size in a title: "taglia 42 (S)", "tg. M", "size L".
_TITLE_SIZE = re.compile(
    r"\b(?:tg|taglia|size|talla|taille)\.?\s*([a-z0-9.,/]{1,6}(?:\s*[(/]\s*[a-z0-9]{1,4}\s*\)?)?)(?=\W|$)(?!['’])"
)
_TITLE_WAIST = re.compile(r"\bw\s?(2[4-9]|3\d|4[0-4])\b")
_CLAIM_CUE = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(fold(c)) for c in sorted(CLAIM_CUES, key=len, reverse=True))
    + r")(?:\s+(?:a|di|de|by|of|the|an?))?\s+"
)
_CLAIM_AFTER = re.compile(r"^\s+(?:style|stile|inspired|ispirat[oa]|like|tipo)(?!\w)")
_SENTENCE_BREAK = re.compile(r"[.,;:!?()\n]")
_SENTENCE_END = re.compile(r"[.;!?\n]")
_COLOR_FAMILIES = (
    {"blue", "navy", "light_blue", "turquoise"},
    {"grey", "black", "silver"},
    {"beige", "khaki", "brown", "gold"},
    {"red", "burgundy", "pink", "orange"},
    {"white", "beige"},
)


def _gate(literal: str) -> frozenset[str]:
    """Word tokens of a keyword. A keyword regex bounded by non-word characters can only match
    if all of these tokens occur in the text, so a cheap subset test skips most regex calls
    without ever changing a result."""
    return frozenset(_WORD.findall(literal))


@lru_cache(maxsize=4096)
def _tokens(text: str) -> frozenset[str]:
    return frozenset(_WORD.findall(text))


# (brand, alias, span, weak)
BrandMatch = tuple[BrandSpec, str, tuple[int, int], bool]


class IdentificationEngine:
    def __init__(self, taxonomy: Taxonomy = DEFAULT_TAXONOMY) -> None:
        self.tax = taxonomy
        # Every keyword entry carries its word tokens ("gate"): see _gate().
        self._line_index: list[tuple[frozenset[str], re.Pattern[str], BrandSpec, str, str]] = []
        for brand in taxonomy.all_brands:
            for line in brand.lines:
                for kw in line.keywords:
                    self._line_index.append(
                        (
                            _gate(fold(kw)),
                            re.compile(rf"(?<!\w){re.escape(fold(kw))}(?!\w)"),
                            brand,
                            line.name,
                            line.category,
                        )
                    )
        self._line_index.sort(key=lambda e: len(e[1].pattern), reverse=True)
        self._distinctive = [
            (
                _gate(fold(kw)),
                re.compile(rf"(?<!\w){re.escape(fold(kw))}(?!\w)"),
                brand,
                fold(kw),
                line.category,
            )
            for brand in taxonomy.all_brands
            for line in brand.lines
            for kw in line.keywords
            if fold(kw) in DISTINCTIVE_MODEL_KEYWORDS
        ]
        self._team_patterns = [
            (
                _gate(fold(a)),
                re.compile(rf"(?<!\w){re.escape(fold(a))}(?!\w)"),
                team,
                fold(a) in {fold(s) for s in strong},
            )
            for team, aliases, strong in FOOTBALL_TEAMS
            for a in sorted(aliases, key=len, reverse=True)
        ]
        self._suspicious = [(re.compile(p), label) for p, label in SUSPICIOUS_PATTERNS]
        self._gender = [
            (_gate(w), re.compile(rf"(?<!\w){re.escape(w)}(?!\w)"), g)
            for g, words in GENDER_KEYWORDS.items()
            for w in words
        ]
        weak_by_brand = {b.slug: {fold(a) for a in b.weak_aliases} for b in taxonomy.all_brands}
        self._brand_aliases = [
            (
                _gate(alias),
                pattern,
                brand,
                alias,
                len(alias) <= SHORT_ALIAS_LEN or alias in weak_by_brand.get(brand.slug, ()),
            )
            for pattern, brand, alias in taxonomy.brand_alias_patterns
        ]
        # An alias can match only if every one of its words is in the text, so the text's own words find the few
        # candidates: aliases are indexed by their first word (their order, longest first, is kept by index).
        self._alias_by_word: dict[str, list[int]] = {}
        for idx, entry in enumerate(self._brand_aliases):
            first = sorted(entry[0])[0] if entry[0] else ""
            self._alias_by_word.setdefault(first, []).append(idx)
        self._match_brands_cached = lru_cache(maxsize=8192)(self._match_brands_uncached)
        # Brand names and long aliases for misspellings ("Ralf Loren", "Carhart"): short aliases
        # are excluded, they would match ordinary words.
        fuzzy = sorted(
            {
                (fold(a), brand)
                for brand in taxonomy.all_brands
                for a in (brand.name, *brand.aliases)
                if len(fold(a)) >= 5 and fold(a) not in weak_by_brand.get(brand.slug, ())
            },
            key=lambda x: x[0],
        )
        # A misspelling keeps the first two letters ("carhart", "ralf loren"): aliases are looked up by them.
        self._fuzzy_by_prefix: dict[str, list[tuple[str, BrandSpec]]] = {}
        for alias, brand in fuzzy:
            self._fuzzy_by_prefix.setdefault(alias[:2], []).append((alias, brand))
        self._category_keywords = [
            (_gate(kw), pattern, cat, kw) for pattern, cat, kw in taxonomy.category_keyword_patterns
        ]
        self._crumb_rules = [(re.compile(p), slug) for p, slug in CRUMB_RULES]
        self._slug_rules = [(re.compile(p), slug) for p, slug in CATALOG_SLUG_RULES]
        self._leaf_slugs = {c.slug for c in taxonomy.leaf_categories()}

    # ------------------------------------------------------------------ public
    def identify(self, item: ListingText, vision: ImageAnalysis | None = None) -> IdentificationResult:
        res = IdentificationResult()
        title = fold(item.title)
        desc = fold(item.description or "")
        title_masked, desc_masked = self._brand(item, title, desc, res, vision)
        self._team_and_season(title_masked, desc_masked, res)
        title_for_category = self._model(title_masked, desc_masked, res, vision)
        self._category(item, title_for_category, desc_masked, res, vision)
        self._gender_attr(item, title, desc, res)
        self._simple_attributes(item, title, desc, res, vision)
        self._signals(item, title, desc, res, vision)
        self._contradictions(item, title, desc, res)
        res.confidence = self._confidence(res)
        return res

    # ----------------------------------------------------------------- helpers
    def _match_brands(self, text: str, *, allow_weak: bool = True) -> list[BrandMatch]:
        """Every brand alias in ``text`` (longest alias first, each stretch of text counted once)."""
        return list(self._match_brands_cached(text, allow_weak))

    def _match_brands_uncached(self, text: str, allow_weak: bool) -> tuple[BrandMatch, ...]:
        tokens = _tokens(text)
        candidates = sorted({i for t in tokens for i in self._alias_by_word.get(t, ())})
        found: list[BrandMatch] = []
        masked = text
        for idx in candidates:
            gate, pattern, brand, alias, weak = self._brand_aliases[idx]
            if (weak and not allow_weak) or not gate <= tokens:
                continue
            if m := pattern.search(masked):
                found.append((brand, alias, m.span(), weak))
                masked = self._mask(masked, m.span())
        return tuple(found)

    def _match_brand(self, text: str) -> tuple[BrandSpec, str, tuple[int, int]] | None:
        found = self._match_brands(text)
        return (found[0][0], found[0][1], found[0][2]) if found else None

    @staticmethod
    def _mask(text: str, span: tuple[int, int] | None) -> str:
        if not span:
            return text
        return text[: span[0]] + " " * (span[1] - span[0]) + text[span[1] :]

    def _claims(self, text: str) -> list[tuple[BrandSpec, str, tuple[int, int]]]:
        """ "tipo Nike", "stile Stone Island", "Nike style": the seller names a brand to say the item is NOT it.
        Returns (brand, phrase, span) for each; the span is what must not count as the item's brand."""
        out: list[tuple[BrandSpec, str, tuple[int, int]]] = []
        for m in _CLAIM_CUE.finditer(text):
            if hit := self._leading_brand(text[m.end() : m.end() + 40]):
                brand, alias = hit
                end = m.end() + len(alias)
                out.append((brand, text[m.start() : end].strip(), (m.start(), end)))
        for brand, _alias, span, _weak in self._match_brands(text, allow_weak=False):
            if tail := _CLAIM_AFTER.match(text[span[1] :]):
                end = span[1] + tail.end()
                out.append((brand, text[span[0] : end].strip(), (span[0], end)))
        return out

    def _leading_brand(self, rest: str) -> tuple[BrandSpec, str] | None:
        tokens = _tokens(rest)
        for idx in sorted({i for t in tokens for i in self._alias_by_word.get(t, ())}):
            gate, pattern, brand, alias, _weak = self._brand_aliases[idx]
            if gate <= tokens and pattern.match(rest):
                return brand, alias
        return None

    def _brand(
        self,
        item: ListingText,
        title: str,
        desc: str,
        res: IdentificationResult,
        vision: ImageAnalysis | None,
    ) -> tuple[str, str]:
        # A brand named only to say the item is *not* it is a claim, never the item's brand.
        claims = [(c, "title") for c in self._claims(title)] + [
            (c, "description") for c in self._claims(desc)
        ]
        for (brand, phrase, _), where in claims:
            res.flags.setdefault(
                "claimed_brand",
                {"brand": brand.name, "slug": brand.slug, "phrase": phrase[:40], "where": where},
            )
        for (_, _, span), where in claims:
            if where == "title":
                title = self._mask(title, span)
            else:
                desc = self._mask(desc, span)

        raw_field = (item.brand_field or "").strip()
        field_text = "" if fold(raw_field) in NO_BRAND_FIELDS else fold(raw_field)
        field_hits = self._match_brands(field_text) if field_text else []
        field_match = field_hits[0] if field_hits else None
        title_hits = self._match_brands(title)
        strong_title = [h for h in title_hits if not h[3]]
        title_match = (strong_title or title_hits or [None])[0]
        desc_hits = self._match_brands(desc, allow_weak=False)
        title_span = title_match[2] if title_match else None
        if title_match and title_match[1].startswith("polo ") and title_span:
            title_span = (title_span[0] + 5, title_span[1])  # "polo ralph lauren": "polo" is still the kind of item
        if title_match and title_match[1] in PRODUCT_ALIASES:
            title_span = None  # "Air Jordan 1": the brand's name is also the product's
        title_masked = self._mask(title, title_span)
        desc_masked = self._mask(desc, desc_hits[0][2] if desc_hits else None)

        chosen: tuple[BrandSpec, str] | None = None
        if field_match:
            brand, alias, _, _ = field_match
            res.brand = Attribute(brand.slug, Certainty.CERTAIN, "provider_field", 0.98)
            chosen = (brand, alias)
            lot = bool(lot_pieces(title) or lot_pieces(desc))  # a bundle may name several brands
            if not lot and (
                clash := next((h for h in strong_title if not related_brands(h[0].slug, brand.slug)), None)
            ):
                # The field and the title name different brands: one of them is wrong, so neither is a filter.
                res.brand = Attribute()
                chosen = None
                res.flags["brand_conflict"] = {"field": brand.name, "title": clash[0].name}
                self._set_guess(res, brand, "provider_field", 0.5, f"il titolo cita '{clash[0].name}'")
                res.evidence.append(f"Il titolo cita '{clash[0].name}' ma il brand indicato è '{brand.name}'")
        elif field_text:
            if fz := self._fuzzy_brand(field_text):
                self._misspelled(res, *fz)
            else:
                # The marketplace names a brand the catalogue does not know: it stays unknown (a word of the title
                # does not replace it) and the doubt about authenticity is raised, not lowered.
                res.flags["unknown_brand"] = raw_field[:60]
                res.evidence.append(
                    f"Brand '{raw_field[:60]}' non presente nel catalogo interno: marca sconosciuta, "
                    "autenticità non valutabile dal solo testo"
                )
                if strong_title:
                    self._set_guess(
                        res,
                        strong_title[0][0],
                        "title",
                        0.5,
                        "citato nel titolo, ma il campo marca dice altro",
                    )
            return title_masked, desc_masked
        elif title_match:
            brand, alias, _, weak = title_match
            # A short or ordinary-word alias is a hint only; a model name that belongs to that brand alone
            # ("TNF Nuptse 700") confirms it.
            inferred = self._brand_from_lines(title, desc) if weak else None
            if weak and not (inferred and inferred[1] and inferred[0].slug == brand.slug):
                self._set_guess(res, brand, "title", 0.6, "alias breve o parola comune")
            else:
                res.brand = Attribute(brand.slug, Certainty.PROBABLE, "title", 0.85)
                chosen = (brand, alias)
                res.evidence.append(f"Brand '{brand.name}' riconosciuto nel titolo")
        elif desc_hits:
            brands = {h[0].slug for h in desc_hits}
            brand, alias, span, _ = desc_hits[0]
            context = self._description_context(desc, span)
            if len(brands) == 1 and context == "cued":
                res.brand = Attribute(brand.slug, Certainty.PROBABLE, "description", FIRM_BRAND)
                chosen = (brand, alias)
                res.evidence.append(f"Brand '{brand.name}' dedotto dalla descrizione")
            else:
                why = (
                    "più marchi citati nella descrizione"
                    if len(brands) > 1
                    else "citato in un confronto o come accessorio"
                    if context == "comparison"
                    else "citato nella descrizione senza dire che è la marca"
                )
                res.flags["brand_mentions"] = sorted(brands)
                self._set_guess(res, brand, "description", 0.5, why)
        else:
            inferred = self._brand_from_lines(title, desc)
            if inferred:
                brand, strong = inferred
                if strong:
                    res.brand = Attribute(brand.slug, Certainty.PROBABLE, "model_keyword", FIRM_BRAND)
                    chosen = (brand, brand.name)
                    res.evidence.append(f"Brand '{brand.name}' dedotto dal nome del modello")
                else:
                    self._set_guess(
                        res, brand, "model_keyword", 0.5, "dedotto da un nome di modello non esclusivo"
                    )
            elif fz := self._fuzzy_brand(title):
                self._misspelled(res, *fz)
            elif vision and vision.brand and (vb := self._match_brand(fold(vision.brand.value))):
                conf = vision.brand.confidence
                if vision.brand.certainty == Certainty.CERTAIN or conf >= FIRM_BRAND:
                    res.brand = Attribute(vb[0].slug, vision.brand.certainty, "image", conf)
                    chosen = (vb[0], vb[1])
                    res.evidence.append(f"Brand '{vb[0].name}' riconosciuto dalle foto")
                else:
                    self._set_guess(res, vb[0], "image", conf, "letto dalle foto con poca sicurezza")

        if chosen:
            brand, alias = chosen
            res.brand_name = brand.name
            # A sub-line ("Polo Ralph Lauren", "Tommy Jeans") can appear in any text field.
            for hits, source in (
                (field_hits, "provider_field"),
                (title_hits, "title"),
                (desc_hits, "description"),
            ):
                hit = next((h for h in hits if h[0].slug == brand.slug and h[1] in SUB_LINES), None)
                if hit:
                    res.line = Attribute(SUB_LINES[hit[1]], Certainty.PROBABLE, source, 0.8)
                    break
            if vision and vision.brand and vision.brand.certainty == Certainty.CERTAIN:
                vb = self._match_brand(fold(vision.brand.value))
                if vb and vb[0].slug == brand.slug:
                    res.brand.confidence = max(res.brand.confidence, 0.95)
                    res.evidence.append("Brand confermato da logo/etichetta nelle foto")
        return title_masked, desc_masked

    def _misspelled(self, res: IdentificationResult, brand: BrandSpec, written: str, _alias: str) -> None:
        res.flags["misspelled_brand"] = {"written": written, "brand": brand.name}
        self._set_guess(res, brand, "fuzzy", 0.55, f"'{written}' somiglia a '{brand.name}'")
        res.evidence.append(
            f"Brand scritto male: '{written}' → {brand.name}. Chi cerca '{brand.name}' "
            "probabilmente non lo trova: possibile occasione (verifica che sia davvero quel brand)."
        )

    def _set_guess(
        self, res: IdentificationResult, brand: BrandSpec, source: str, conf: float, why: str
    ) -> None:
        res.brand_guess = Attribute(brand.slug, Certainty.PROBABLE, source, conf)
        res.brand_guess_name = brand.name
        res.evidence.append(
            f"Brand '{brand.name}' solo ipotizzato ({why}): non usato per cercare i comparabili"
        )

    @staticmethod
    def _description_context(desc: str, span: tuple[int, int]) -> str:
        """``cued`` when the description says the brand is the item's ("Marca Moncler", "Ralph Lauren originale"),
        ``comparison`` when it is named next to something else, else ``plain``."""
        # "Brand: Armani Jeans": the colon belongs to the cue, so only a full stop ends the look-back.
        before = _WORD.findall(_SENTENCE_END.split(desc[: span[0]])[-1])[-3:]
        after_text = desc[span[1] :]
        after = _WORD.findall(_SENTENCE_BREAK.split(after_text)[0])[:2]
        if any(w in _COMPARISON_BEFORE for w in before) or _AFTER_NEGATING.search(after_text[:80]):
            return "comparison"
        if any(w in _STRONG_CUES for w in before[-2:] + after) or (before and before[-1] in _BRAND_CUES):
            return "cued"
        return "plain"

    def _fuzzy_brand(self, text: str) -> tuple[BrandSpec, str, str] | None:
        """(brand, written form, catalog alias) for a near-miss spelling of a known brand."""
        words = _WORD.findall(text)
        best: tuple[float, BrandSpec, str, str] | None = None
        for n in (1, 2, 3):
            for i in range(len(words) - n + 1):
                cand = " ".join(words[i : i + n])
                if len(cand) < 5:
                    continue
                cand_words = cand.split()
                for alias, brand in self._fuzzy_by_prefix.get(cand[:2], ()):
                    if cand == alias or abs(len(alias) - len(cand)) > 3:
                        continue
                    alias_words = alias.split()
                    if len(alias_words) != len(cand_words):
                        continue
                    if len(alias_words) == 1:
                        score = fuzz.ratio(cand, alias)
                        ok = score >= 88
                    else:
                        # word by word: every word close, the whole close on average
                        parts = [
                            100 * JaroWinkler.normalized_similarity(a, b)
                            for a, b in zip(cand_words, alias_words, strict=True)
                        ]
                        score = sum(parts) / len(parts)
                        ok = min(parts) >= 82 and score >= 88 and cand_words[0][0] == alias_words[0][0]
                    if ok and (best is None or score > best[0]):
                        best = (score, brand, cand, alias)
        return (best[1], best[2], best[3]) if best else None

    def _brand_from_lines(self, title: str, desc: str) -> tuple[BrandSpec, bool] | None:
        """Infer the brand from a distinctive model name, only when it points to a single brand. The second value
        says whether it is strong enough to be the brand: a name no other brand uses, and a title that does not
        name another kind of item ("Sciarpa Himalayan" is a scarf, not a puffer jacket)."""
        for text in (title, desc):
            tokens = _tokens(text)
            candidates: dict[str, tuple[BrandSpec, str, str]] = {
                brand.slug: (brand, kw, cat)
                for gate, pattern, brand, kw, cat in self._distinctive
                if gate <= tokens and pattern.search(text)
            }
            if len(candidates) != 1:
                continue
            brand, kw, line_cat = next(iter(candidates.values()))
            if kw in WEAK_MODEL_KEYWORDS or text is desc:
                return brand, False
            scores = self._keyword_scores(self._mask_keyword(title, kw))
            named = max(scores, key=lambda s: scores[s]) if scores else None
            consistent = (
                named is None
                or named == line_cat
                or self.tax.parent_of(named) == self.tax.parent_of(line_cat)
            )
            return brand, consistent
        return None

    @staticmethod
    def _mask_keyword(text: str, kw: str) -> str:
        return re.sub(rf"(?<!\w){re.escape(kw)}(?!\w)", " " * len(kw), text)

    def _team_and_season(self, title: str, desc: str, res: IdentificationResult) -> None:
        """A team is read only where the text is about a football shirt: a football word or a season next to it, or
        a full club name. "Made in Italy", "Spedisco da Napoli", "Inter-city", "Ritiro a Roma" are not teams."""
        for text, source, conf in ((title, "title", 0.85), (desc, "description", 0.6)):
            tokens = _tokens(text)
            context = bool(FOOTBALL_CONTEXT.search(text))
            for gate, pattern, team, strong in self._team_patterns:
                if gate <= tokens and pattern.search(text) and (context or (strong and source == "title")):
                    res.team = Attribute(team, Certainty.PROBABLE, source, conf if context else 0.7)
                    res.flags["football_context"] = context
                    break
            if res.team.known:
                break
        if m := SEASON_PATTERN.search(title) or SEASON_PATTERN.search(desc):
            start = int(m.group(1))
            end = m.group(2)
            end_full = int(end) if len(end) == 4 else int(str(start)[:2] + end)
            if 0 <= end_full - start <= 1:
                res.season = Attribute(f"{start}/{str(end_full)[-2:]}", Certainty.PROBABLE, "title", 0.8)

    def _model(self, title: str, desc: str, res: IdentificationResult, vision: ImageAnalysis | None) -> str:
        """Set the model; returns the title with the model's own words taken out (so "Tech Fleece" does not also
        count as the category "fleece")."""
        brand_slug = res.brand.value
        if brand_slug:
            for text, source, conf in ((title, "title", 0.85), (desc, "description", 0.65)):
                tokens = _tokens(text)
                for gate, pattern, brand, line_name, _cat in self._line_index:
                    if brand.slug != brand_slug or not gate <= tokens:
                        continue
                    if m := pattern.search(text):
                        res.model = Attribute(line_name, Certainty.PROBABLE, source, conf)
                        res.evidence.append(
                            f"Modello '{line_name}' riconosciuto ({'titolo' if source == 'title' else 'descrizione'})"
                        )
                        return self._mask(title, m.span()) if source == "title" else title
        # A team is the model of a football shirt only (never of a hoodie that merely names the club).
        if res.team.known and res.flags.get("football_context"):
            res.model = Attribute(res.team.value, Certainty.PROBABLE, res.team.source, res.team.confidence)
            return title
        if vision and vision.model:
            res.model = Attribute(
                vision.model.value, vision.model.certainty, "image", vision.model.confidence
            )
        return title

    # ---------------------------------------------------------------- category
    def _crumb_entries(self, item: ListingText) -> list[str]:
        raw = " > ".join(x for x in (item.category_field, item.subcategory_field) if x)
        return [e for e in (fold(p) for p in re.split(r"\s*(?:>|›|\|)\s*", raw)) if e]

    def _slug_category(self, slug: str) -> str | None:
        """Category of a catalogue slug ("straight-fit-jeans"); a slug that joins several kinds
        ("coats-and-jackets") is a group and names none."""
        parts = slug.split("-and-")
        kinds = [next((k for rx, k in self._slug_rules if rx.search(p)), None) for p in parts]
        return kinds[0] if None not in kinds and len(set(kinds)) == 1 and len(parts) == 1 else None

    def _category_from_crumbs(self, item: ListingText, res: IdentificationResult) -> str | None:
        """The category the marketplace itself files the item under, from the catalogue links when the capture has
        them (language-free), else from the names on the path; the deepest entry that names one of ours wins."""
        brand_words = {fold(res.brand_name or ""), fold(item.brand_field or "")} - {""}
        for code in reversed(item.category_codes):
            parsed = catalog_code(code)
            if not parsed:
                continue
            ident, slug = parsed
            if ident in CATALOG_ID_CATEGORIES:
                if CATALOG_ID_CATEGORIES[ident]:
                    return CATALOG_ID_CATEGORIES[ident]
                continue
            if kind := self._slug_category(slug):
                return kind
        entries = self._crumb_entries(item)
        for entry in reversed(entries):
            if CRUMB_GROUPS.match(entry):
                continue
            if any(b and b in entry for b in brand_words):
                continue  # "Polo Ralph Lauren Polo": the brand's own entry, not a kind of item
            if entry in self._leaf_slugs:
                return entry
            for cat in self.tax.leaf_categories():
                if entry in (fold(cat.name), fold(cat.name_it)):
                    return cat.slug
            if re.search(r"\s(?:e|and|et|y|und|&)\s", entry):
                kinds = {s for rx, s in self._crumb_rules if rx.search(entry)}
                if len(kinds) != 1:
                    continue  # a group of several kinds ("Maglioni e felpe")
            for rx, slug in self._crumb_rules:
                if rx.search(entry):
                    return slug
        return None

    def _category(
        self,
        item: ListingText,
        title: str,
        desc: str,
        res: IdentificationResult,
        vision: ImageAnalysis | None,
    ) -> None:
        scores: dict[str, float] = {}
        sources: dict[str, str] = {}

        def add(slug: str, pts: float, source: str) -> None:
            scores[slug] = scores.get(slug, 0.0) + pts
            if slug not in sources:
                sources[slug] = source

        crumb = self._category_from_crumbs(item, res)
        for slug, pts in self._keyword_scores(title).items():
            add(slug, pts, "title")
        for slug, pts in self._keyword_scores(desc).items():
            add(slug, pts * 0.35, "description")
        if res.model.known and res.brand.value:
            brand = self.tax.brand_by_slug.get(res.brand.value)
            for line in brand.lines if brand else ():
                if line.name == res.model.value:
                    add(line.category, 2.5, "model")
        if res.team.known and res.flags.get("football_context") and res.team.source == "title":
            add("football-shirts", 3.5, "title")
        if vision and vision.category and vision.category.value in self.tax.category_by_slug:
            add(vision.category.value, 2 * vision.category.confidence, "image")
        if crumb:
            # Filed under another category than its title says: fewer buyers browse to it.
            title_scores = self._keyword_scores(title)
            if title_scores:
                seen = max(title_scores, key=lambda s: title_scores[s])
                parents = {c.slug: c.parent for c in self.tax.leaf_categories()}
                if (
                    seen != crumb
                    and parents.get(seen) != parents.get(crumb)
                    and title_scores[seen] >= 2
                    and title_scores.get(crumb, 0) == 0
                ):
                    res.flags["category_mismatch"] = {"declared": crumb, "detected": seen}
                    res.evidence.append(
                        f"Categoria dichiarata '{crumb}' ma il titolo indica '{seen}': chi cerca in quella "
                        "categoria non lo vede (possibile occasione)."
                    )
            res.category = Attribute(crumb, Certainty.CERTAIN, "provider_field", 0.97)
            return
        if not scores:
            return
        best = max(scores, key=lambda s: scores[s])
        total = sum(scores.values())
        share = scores[best] / total if total else 0
        conf = min(0.9, 0.45 + 0.1 * scores[best]) * (0.6 + 0.4 * share)
        # Only the marketplace's own category path makes a category certain, never words in a title.
        res.category = Attribute(best, Certainty.PROBABLE, sources[best], round(conf, 2))

    def _keyword_scores(self, text: str) -> dict[str, float]:
        """Longest-first keyword matching with span masking (so 't-shirt' doesn't count as 'shirt')."""
        scores: dict[str, float] = {}
        masked = text
        # Masking only removes matches, so the tokens of the unmasked text are a safe gate.
        tokens = _tokens(text)
        for gate, pattern, cat, kw in self._category_keywords:
            if not gate <= tokens:
                continue
            m = pattern.search(masked)
            if not m:
                continue
            weight = 1.0 if kw in GENERIC_CATEGORY_KEYWORDS else 3.0 if len(kw) > 4 else 2.0
            scores[cat.slug] = scores.get(cat.slug, 0.0) + weight
            masked = self._mask(masked, m.span())
        return scores

    # ------------------------------------------------------------------ gender
    def _gender_attr(self, item: ListingText, title: str, desc: str, res: IdentificationResult) -> None:
        entries = self._crumb_entries(item)
        if entries and (g := CRUMB_ROOT_GENDER.get(entries[0])):
            res.gender = Attribute(g, Certainty.CERTAIN, "provider_field", 0.92)
            return
        for text, source, conf in ((title, "title", 0.8), (desc, "description", 0.6)):
            text = VINTAGE_PATTERN.sub(" ", text)  # "anni 90" must not read as kids' age
            tokens = _tokens(text)
            found = {g for gate, pattern, g in self._gender if gate <= tokens and pattern.search(text)}
            if not found:
                continue
            for g in ("kids", "unisex", "women", "men"):
                if g in found:
                    if g in ("women", "men") and {"women", "men"} <= found:
                        g = "unisex"
                    res.gender = Attribute(g, Certainty.PROBABLE, source, conf)
                    return

    # ------------------------------------------------------- the simple fields
    def _simple_attributes(
        self,
        item: ListingText,
        title: str,
        desc: str,
        res: IdentificationResult,
        vision: ImageAnalysis | None,
    ) -> None:
        if c := normalize_color(item.color_field):
            res.color = Attribute(c, Certainty.CERTAIN, "provider_field", 0.95)
        elif c := normalize_color(title):
            res.color = Attribute(c, Certainty.PROBABLE, "title", 0.75)
        elif vision and vision.color and (c := normalize_color(vision.color.value)):
            res.color = Attribute(c, vision.color.certainty, "image", vision.color.confidence)

        if m := normalize_material(item.material_field):
            res.material = Attribute(m, Certainty.CERTAIN, "provider_field", 0.95)
        elif vision and vision.composition and (m := normalize_material(vision.composition.value)):
            res.material = Attribute(
                m, vision.composition.certainty, "image_label", vision.composition.confidence
            )
        elif m := normalize_material(title) or normalize_material(desc):
            res.material = Attribute(m, Certainty.PROBABLE, "text", 0.6)

        category = res.category.value
        if s := normalize_size(item.size_field, category, res.gender.value):
            # A bare number is a size only once we know what the item is: until then it is a guess at the scale.
            if size_is_ambiguous(item.size_field, category, res.gender.value):
                res.size = Attribute(s, Certainty.PROBABLE, "provider_field", 0.6)
            else:
                res.size = Attribute(s, Certainty.CERTAIN, "provider_field", 0.95)
        elif (s := self._title_size(title, category, res.gender.value)) is not None:
            res.size = Attribute(s, Certainty.PROBABLE, "title", 0.7)
        elif vision and vision.size_label and (s := normalize_size(vision.size_label.value, category)):
            res.size = Attribute(s, vision.size_label.certainty, "image_label", vision.size_label.confidence)

        if category in WINTER:
            res.seasonality = Attribute("autumn_winter", Certainty.PROBABLE, "category", 0.7)
        elif category in SUMMER:
            res.seasonality = Attribute("spring_summer", Certainty.PROBABLE, "category", 0.7)
        elif category:
            res.seasonality = Attribute("all_season", Certainty.PROBABLE, "category", 0.5)

        raw_text = f"{item.title}\n{item.description or ''}"
        for pattern in PRODUCT_CODE_PATTERNS:
            code = next(
                (
                    m3.group(1).upper()
                    for m3 in pattern.finditer(raw_text)
                    if plausible_product_code(m3.group(1))
                ),
                None,
            )
            if code:
                res.product_code = Attribute(code, Certainty.PROBABLE, "text", 0.7)
                break
        if not res.product_code.known and vision and vision.product_code:
            res.product_code = Attribute(
                vision.product_code.value.upper(),
                vision.product_code.certainty,
                "image_label",
                vision.product_code.confidence,
            )

        season_year = int(res.season.value[:4]) if res.season.value else None
        res.is_vintage = bool(VINTAGE_PATTERN.search(title) or VINTAGE_PATTERN.search(desc)) or (
            season_year is not None and season_year < 2010
        )

    @staticmethod
    def _title_size(title: str, category: str | None, gender: str | None) -> str | None:
        """A size the title states ("taglia 42 (S)", "tg. M", "W32 L32")."""
        m = _TITLE_SIZE.search(title)
        if m and (s := normalize_size(m.group(1), category, gender)):
            return s
        if m := _TITLE_WAIST.search(title):
            return f"W{m.group(1)}"
        return None

    # ----------------------------------------------------------------- signals
    def _signals(
        self,
        item: ListingText,
        title: str,
        desc: str,
        res: IdentificationResult,
        vision: ImageAnalysis | None,
    ) -> None:
        text = f"{title} \n {desc}"
        terms: set[str] = set()
        for rx, label in self._suspicious:
            for m in rx.finditer(text):
                if self._negated_term(text, m.start()):
                    continue
                if label == "copia" and self._near_document(text, m.start(), m.end()):
                    continue
                terms.add(label)
                break
        # "tipo/stile <brand>" ("like Ralph Lauren") is a strong counterfeit/dupe hint, whether or not the brand
        # was also named properly.
        # It is suspicious unless the item has a firm brand of its own that is another one ("simile a Nike, ma è
        # Champion originale" compares, it does not claim).
        claim = res.flags.get("claimed_brand")
        if claim and (not res.brand.value or related_brands(res.brand.value, claim["slug"])):
            terms.add(f"'tipo {claim['brand']}'")
        res.suspicious_terms = sorted(terms)
        report = extract_defects(f"{item.title}\n{item.description or ''}")
        res.defect_terms = report.labels
        res.defect_hits = [h.as_dict() for h in report.hits]
        if m := CLAIMED_PRICE.search(desc):
            try:
                value = float(m.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                value = 0
            if 10 <= value <= 20000:
                res.flags["claimed_original_price"] = value
        concerns = list(vision.authenticity_concerns) if vision else []
        if res.suspicious_terms or concerns:
            res.authenticity = Attribute(
                "suspicious", Certainty.PROBABLE, "text" if res.suspicious_terms else "image", 0.7
            )
            if res.suspicious_terms:
                res.evidence.append("Termini sospetti nella descrizione: " + ", ".join(res.suspicious_terms))
            for c in concerns:
                res.evidence.append(f"Possibile incongruenza nelle foto: {c}")
        elif vision and vision.authenticity_positive_signals:
            # Consistent details are reported, but authenticity is never asserted.
            res.authenticity = Attribute("no_red_flags", Certainty.UNVERIFIABLE, "image", 0.5)

    @staticmethod
    def _negated_term(text: str, start: int) -> bool:
        """ "non è un fake", "garantito non replica", "pas une copie": a term right after a negation is not read."""
        before = _WORD.findall(_SENTENCE_BREAK.split(text[:start])[-1])[-3:]
        return any(w in NEGATION_WORDS for w in before)

    @staticmethod
    def _near_document(text: str, start: int, end: int) -> bool:
        """ "copia dello scontrino", "allego copia della fattura": a copy of a document, not of the item."""
        window = (
            _WORD.findall(text[max(0, start - 30) : start])[-3:] + _WORD.findall(text[end : end + 40])[:4]
        )
        return any(w in DOCUMENT_WORDS for w in window)

    # ------------------------------------------------------------ contradictions
    def _contradictions(self, item: ListingText, title: str, desc: str, res: IdentificationResult) -> None:
        """Where a field and the free text disagree. They are listed (the dossier shows them) and lower the
        confidence: the field is kept, the text is not trusted over it."""

        def add(field_name: str, declared: str, found: str, where: str, severity: str = "medium") -> None:
            res.contradictions.append(
                {
                    "field": field_name,
                    "declared": declared,
                    "found": found,
                    "where": where,
                    "severity": severity,
                }
            )

        if clash := res.flags.get("brand_conflict"):
            add("brand", clash["field"], clash["title"], "titolo", "high")
        elif res.brand.value:
            other = next(
                (h for h in self._match_brands(desc, allow_weak=False) if h[0].slug != res.brand.value), None
            )
            if other and self._description_context(desc, other[2]) == "cued":
                add("brand", res.brand_name or "", other[0].name, "descrizione", "medium")
        ts = self._title_size(title, res.category.value, res.gender.value)
        if (
            res.size.source == "provider_field"
            and ts
            and ts != res.size.value
            and size_distance(ts, res.size.value) != 0
        ):
            add("size", res.size.value or "", ts, "titolo")
        if res.color.source == "provider_field":
            tc = normalize_color(title)
            if (
                tc
                and tc != res.color.value
                and not any(tc in f and res.color.value in f for f in _COLOR_FAMILIES)
            ):
                add("color", res.color.value or "", tc, "titolo", "low")
        if res.gender.source == "provider_field":
            rest = self._title_gender(title)
            if rest and rest not in (res.gender.value, "unisex"):
                add("gender", res.gender.value or "", rest, "titolo")
        if cm := res.flags.get("category_mismatch"):
            add("category", cm["declared"], cm["detected"], "titolo", "low")

    def _title_gender(self, title: str) -> str | None:
        text = VINTAGE_PATTERN.sub(" ", title)
        tokens = _tokens(text)
        found = {g for gate, pattern, g in self._gender if gate <= tokens and pattern.search(text)}
        if len(found) == 1:
            return next(iter(found))
        return None

    @staticmethod
    def _confidence(res: IdentificationResult) -> int:
        brand_has_lines = False
        if res.brand.value and (spec := DEFAULT_TAXONOMY.brand_by_slug.get(res.brand.value)):
            brand_has_lines = bool(spec.lines)
        model_component = res.model.confidence if res.model.known else (0.0 if brand_has_lines else 0.45)
        if res.category.value == "football-shirts" and res.team.known:
            model_component = max(model_component, res.team.confidence * (1.0 if res.season.known else 0.8))
        score = (
            0.35 * res.brand.confidence
            + 0.25 * res.category.confidence
            + 0.15 * model_component
            + 0.10 * res.size.confidence
            + 0.05 * (0.9 if res.gender.known else 0.3)
            + 0.05 * res.color.confidence
            + 0.05 * (0.8 if res.material.known else 0.4)
        )
        # A disagreement between the fields and the text means one of them is wrong: less sure of the whole.
        score -= min(0.18, 0.06 * len(res.contradictions))
        return max(0, min(100, round(score * 100)))
