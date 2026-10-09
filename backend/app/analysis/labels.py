"""What the labels say: a state per kind of label, and the facts read from them.

States (``present_readable`` · ``present_unreadable`` · ``not_visible`` · ``absence_verifiable`` ·
``insufficient_information``) never turn "not shown" into "not there": that needs a photo of the
place where the label would be. Without a photo analysis every label is
``insufficient_information``, not a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.analysis.categories import CategoryPlugin
from app.analysis.coverage import is_analysed
from app.identification.taxonomy import fold

STATE_ORDER = {
    "present_readable": 0,
    "present_unreadable": 1,
    "absence_verifiable": 2,
    "not_visible": 3,
    "insufficient_information": 4,
}
STATE_LABEL = {
    "present_readable": "presente e leggibile",
    "present_unreadable": "presente ma non leggibile",
    "not_visible": "non visibile",
    "absence_verifiable": "assenza verificabile",
    "insufficient_information": "informazione insufficiente",
}
TYPE_LABEL = {
    "brand_label": "etichetta del marchio",
    "care_label": "etichetta di lavaggio",
    "composition": "composizione",
    "size_label": "etichetta della taglia",
    "sku_code": "codice prodotto",
    "barcode": "codice a barre",
    "paper_tag": "cartellino",
    "proof_of_purchase": "prova d'acquisto",
}

# Materials in the languages sellers write, as their canonical English name.
MATERIALS: dict[str, tuple[str, ...]] = {
    "cotton": ("cotone", "coton", "cotton", "baumwolle", "algodon"),
    "polyester": ("poliestere", "polyester", "poliester"),
    "wool": ("lana", "laine", "wool", "wolle"),
    "cashmere": ("cashmere", "cachemire", "kaschmir", "casmir"),
    "silk": ("seta", "soie", "silk", "seide"),
    "linen": ("lino", "lin", "linen", "leinen"),
    "viscose": ("viscosa", "viscose", "viskose", "rayon"),
    "elastane": ("elastan", "elastane", "elastano", "spandex", "lycra", "elasthan"),
    "polyamide": ("poliammide", "polyamide", "polyamid", "poliamida", "nylon"),
    "acrylic": ("acrilico", "acrylique", "acrylic", "acryl", "acrilico"),
    "leather": ("pelle", "cuir", "leather", "leder", "cuero"),
    "down": ("piuma", "piumino", "duvet", "down", "daunen", "plumon", "plumas"),
    "denim": ("denim", "jeans"),
}
_MATERIAL_BY_WORD = {w: canon for canon, words in MATERIALS.items() for w in words}
_PCT_BEFORE = re.compile(r"(\d{1,3})\s*%\s*([a-z]+)")
_PCT_AFTER = re.compile(r"([a-z]+)\s*[:=]?\s*(\d{1,3})\s*%")
RN_CA = re.compile(r"\b(RN|CA)\s?(\d{4,7})\b", re.IGNORECASE)
BARCODE = re.compile(r"\b(\d{12,13})\b")


def parse_composition(text: str | None) -> dict[str, int]:
    """``{"cotton": 67, "polyester": 33}`` from "67% cotone 33% poliestere" (any of the languages).

    Labels write the percentage before the fibre ("67% cotton") or after it ("cotton 67%"); when a
    text could be read both ways, the reading whose shares add up closest to 100 is the right one."""
    folded = fold(text or "").replace("ß", "ss")
    readings: list[dict[str, int]] = []
    for rx, order in ((_PCT_BEFORE, (0, 1)), (_PCT_AFTER, (1, 0))):
        out: dict[str, int] = {}
        for found in rx.findall(folded):
            pct, word = found[order[0]], found[order[1]]
            canon = _MATERIAL_BY_WORD.get(word)
            if canon and 0 < int(pct) <= 100:
                out[canon] = out.get(canon, 0) + int(pct)
        if out:
            readings.append(out)
    if not readings:
        return {}
    return min(readings, key=lambda r: abs(sum(r.values()) - 100))


def materials_in(text: str | None) -> list[str]:
    """Canonical materials named in free text, in order of appearance."""
    found: list[str] = []
    for word in re.findall(r"[a-z]+", fold(text or "")):
        canon = _MATERIAL_BY_WORD.get(word)
        if canon and canon not in found:
            found.append(canon)
    return found


@dataclass
class LabelState:
    type: str
    state: str
    photo: int | None = None
    text: str | None = None
    certainty: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": TYPE_LABEL[self.type],
            "state": self.state,
            "state_label": STATE_LABEL[self.state],
            "photo": self.photo,
            "text": self.text,
            "certainty": self.certainty,
        }


@dataclass
class LabelReport:
    states: list[LabelState]
    size_text: str | None = None
    composition: dict[str, int] = field(default_factory=dict)
    composition_text: str | None = None
    codes: dict[str, str] = field(default_factory=dict)  # rn / ca / barcode / sku
    brand_text: str | None = None
    unread: list[str] = field(default_factory=list)  # label kinds worth asking a photo of

    def state_of(self, label_type: str) -> str | None:
        return next((s.state for s in self.states if s.type == label_type), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "states": [s.as_dict() for s in self.states],
            "size_text": self.size_text,
            "composition": self.composition,
            "composition_text": self.composition_text,
            "codes": self.codes,
            "brand_text": self.brand_text,
            "unread": self.unread,
        }


def _finding_text(vision: dict[str, Any], key: str) -> tuple[str | None, str | None]:
    f = vision.get(key)
    if isinstance(f, dict) and f.get("value") and f.get("certainty") in ("certain", "probable"):
        return str(f["value"]), f.get("certainty")
    return None, None


def build_label_report(vision: dict[str, Any] | None, plugin: CategoryPlugin) -> LabelReport:
    v = vision or {}
    analysed = is_analysed(vision)
    ocr = v.get("ocr_facts") or {}
    if not analysed and not ocr.get("found"):
        return LabelReport(
            [LabelState(t, "insufficient_information") for t in plugin.label_types],
            unread=[TYPE_LABEL[t] for t in plugin.label_types],
        )
    best: dict[str, LabelState] = {}

    def offer(st: LabelState) -> None:
        cur = best.get(st.type)
        if cur is None or STATE_ORDER[st.state] < STATE_ORDER[cur.state]:
            best[st.type] = st

    if analysed:
        for lab in v.get("labels") or []:
            if lab.get("type") in plugin.label_types or lab.get("type") in ("paper_tag", "proof_of_purchase"):
                offer(
                    LabelState(
                        lab["type"], lab["state"], lab.get("photo"), lab.get("text"), lab.get("certainty")
                    )
                )
        # Facts the analysis read are labels too, even when it did not list them as such.
        size, c1 = _finding_text(v, "size_label")
        if size:
            offer(LabelState("size_label", "present_readable", None, size, c1))
        comp, c2 = _finding_text(v, "composition")
        if comp:
            offer(LabelState("composition", "present_readable", None, comp, c2))
        code, c3 = _finding_text(v, "product_code")
        if code:
            offer(LabelState("sku_code", "present_readable", None, code, c3))
    # What the local OCR read: always only "probable" (it makes mistakes), and available without a model.
    if ocr.get("size"):
        offer(LabelState("size_label", "present_readable", ocr.get("size_photo"), ocr["size"], "probable"))
    if ocr.get("composition"):
        offer(
            LabelState(
                "composition",
                "present_readable",
                ocr.get("composition_photo"),
                ocr.get("composition_text"),
                "probable",
            )
        )
    for kind, number in (ocr.get("codes") or {}).items():
        label = f"{kind.upper()} {number}" if kind in ("rn", "ca") else number
        offer(
            LabelState(
                "barcode" if kind == "barcode" else "sku_code", "present_readable", None, label, "probable"
            )
        )
    if ocr.get("brands"):
        offer(
            LabelState(
                "brand_label", "present_readable", ocr.get("brand_photo"), ocr["brands"][0], "probable"
            )
        )
    brand, _ = _finding_text(v, "brand") if analysed else (None, None)
    brand = brand or (ocr["brands"][0] if ocr.get("brands") else None)

    missing_state = "not_visible" if analysed else "insufficient_information"
    states = [best.get(t) or LabelState(t, missing_state) for t in plugin.label_types]
    for extra in ("paper_tag", "proof_of_purchase"):  # reported when seen, not required
        if extra in best and extra not in plugin.label_types:
            states.append(best[extra])

    text_blob = " ".join(s.text for s in states if s.text and s.state == "present_readable")
    composition_source = (
        next((s.text for s in states if s.type == "composition" and s.text), None) or text_blob
    )
    codes: dict[str, str] = {}
    for kind, number in RN_CA.findall(text_blob):
        codes[kind.lower()] = number
    if m := BARCODE.search(text_blob):
        codes["barcode"] = m.group(1)
    sku = next((s.text for s in states if s.type == "sku_code" and s.text), None)
    if sku:
        codes["sku"] = sku
    unread = [
        TYPE_LABEL[s.type]
        for s in states
        if s.state in ("not_visible", "present_unreadable", "insufficient_information")
        and s.type in plugin.label_types
    ]
    return LabelReport(
        states,
        size_text=next((s.text for s in states if s.type == "size_label" and s.text), None),
        composition=parse_composition(composition_source),
        composition_text=composition_source or None,
        codes=codes,
        brand_text=brand,
        unread=unread,
    )
