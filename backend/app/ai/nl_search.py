"""Natural-language search -> structured opportunity filters.

Deterministic multilingual parser (Italian/English) for queries such as:

* "Ralph Lauren hoodie M under 20"
* "fammi vedere felpe Ralph Lauren sotto 25 euro con almeno 50% ROI"
* "sneakers nike 42 tra 30 e 60 euro profitto almeno 15 basso rischio"

Every recognized fragment is removed from the text; leftover meaningful words become a free-text
title query. The parser reports what it understood so the UI can show the applied filters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.identification.taxonomy import DEFAULT_TAXONOMY, Taxonomy, fold
from app.ingestion.normalizer import normalize_size

STOPWORDS = {
    "fammi",
    "vedere",
    "mostrami",
    "cerca",
    "cercami",
    "trova",
    "trovami",
    "voglio",
    "vorrei",
    "show",
    "me",
    "find",
    "search",
    "with",
    "con",
    "almeno",
    "at",
    "least",
    "di",
    "da",
    "del",
    "della",
    "delle",
    "dei",
    "degli",
    "le",
    "la",
    "il",
    "lo",
    "gli",
    "i",
    "un",
    "una",
    "uno",
    "e",
    "and",
    "per",
    "for",
    "in",
    "a",
    "the",
    "che",
    "euro",
    "eur",
    "taglia",
    "size",
    "tg",
    "articoli",
    "items",
    "deals",
    "affari",
    "offerte",
    "solo",
    "only",
    "sotto",
    "under",
    "below",
    "over",
    "sopra",
    "tra",
    "between",
    "max",
    "min",
    "massimo",
    "minimo",
    "prezzo",
    "price",
}
CONDITION_WORDS = (
    (
        r"\bnuov[oiae] con (?:cartellino|etichetta)\b|\bnew with tags\b|\bnwt\b|\bcon cartellino\b",
        "new_with_tags",
    ),
    (r"\bnuov[oiae]\b|\bnew\b|\bmai (?:usat|indossat)[oaie]\b", "new_without_tags"),
    (r"\bottim[eoai] condizion[ie]\b|\bvery good\b|\bcome nuov[oa]\b", "very_good"),
)
COUNTRY_WORDS = {
    "italia": "IT",
    "italy": "IT",
    "francia": "FR",
    "france": "FR",
    "spagna": "ES",
    "spain": "ES",
    "germania": "DE",
    "germany": "DE",
}
# Generic words that cover several categories ("felpa" = crewneck sweatshirts and hoodies).
GENERIC_CATEGORY_WORDS = {"felpa": ("sweatshirts", "hoodies")}
NUM = r"(\d+(?:[.,]\d+)?)"
EUR = r"(?:\s*(?:€|euro|eur))?"


@dataclass
class ParsedQuery:
    filters: dict[str, Any] = field(default_factory=dict)
    understood: list[str] = field(default_factory=list)
    remaining_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"filters": self.filters, "understood": self.understood, "remaining_text": self.remaining_text}


def _num(s: str) -> float:
    return float(s.replace(",", "."))


class NaturalLanguageParser:
    def __init__(self, taxonomy: Taxonomy = DEFAULT_TAXONOMY) -> None:
        self.tax = taxonomy
        self._vocab = {w for c in taxonomy.categories for kw in c.keywords for w in fold(kw).split()}

    def parse(self, query: str) -> ParsedQuery:
        text = " " + fold(query) + " "
        out = ParsedQuery()
        f = out.filters

        def take(pattern: str, handler: Any) -> None:
            nonlocal text
            m = re.search(pattern, text)
            if m:
                handler(m)
                text = text[: m.start()] + " " + text[m.end() :]

        # ---- economics (most specific first) ------------------------------------------------
        take(
            rf"\broi\s*(?:>=|>|di almeno|almeno|min(?:imo)?|of at least|at least|over|sopra)?\s*{NUM}\s*%?",
            lambda m: self._set(out, "min_roi", _num(m.group(1)) / 100, f"ROI ≥ {m.group(1)}%"),
        )
        take(
            rf"\b(?:almeno|minimo|min|at least|>=|>)\s*{NUM}\s*%\s*(?:di\s+)?roi\b",
            lambda m: self._set(out, "min_roi", _num(m.group(1)) / 100, f"ROI ≥ {m.group(1)}%"),
        )
        take(
            rf"\b(?:profitto|profit|guadagno|margine)\s*(?:netto\s*)?(?:>=|>|di almeno|almeno|min(?:imo)?|at least|over|sopra)?\s*{NUM}{EUR}",
            lambda m: self._set(out, "min_profit", _num(m.group(1)), f"Profitto ≥ €{m.group(1)}"),
        )
        take(
            rf"\b(?:flip(?:\s*score)?|punteggio)\s*(?:>=|>|almeno|min(?:imo)?|over|sopra)?\s*{NUM}",
            lambda m: self._set(out, "min_flip", int(_num(m.group(1))), f"Flip Score ≥ {m.group(1)}"),
        )
        take(
            rf"\b(?:tra|between|da)\s*{NUM}{EUR}\s*(?:e|and|a|-|to)\s*{NUM}{EUR}",
            lambda m: (
                self._set(out, "min_price", _num(m.group(1)), f"Prezzo ≥ €{m.group(1)}"),
                self._set(out, "max_price", _num(m.group(2)), f"Prezzo ≤ €{m.group(2)}"),
            ),
        )
        take(
            rf"\b(?:sotto(?:\s+i)?|under|below|meno di|max(?:imo)?|fino a|entro|<=|<)\s*{NUM}{EUR}",
            lambda m: self._set(out, "max_price", _num(m.group(1)), f"Prezzo ≤ €{m.group(1)}"),
        )
        take(
            rf"\b(?:sopra(?:\s+i)?|over|oltre|piu di|almeno)\s*{NUM}{EUR}",
            lambda m: self._set(out, "min_price", _num(m.group(1)), f"Prezzo ≥ €{m.group(1)}"),
        )

        # ---- qualitative ----------------------------------------------------------------------
        take(
            r"\b(?:basso rischio|low risk|poco rischio|sicur[ie])\b",
            lambda m: self._set(out, "max_risk", 25, "Rischio basso"),
        )
        take(
            r"\b(?:vendita veloce|veloci da vendere|fast flip|fast|rapid[ie]|veloc[ie])\b",
            lambda m: self._set(out, "min_velocity", 70, "Vendita veloce"),
        )
        take(r"\b(?:ultra deal|ultra)\b", lambda m: self._set(out, "ultra_only", True, "Solo Ultra Deal"))
        take(
            r"\b(?:appena pubblicat[ioe]|just listed|nuovi annunci|ultim[ie] or[ae])\b",
            lambda m: self._set(out, "published_within_hours", 6, "Pubblicati nelle ultime 6 ore"),
        )
        take(
            r"\bvintage\b|\bretro\b|\banni\s?[6-9]0\b|\b[6-9]0s\b",
            lambda m: self._set(out, "vintage_only", True, "Vintage"),
        )
        for pattern, cond in CONDITION_WORDS:
            take(pattern, lambda m, c=cond: self._append(out, "conditions", c, f"Condizione: {c}"))
        for word, code in COUNTRY_WORDS.items():
            take(rf"\b{word}\b", lambda m, c=code: self._append(out, "countries", c, f"Paese: {c}"))

        # ---- brand & category -----------------------------------------------------------------
        for pattern, brand, _alias in self.tax.brand_alias_patterns:
            if len(_alias) <= 2:
                continue
            m = pattern.search(text)
            if m:
                self._append(out, "brands", brand.slug, f"Brand: {brand.name}")
                text = text[: m.start()] + " " + text[m.end() :]
        # Plurals ("felpe", "giacche", "maglie calcio") are reduced to the singular forms used by
        # the category vocabulary before matching.
        text = " ".join(
            w if w in self._vocab or _singular(w) not in self._vocab else _singular(w) for w in text.split()
        )
        text = f" {text} "
        for word, slugs in GENERIC_CATEGORY_WORDS.items():
            if re.search(rf"\b{word}\b(?!\s+con\s+cappuccio)", text):
                for slug in slugs:
                    self._append(
                        out, "categories", slug, f"Categoria: {self.tax.category_by_slug[slug].name_it}"
                    )
                text = re.sub(rf"\b{word}\b", " ", text, count=1)
        for pattern, cat, _kw in self.tax.category_keyword_patterns:
            m = pattern.search(text)
            if m and len(_kw) > 2:
                self._append(out, "categories", cat.slug, f"Categoria: {cat.name_it}")
                text = text[: m.start()] + " " + text[m.end() :]

        # ---- size -----------------------------------------------------------------------------
        cats = f.get("categories") or []
        m = re.search(
            r"\b(?:taglia|tg|size)?\s*\b(xxs|xs|s|m|l|xl|xxl|xxxl|w\d{2}|eu\s?\d{2}(?:[.,]5)?)\b", text
        )
        if m:
            size = normalize_size(m.group(1).upper(), cats[0] if cats else None)
            if size:
                self._append(out, "sizes", size, f"Taglia: {size}")
                text = text[: m.start()] + " " + text[m.end() :]
        elif cats and any(c in ("sneakers",) for c in cats):
            m = re.search(r"\b(3[5-9]|4[0-9])(?:[.,]5)?\b", text)
            if m:
                self._append(out, "sizes", f"EU{m.group(1)}", f"Taglia: EU{m.group(1)}")
                text = text[: m.start()] + " " + text[m.end() :]

        leftover = [
            w
            for w in re.findall(r"[a-z0-9']+", text)
            if w not in STOPWORDS and len(w) > 2 and not w.isdigit()
        ]
        out.remaining_text = " ".join(leftover)
        if out.remaining_text:
            f["q"] = out.remaining_text
            out.understood.append(f"Testo: “{out.remaining_text}”")
        if "sort" not in f:
            f["sort"] = "roi" if "min_roi" in f else "profit" if "min_profit" in f else "flip"
        return out

    @staticmethod
    def _set(out: ParsedQuery, key: str, value: Any, label: str) -> None:
        out.filters[key] = value
        out.understood.append(label)

    @staticmethod
    def _append(out: ParsedQuery, key: str, value: Any, label: str) -> None:
        values = out.filters.setdefault(key, [])
        if value not in values:
            values.append(value)
            out.understood.append(label)


def _singular(word: str) -> str:
    rules = (
        ("che", "ca"),
        ("ghe", "ga"),
        ("ioni", "ione"),
        ("oni", "one"),
        ("ie", "ia"),
        ("e", "a"),
        ("i", "o"),
    )
    for suffix, repl in rules:
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)] + repl
    if word.endswith("s") and len(word) > 4:
        return word[:-1]
    return word
