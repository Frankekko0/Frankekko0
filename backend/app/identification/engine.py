"""Product Identification Engine.

Turns a noisy listing (incomplete title, free-text description, optional structured fields,
optional image analysis) into a structured product identity with per-attribute certainty:

* ``certain``       - given by the marketplace in a structured field (or read on a label);
* ``probable``      - inferred from title/description/photos with reasonable evidence;
* ``unverifiable``  - unknown or not confirmable.

It never claims authenticity: the best it states is "no red flags found"; suspicious wording
or visual concerns are surfaced as risk signals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import Certainty
from app.identification.taxonomy import (
    DEFAULT_TAXONOMY,
    DEFECT_PATTERNS,
    DISTINCTIVE_MODEL_KEYWORDS,
    FOOTBALL_TEAMS,
    GENDER_KEYWORDS,
    PRODUCT_CODE_PATTERNS,
    SEASON_PATTERN,
    SUSPICIOUS_PATTERNS,
    VINTAGE_PATTERN,
    BrandSpec,
    Taxonomy,
    fold,
)
from app.ingestion.normalizer import normalize_color, normalize_material, normalize_size
from app.vision.types import ImageAnalysis

SHORT_ALIAS_LEN = 3
SUB_LINES = {
    "polo ralph lauren": "Polo Ralph Lauren",
    "polo by ralph lauren": "Polo Ralph Lauren",
    "tommy jeans": "Tommy Jeans",
    "adidas originals": "Adidas Originals",
    "nike sportswear": "Nike Sportswear",
    "carhartt wip": "Carhartt WIP",
}
WINTER = {"puffer-jackets", "coats", "knitwear", "fleece"}
SUMMER = {"polo-shirts", "t-shirts"}


@dataclass
class Attribute:
    value: str | None = None
    certainty: Certainty = Certainty.UNVERIFIABLE
    source: str | None = None
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "certainty": self.certainty.value,
            "source": self.source,
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


@dataclass
class IdentificationResult:
    brand: Attribute = field(default_factory=Attribute)
    brand_name: str | None = None
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
    evidence: list[str] = field(default_factory=list)
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
            "evidence": self.evidence,
            "confidence": self.confidence,
        }


class IdentificationEngine:
    def __init__(self, taxonomy: Taxonomy = DEFAULT_TAXONOMY) -> None:
        self.tax = taxonomy
        self._line_index: list[tuple[re.Pattern[str], BrandSpec, str, str]] = []
        for brand in taxonomy.all_brands:
            for line in brand.lines:
                for kw in line.keywords:
                    self._line_index.append(
                        (re.compile(rf"(?<!\w){re.escape(fold(kw))}(?!\w)"), brand, line.name, line.category)
                    )
        self._line_index.sort(key=lambda e: len(e[0].pattern), reverse=True)
        self._distinctive = [
            (re.compile(rf"(?<!\w){re.escape(fold(kw))}(?!\w)"), brand)
            for brand in taxonomy.all_brands
            for line in brand.lines
            for kw in line.keywords
            if fold(kw) in DISTINCTIVE_MODEL_KEYWORDS
        ]
        self._team_patterns = [
            (re.compile(rf"(?<!\w){re.escape(fold(a))}(?!\w)"), team)
            for team, aliases in FOOTBALL_TEAMS
            for a in sorted(aliases, key=len, reverse=True)
        ]
        self._suspicious = [(re.compile(p), label) for p, label in SUSPICIOUS_PATTERNS]
        self._defects = [(re.compile(p), label) for p, label in DEFECT_PATTERNS]
        self._gender = [
            (re.compile(rf"(?<!\w){re.escape(w)}(?!\w)"), g)
            for g, words in GENDER_KEYWORDS.items()
            for w in words
        ]

    # ------------------------------------------------------------------ public
    def identify(self, item: ListingText, vision: ImageAnalysis | None = None) -> IdentificationResult:
        res = IdentificationResult()
        title = fold(item.title)
        desc = fold(item.description or "")
        title_masked, desc_masked = self._brand(item, title, desc, res, vision)
        self._team_and_season(title_masked, desc_masked, res)
        self._model(title_masked, desc_masked, res, vision)
        self._category(item, title_masked, desc_masked, res, vision)
        self._gender_attr(title, desc, res)
        self._simple_attributes(item, title, desc, res, vision)
        self._signals(item, title, desc, res, vision)
        res.confidence = self._confidence(res)
        return res

    # ----------------------------------------------------------------- helpers
    def _match_brand(self, text: str) -> tuple[BrandSpec, str, tuple[int, int]] | None:
        for pattern, brand, alias in self.tax.brand_alias_patterns:
            if m := pattern.search(text):
                return brand, alias, m.span()
        return None

    @staticmethod
    def _mask(text: str, span: tuple[int, int] | None) -> str:
        if not span:
            return text
        return text[: span[0]] + " " * (span[1] - span[0]) + text[span[1] :]

    def _brand(
        self,
        item: ListingText,
        title: str,
        desc: str,
        res: IdentificationResult,
        vision: ImageAnalysis | None,
    ) -> tuple[str, str]:
        field_match = self._match_brand(fold(item.brand_field)) if item.brand_field else None
        title_match = self._match_brand(title)
        desc_match = self._match_brand(desc)
        title_masked = self._mask(title, title_match[2] if title_match else None)
        desc_masked = self._mask(desc, desc_match[2] if desc_match else None)

        chosen: tuple[BrandSpec, str] | None = None
        if field_match:
            brand, alias, _ = field_match
            res.brand = Attribute(brand.slug, Certainty.CERTAIN, "provider_field", 0.98)
            chosen = (brand, alias)
            if title_match and title_match[0].slug != brand.slug:
                res.brand.confidence = 0.8
                res.evidence.append(
                    f"Il titolo cita '{title_match[0].name}' ma il brand indicato è '{brand.name}'"
                )
        elif title_match:
            brand, alias, _ = title_match
            conf = 0.6 if len(alias) <= SHORT_ALIAS_LEN else 0.85
            res.brand = Attribute(brand.slug, Certainty.PROBABLE, "title", conf)
            chosen = (brand, alias)
            res.evidence.append(f"Brand '{brand.name}' riconosciuto nel titolo")
        elif desc_match:
            brand, alias, _ = desc_match
            conf = 0.5 if len(alias) <= SHORT_ALIAS_LEN else 0.7
            res.brand = Attribute(brand.slug, Certainty.PROBABLE, "description", conf)
            chosen = (brand, alias)
            res.evidence.append(f"Brand '{brand.name}' dedotto dalla descrizione")
        else:
            inferred = self._brand_from_lines(title) or self._brand_from_lines(desc)
            if inferred:
                res.brand = Attribute(inferred.slug, Certainty.PROBABLE, "model_keyword", 0.55)
                chosen = (inferred, inferred.name)
                res.evidence.append(f"Brand '{inferred.name}' dedotto dal nome del modello")
            elif vision and vision.brand and (vb := self._match_brand(fold(vision.brand.value))):
                res.brand = Attribute(vb[0].slug, vision.brand.certainty, "image", vision.brand.confidence)
                chosen = (vb[0], vb[1])
                res.evidence.append(f"Brand '{vb[0].name}' riconosciuto dalle foto")
            elif item.brand_field:
                res.evidence.append(f"Brand '{item.brand_field}' non presente nel catalogo interno")

        if chosen:
            brand, alias = chosen
            res.brand_name = brand.name
            if alias in SUB_LINES:
                res.line = Attribute(
                    SUB_LINES[alias], res.brand.certainty, res.brand.source, res.brand.confidence
                )
            if vision and vision.brand and vision.brand.certainty == Certainty.CERTAIN:
                vb = self._match_brand(fold(vision.brand.value))
                if vb and vb[0].slug == brand.slug:
                    res.brand.confidence = max(res.brand.confidence, 0.95)
                    res.evidence.append("Brand confermato da logo/etichetta nelle foto")
        return title_masked, desc_masked

    def _brand_from_lines(self, text: str) -> BrandSpec | None:
        """Infer the brand from a distinctive model name, only when it points to a single brand."""
        candidates = {brand.slug: brand for pattern, brand in self._distinctive if pattern.search(text)}
        return next(iter(candidates.values())) if len(candidates) == 1 else None

    def _team_and_season(self, title: str, desc: str, res: IdentificationResult) -> None:
        for text, conf in ((title, 0.85), (desc, 0.6)):
            for pattern, team in self._team_patterns:
                if pattern.search(text):
                    res.team = Attribute(
                        team, Certainty.PROBABLE, "title" if text is title else "description", conf
                    )
                    break
            if res.team.known:
                break
        if m := SEASON_PATTERN.search(title) or SEASON_PATTERN.search(desc):
            start = int(m.group(1))
            end = m.group(2)
            end_full = int(end) if len(end) == 4 else int(str(start)[:2] + end)
            if 0 <= end_full - start <= 1:
                res.season = Attribute(f"{start}/{str(end_full)[-2:]}", Certainty.PROBABLE, "title", 0.8)

    def _model(self, title: str, desc: str, res: IdentificationResult, vision: ImageAnalysis | None) -> None:
        brand_slug = res.brand.value
        if brand_slug:
            for text, source, conf in ((title, "title", 0.85), (desc, "description", 0.65)):
                for pattern, brand, line_name, _cat in self._line_index:
                    if brand.slug != brand_slug:
                        continue
                    if pattern.search(text):
                        res.model = Attribute(line_name, Certainty.PROBABLE, source, conf)
                        res.evidence.append(
                            f"Modello '{line_name}' riconosciuto ({'titolo' if source == 'title' else 'descrizione'})"
                        )
                        return
        if res.team.known:
            res.model = Attribute(res.team.value, Certainty.PROBABLE, res.team.source, res.team.confidence)
            return
        if vision and vision.model:
            res.model = Attribute(
                vision.model.value, vision.model.certainty, "image", vision.model.confidence
            )

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
            if source == "provider_field" or slug not in sources:
                sources[slug] = source

        provider_text = fold(" ".join(x for x in (item.category_field, item.subcategory_field) if x))
        if provider_text:
            for cat in self.tax.leaf_categories():
                if provider_text in (fold(cat.name), fold(cat.name_it), cat.slug):
                    add(cat.slug, 8, "provider_field")
            for slug, pts in self._keyword_scores(provider_text).items():
                add(slug, pts * 1.6, "provider_field")
        for slug, pts in self._keyword_scores(title).items():
            add(slug, pts, "title")
        for slug, pts in self._keyword_scores(desc).items():
            add(slug, pts * 0.35, "description")
        if res.model.known and res.brand.value:
            brand = self.tax.brand_by_slug.get(res.brand.value)
            for line in brand.lines if brand else ():
                if line.name == res.model.value:
                    add(line.category, 2.5, "model")
        if res.team.known:
            add("football-shirts", 3.5, "title")
        if vision and vision.category and vision.category.value in self.tax.category_by_slug:
            add(vision.category.value, 2 * vision.category.confidence, "image")
        if not scores:
            return
        best = max(scores, key=lambda s: scores[s])
        total = sum(scores.values())
        share = scores[best] / total if total else 0
        source = sources[best]
        if source == "provider_field" and scores[best] >= 8:
            res.category = Attribute(best, Certainty.CERTAIN, source, 0.97)
        else:
            conf = min(0.9, 0.45 + 0.1 * scores[best]) * (0.6 + 0.4 * share)
            res.category = Attribute(best, Certainty.PROBABLE, source, round(conf, 2))

    def _keyword_scores(self, text: str) -> dict[str, float]:
        """Longest-first keyword matching with span masking (so 't-shirt' doesn't count as 'shirt')."""
        scores: dict[str, float] = {}
        masked = text
        for pattern, cat, kw in self.tax.category_keyword_patterns:
            m = pattern.search(masked)
            if not m:
                continue
            weight = 3.0 if len(kw) > 4 else 2.0
            scores[cat.slug] = scores.get(cat.slug, 0.0) + weight
            masked = self._mask(masked, m.span())
        return scores

    def _gender_attr(self, title: str, desc: str, res: IdentificationResult) -> None:
        for text, source, conf in ((title, "title", 0.8), (desc, "description", 0.6)):
            text = VINTAGE_PATTERN.sub(" ", text)  # "anni 90" must not read as kids' age
            found = {g for pattern, g in self._gender if pattern.search(text)}
            if not found:
                continue
            for g in ("kids", "unisex", "women", "men"):
                if g in found:
                    if g in ("women", "men") and {"women", "men"} <= found:
                        g = "unisex"
                    res.gender = Attribute(g, Certainty.PROBABLE, source, conf)
                    return

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
            res.size = Attribute(s, Certainty.CERTAIN, "provider_field", 0.95)
        else:
            m2 = re.search(r"\b(?:tg|taglia|size|talla|taille)\.?\s*([a-z0-9.,/]{1,6})\b", title)
            if m2 and (s := normalize_size(m2.group(1), category, res.gender.value)):
                res.size = Attribute(s, Certainty.PROBABLE, "title", 0.7)
            elif vision and vision.size_label and (s := normalize_size(vision.size_label.value, category)):
                res.size = Attribute(
                    s, vision.size_label.certainty, "image_label", vision.size_label.confidence
                )

        if category in WINTER:
            res.seasonality = Attribute("autumn_winter", Certainty.PROBABLE, "category", 0.7)
        elif category in SUMMER:
            res.seasonality = Attribute("spring_summer", Certainty.PROBABLE, "category", 0.7)
        elif category:
            res.seasonality = Attribute("all_season", Certainty.PROBABLE, "category", 0.5)

        raw_text = f"{item.title}\n{item.description or ''}"
        for pattern in PRODUCT_CODE_PATTERNS:
            if m3 := pattern.search(raw_text):
                res.product_code = Attribute(m3.group(1).upper(), Certainty.PROBABLE, "text", 0.7)
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

    def _signals(
        self,
        item: ListingText,
        title: str,
        desc: str,
        res: IdentificationResult,
        vision: ImageAnalysis | None,
    ) -> None:
        text = f"{title} \n {desc}"
        res.suspicious_terms = sorted({label for p, label in self._suspicious if p.search(text)})
        # "tipo/stile <brand>" ("like Ralph Lauren") is a strong counterfeit/dupe hint.
        if res.brand_name and re.search(
            rf"\b(?:tipo|stile|style|like)\s+{re.escape(fold(res.brand_name))}\b", text
        ):
            res.suspicious_terms.append(f"'tipo {res.brand_name}'")
        res.defect_terms = sorted({label for p, label in self._defects if p.search(text)})
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
        return max(0, min(100, round(score * 100)))
