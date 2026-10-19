"""Local OCR for labels: free, offline, and available without any model key.

The engine is RapidOCR (ONNX, pure ``pip``: models are bundled in the wheel, no system package
such as Tesseract is needed). It reads the text on the photos at full resolution; the text then
feeds the label report (size, composition, RN/CA codes, country, brand) with ``probable``
certainty (OCR makes mistakes, spaces vanish: "SIZEM"). The multimodal model is told what the OCR
read and is asked only for what it could not (cheaper, and two independent readings can agree or
disagree).

If the package is not installed or ``OCR_ENABLED`` is false the engine is simply absent and
everything works as before: nothing is claimed that was not read.
"""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

from PIL import Image

from app.analysis.labels import RN_CA, parse_composition
from app.core.config import get_settings
from app.core.logging import get_logger
from app.identification.taxonomy import BRANDS, fold

log = get_logger(__name__)
MAX_SIDE = 1600  # px: larger photos are shrunk before reading (speed), small print is still legible
MIN_CONFIDENCE = 0.5
MAX_LINES = 40

# A size is read only where a label says it: after an explicit size word (the long ones also glued to the size, as the
# OCR loses spaces: "SIZEM"), after "TG" as a separate word, or as a line of its own on a photo that carries other label
# text. A bare letter is not a size ("Originals TM", "SPORTS", "WASH COLD T30" were read as M, S and 30).
_SIZES = r"(XXXL|XXL|XL|XXS|XS|S|M|L|\d{2}(?:/\d{2})?)"
_SIZE_WORD = re.compile(r"(?:SIZE|TAGLIA|TAILLE|GROSSE|GRÖSSE|TALLA)[:.]?" + _SIZES + r"(?![A-Z])")
_SIZE_TG = re.compile(r"(?<![A-Z0-9])TG\.?\s?" + _SIZES + r"(?![A-Z0-9])")
_SIZE_ALONE = re.compile(r"^" + _SIZES + r"$")
# "MADE IN <country>" within one line (spaces kept), or a line that is only the glued words.
_COUNTRY_SPACED = re.compile(
    r"(?<![A-Z])(?:MADE\s+IN|FABRIQU[EÉ]\s+EN|HERGESTELLT\s+IN)\s+([A-Z]{3,20})(?![A-Z])"
)
_COUNTRY_GLUED = re.compile(r"^(?:MADEIN|FABRIQU[EÉ]ENF?|HERGESTELLTIN)([A-Z]{3,20})$")
_COUNTRY_OPEN = re.compile(r"(?:MADEIN|FABRIQU[EÉ]EN|HERGESTELLTIN)$")  # the country is on the next line
_URL = re.compile(r"www\.|http|\.com|\.it\b|\.net|@")
_BARCODE = re.compile(r"\b(\d{12,13})\b")


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    box: list[float]  # [x, y, w, h], 0..1 of the photo

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "confidence": round(self.confidence, 2), "box": self.box}


class OcrEngine(Protocol):
    name: str

    def read(self, image: Image.Image) -> list[OcrLine]: ...


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR  # imported here: optional dependency

        self._engine = RapidOCR()
        self._lock = threading.Lock()  # the ONNX session is not shared between threads

    def read(self, image: Image.Image) -> list[OcrLine]:
        import numpy as np

        img = image.convert("RGB")
        w, h = img.size
        if max(w, h) > MAX_SIDE:
            scale = MAX_SIDE / max(w, h)
            img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
            w, h = img.size
        with self._lock:
            result, _ = self._engine(np.asarray(img))
        lines: list[OcrLine] = []
        for box, text, conf in result or []:
            conf = float(conf)
            text = str(text).strip()
            if conf < MIN_CONFIDENCE or not text:
                continue
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            x0, y0, x1, y1 = (
                max(0.0, min(xs)),
                max(0.0, min(ys)),
                min(float(w), max(xs)),
                min(float(h), max(ys)),
            )
            lines.append(
                OcrLine(
                    text[:120],
                    conf,
                    [round(x0 / w, 4), round(y0 / h, 4), round((x1 - x0) / w, 4), round((y1 - y0) / h, 4)],
                )
            )
        return lines[:MAX_LINES]


_engine: OcrEngine | None = None
_tried = False


def get_ocr_engine() -> OcrEngine | None:
    """The OCR engine, or ``None`` when it is switched off or not installed."""
    global _engine, _tried
    if _tried:
        return _engine
    _tried = True
    if not get_settings().ocr_enabled:
        return None
    try:
        _engine = RapidOcrEngine()
    except Exception as exc:  # not installed, or its runtime failed to start
        log.info("ocr.unavailable", error=type(exc).__name__)
        _engine = None
    return _engine


def set_ocr_engine(engine: OcrEngine | None) -> None:
    """Replace the engine (tests, or a different engine)."""
    global _engine, _tried
    _engine, _tried = engine, True


async def read_photo(engine: OcrEngine, image: Image.Image) -> list[OcrLine]:
    try:
        return await asyncio.to_thread(engine.read, image)
    except Exception as exc:  # a photo that cannot be read costs nothing else
        log.info("ocr.failed", error=type(exc).__name__)
        return []


# ------------------------------------------------------------------ what the text says
@dataclass
class OcrFacts:
    size: str | None = None
    size_photo: int | None = None
    composition: dict[str, int] = field(default_factory=dict)
    composition_text: str | None = None
    composition_photo: int | None = None
    codes: dict[str, str] = field(default_factory=dict)
    country: str | None = None
    brands: list[str] = field(default_factory=list)  # a brand name read on a photo that carries label text
    brand_photo: int | None = None
    label_photos: list[int] = field(default_factory=list)  # photos that show label-like text
    # A brand name read on a photo with no label text around it (a logo, a bag in the background): a lead, not a label.
    logo_text: list[str] = field(default_factory=list)
    # How surely the lines each fact came from were read (the lowest of them): the OCR is a reading, not a measurement.
    confidence: dict[str, float] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return bool(self.size or self.composition or self.codes or self.country or self.brands)


def _compact(text: str) -> str:
    return re.sub(r"[\s_]+", "", text.upper())


_BRAND_ALIASES = sorted(
    {
        (" ".join(fold(alias).split()), fold(alias).replace(" ", ""), b.name)
        for b in BRANDS
        for alias in (b.name, *b.aliases)
        if len(alias) >= 4
    },
    key=lambda t: -len(t[1]),
)
_WORDS = re.compile(r"[a-z0-9]+")


def _brand_in(line_text: str) -> str | None:
    """The brand a single line of text names, as whole words (or the glued word the OCR makes of them); a line that is
    a web address names nothing ("SHOP AT NIKE.COM" on a bag is not a Nike label)."""
    if _URL.search(line_text.lower()):
        return None
    tokens = _WORDS.findall(fold(line_text))
    joined, squashed = " " + " ".join(tokens) + " ", "".join(tokens)
    for spaced, glued, name in _BRAND_ALIASES:
        if f" {spaced} " in joined or squashed == glued:
            return name
    return None


def _country_of(lines: list[OcrLine]) -> tuple[str, float] | None:
    """The country of "made in ...", read line by line (never across two lines that happen to follow each other)."""
    for i, line in enumerate(lines):
        up = line.text.upper()
        m = _COUNTRY_SPACED.search(up) or _COUNTRY_GLUED.match(_compact(line.text))
        if m:
            return m.group(1).title(), line.confidence
        if _COUNTRY_OPEN.search(_compact(line.text)) and i + 1 < len(lines):
            nxt = _compact(lines[i + 1].text)
            if re.fullmatch(r"[A-Z]{3,20}", nxt):
                return nxt.title(), min(line.confidence, lines[i + 1].confidence)
    return None


def parse_ocr(photos: list[tuple[int, list[OcrLine]]]) -> OcrFacts:
    """Facts from the OCR text of the photos: ``(photo index, lines)`` pairs.

    Spaces are often lost by OCR, so the long size words are matched on the compacted uppercase text. Each fact is read
    from single lines (a country is never glued to the next line's text), and every line keeps its confidence."""
    facts = OcrFacts()
    for photo, lines in photos:
        label_like = False
        text_all = " ".join(line.text for line in lines)
        size_here: tuple[str, float] | None = None
        for line in lines:
            c = _compact(line.text)
            if facts.size is None and size_here is None:
                m = _SIZE_WORD.search(c) or _SIZE_TG.search(line.text.upper())
                if m:
                    size_here = (m.group(1), line.confidence)
            if m := RN_CA.search(line.text.replace(" ", "")) or RN_CA.search(line.text):
                facts.codes[m.group(1).lower()] = m.group(2)
                label_like = True
            if b := _BARCODE.search(line.text.replace(" ", "")):
                facts.codes.setdefault("barcode", b.group(1))
                label_like = True
        comp = parse_composition(text_all)
        if comp and (not facts.composition or sum(comp.values()) > sum(facts.composition.values())):
            facts.composition, facts.composition_text, facts.composition_photo = comp, text_all[:160], photo
            shares = [x.confidence for x in lines if "%" in x.text] or [x.confidence for x in lines]
            facts.confidence["composition"] = round(min(shares), 2)
            label_like = True
        if facts.country is None and (found := _country_of(lines)):
            facts.country = found[0]
            facts.confidence["country"] = round(found[1], 2)
            label_like = True
        if size_here is not None:
            label_like = True
        elif facts.size is None and label_like:
            # A size on a line of its own ("M", "42") counts only beside other label text.
            for line in lines:
                if m := _SIZE_ALONE.match(_compact(line.text)):
                    size_here = (m.group(1), line.confidence)
                    break
        if facts.size is None and size_here is not None:
            facts.size, facts.size_photo = size_here[0], photo
            facts.confidence["size"] = round(size_here[1], 2)
        for line in lines:
            if name := _brand_in(line.text):
                target = facts.brands if label_like else facts.logo_text
                if name not in target:
                    target.append(name)
                    if label_like and facts.brand_photo is None:
                        facts.brand_photo = photo
                break
        if label_like:
            facts.label_photos.append(photo)
    return facts
