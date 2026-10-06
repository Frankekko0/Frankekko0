"""Image analysis.

* :class:`HeuristicImageAnalyzer` (always available): downloads public listing photos with strict
  limits, computes perceptual hashes (duplicate/stolen-photo detection) and photo-quality metrics.
  It does not guess brands or defects - it has no evidence for that.
* :class:`ClaudeVisionAnalyzer` (when ``AI_API_KEY`` is set): reads logos, labels (size,
  composition, product codes), visible defects and authenticity red flags, each with an explicit
  certainty level. It never asserts authenticity.

Downloads are SSRF-safe: https only, public IP addresses only, size and time limits, image
content types only.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

import httpx

from app.ai.llm import LLMClient
from app.core.logging import get_logger
from app.domain.enums import Certainty
from app.vision.phash import dhash, load_image, photo_quality
from app.vision.types import (
    DefectFinding,
    ImageAnalysis,
    PhotoCheck,
    PhotoEvidence,
    PhotoQuality,
    Provenance,
    VisualFinding,
)

log = get_logger(__name__)
MAX_IMAGE_BYTES = 12 * 1024 * 1024  # full-resolution photos
MAX_IMAGES = 20  # every photo a Vinted listing can have
FETCH_CONCURRENCY = 4
ALLOWED_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")


def is_public_https_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


async def fetch_image(client: httpx.AsyncClient, url: str) -> bytes | None:
    if not await asyncio.to_thread(is_public_https_url, url):
        return None
    try:
        async with client.stream("GET", url, follow_redirects=False) as resp:
            if resp.status_code != 200:
                return None
            ctype = resp.headers.get("content-type", "").split(";")[0].strip()
            if ctype not in ALLOWED_TYPES:
                return None
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError:
        return None


class ImageAnalyzer(ABC):
    name = "abstract"

    @abstractmethod
    async def analyze(
        self, image_urls: list[str], provided_hashes: list[str | None], context: dict[str, Any]
    ) -> ImageAnalysis: ...


class HeuristicImageAnalyzer(ImageAnalyzer):
    """Every photo at full resolution: perceptual hash and whether it can prove anything."""

    name = "heuristic"

    async def analyze(
        self, image_urls: list[str], provided_hashes: list[str | None], context: dict[str, Any]
    ) -> ImageAnalysis:
        urls = image_urls[:MAX_IMAGES]
        hashes: list[str | None] = [
            provided_hashes[i] if i < len(provided_hashes) else None for i in range(len(urls))
        ]
        checks: list[PhotoCheck] = []
        gate = asyncio.Semaphore(FETCH_CONCURRENCY)
        async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": "FlipFinder/1.0"}) as client:

            async def one(url: str) -> bytes | None:
                if not url.startswith("https://"):
                    return None
                async with gate:
                    return await fetch_image(client, url)

            blobs = await asyncio.gather(*(one(u) for u in urls))
        for i, blob in enumerate(blobs):
            if not blob:
                continue
            try:
                img = load_image(blob)
            except Exception as exc:
                log.info("vision.image_decode_failed", error=type(exc).__name__)
                continue
            hashes[i] = dhash(img)
            q = photo_quality(img)
            checks.append(
                PhotoCheck(
                    photo=i,
                    usable=bool(q["usable"]),
                    reason=q["reason"],
                    width=q["width"],
                    height=q["height"],
                    sharpness=q["sharpness"],
                    screenshot=bool(q["screenshot"]),
                )
            )
        sharp = [c.sharpness for c in checks if c.sharpness is not None]
        usable = [c for c in checks if c.usable]
        count = len(image_urls)
        score = min(100, 20 * min(count, 4) + (20 if checks and len(usable) >= len(checks) / 2 else 0))
        quality = PhotoQuality(
            photo_count=count,
            analyzed_count=len(checks),
            avg_sharpness=round(sum(sharp) / len(sharp), 3) if sharp else None,
            score=max(0, score),
        )
        notes = []
        if checks and len(usable) < len(checks):
            bad = [f"foto {c.photo + 1} {c.reason}" for c in checks if not c.usable]
            notes.append("Foto non utilizzabili per le verifiche: " + ", ".join(bad))
        shots = [c.photo for c in checks if c.screenshot]
        return ImageAnalysis(
            analyzer=self.name,
            photo_quality=quality,
            perceptual_hashes=sorted({h for h in hashes if h}),
            photo_hashes=hashes,
            photo_checks=checks,
            provenance=Provenance(
                screenshots=len(shots),
                notes=[f"Foto {i + 1}: sembra uno screenshot" for i in shots],
            ),
            notes=notes,
        )


VISION_SYSTEM = """Analizzi le foto di un annuncio di abbigliamento usato per un reseller che compra
con soldi propri: un falso non rilevato è una perdita. Le foto sono numerate ("Foto 1", "Foto 2"...).
Riporta solo ciò che vedi. Per ogni informazione indica la certezza:
- "certain": letta chiaramente (es. testo di un'etichetta leggibile);
- "probable": dedotta con buona evidenza visiva;
- "unverifiable": non verificabile dalle foto.
Esamina OGNI foto: etichette, codici, font, logo, cuciture, zip, bottoni, materiali e la coerenza tra
le foto. Per ogni dettaglio controllato aggiungi una voce in photo_findings con il numero della foto
(da 1), il tipo, l'esito (consistent / concern / unreadable), la certezza, cosa hai visto e il
riquadro [x, y, larghezza, altezza] in frazioni 0-1 della foto dove si trova il dettaglio.
Una foto sfocata, tagliata, troppo lontana o troppo scura per leggere un dettaglio è "unreadable":
mai "consistent". In photo_checks indica per ogni foto se è utilizzabile e perché no.
In provenance conta le foto che non ritraggono l'articolo reale: foto di catalogo o stock,
screenshot, filigrane di altri siti, foto ritoccate o generate.
Non dichiarare MAI che un articolo è autentico: al massimo che i dettagli visti sono coerenti.
Se un'informazione non è visibile, usa null."""

PHOTO_KINDS = [
    "label",
    "care_tag",
    "code",
    "logo",
    "stitching",
    "zip",
    "button",
    "material",
    "overall",
    "sole",
    "insole",
    "box_label",
    "hardware",
]


def _finding_schema() -> dict[str, Any]:
    return {
        "type": ["object", "null"],
        "properties": {
            "value": {"type": "string"},
            "certainty": {"type": "string", "enum": ["certain", "probable", "unverifiable"]},
            "confidence": {"type": "number"},
            "evidence": {"type": ["string", "null"]},
        },
        "required": ["value", "certainty", "confidence", "evidence"],
        "additionalProperties": False,
    }


VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **{
            k: _finding_schema()
            for k in (
                "brand",
                "logo",
                "model",
                "size_label",
                "composition",
                "product_code",
                "category",
                "color",
                "condition_estimate",
            )
        },
        "defects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["stain", "hole", "fading", "wear", "pilling", "tear", "other"],
                    },
                    "severity": {"type": "string", "enum": ["minor", "moderate", "severe"]},
                    "certainty": {"type": "string", "enum": ["certain", "probable", "unverifiable"]},
                    "description": {"type": ["string", "null"]},
                },
                "required": ["kind", "severity", "certainty", "description"],
                "additionalProperties": False,
            },
        },
        "authenticity_concerns": {"type": "array", "items": {"type": "string"}},
        "authenticity_positive_signals": {"type": "array", "items": {"type": "string"}},
        "has_label_photo": {"type": "boolean"},
        "photo_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "photo": {"type": "integer"},
                    "kind": {"type": "string", "enum": PHOTO_KINDS},
                    "verdict": {"type": "string", "enum": ["consistent", "concern", "unreadable"]},
                    "certainty": {"type": "string", "enum": ["certain", "probable", "unverifiable"]},
                    "detail": {"type": "string"},
                    "box": {"type": ["array", "null"], "items": {"type": "number"}},
                },
                "required": ["photo", "kind", "verdict", "certainty", "detail", "box"],
                "additionalProperties": False,
            },
        },
        "photo_checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "photo": {"type": "integer"},
                    "usable": {"type": "boolean"},
                    "reason": {
                        "type": ["string", "null"],
                        "enum": ["sfocata", "tagliata", "troppo lontana", "troppo scura", None],
                    },
                },
                "required": ["photo", "usable", "reason"],
                "additionalProperties": False,
            },
        },
        "provenance": {
            "type": "object",
            "properties": {
                "stock_or_catalog": {"type": "integer"},
                "screenshots": {"type": "integer"},
                "foreign_watermarks": {"type": "integer"},
                "edited_or_generated": {"type": "integer"},
                "notes": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "stock_or_catalog",
                "screenshots",
                "foreign_watermarks",
                "edited_or_generated",
                "notes",
            ],
            "additionalProperties": False,
        },
    },
    "required": [
        "brand",
        "logo",
        "model",
        "size_label",
        "composition",
        "product_code",
        "category",
        "color",
        "condition_estimate",
        "defects",
        "authenticity_concerns",
        "authenticity_positive_signals",
        "has_label_photo",
        "photo_findings",
        "photo_checks",
        "provenance",
    ],
    "additionalProperties": False,
}


class ClaudeVisionAnalyzer(ImageAnalyzer):
    name = "claude_vision"

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self.heuristic = HeuristicImageAnalyzer()

    async def analyze(
        self, image_urls: list[str], provided_hashes: list[str | None], context: dict[str, Any]
    ) -> ImageAnalysis:
        base = await self.heuristic.analyze(image_urls, provided_hashes, context)
        # Every photo, numbered by its gallery position (the answer refers to these numbers).
        numbered = [
            (i, u)
            for i, u in enumerate(image_urls[:MAX_IMAGES])
            if u.startswith("https://") and await asyncio.to_thread(is_public_https_url, u)
        ]
        if not numbered:
            return base
        content: list[dict[str, Any]] = []
        for i, u in numbered:
            content.append({"type": "text", "text": f"Foto {i + 1}"})
            content.append({"type": "image", "source": {"type": "url", "url": u}})
        rules = context.get("brand_rules") or {}
        content.append(
            {
                "type": "text",
                "text": f"Titolo: {context.get('title', '')}\nBrand dichiarato: {context.get('brand') or 'non indicato'}\n"
                f"Valori ammessi per 'category': {', '.join(context.get('category_slugs', []))}\n"
                + (
                    f"Foto chiave per questo brand: {', '.join(rules.get('key_photos', []))}\n"
                    if rules
                    else ""
                )
                + (
                    f"Controlli noti per questo brand: {'; '.join(rules.get('checks', []))}\n"
                    if rules.get("checks")
                    else ""
                )
                + "Analizza ogni foto e compila i campi richiesti.",
            }
        )
        data = await self.llm.structured(
            system=VISION_SYSTEM, content=content, schema=VISION_SCHEMA, purpose="vision"
        )
        if data is None:
            return base

        def finding(key: str) -> VisualFinding | None:
            raw = data.get(key)
            if not raw or not raw.get("value"):
                return None
            try:
                return VisualFinding(
                    value=str(raw["value"])[:120],
                    certainty=Certainty(raw.get("certainty", "probable")),
                    confidence=max(0.0, min(1.0, float(raw.get("confidence", 0.5)))),
                    evidence=(raw.get("evidence") or None),
                )
            except (ValueError, TypeError):
                return None

        defects = []
        for d in data.get("defects", [])[:10]:
            try:
                defects.append(DefectFinding.model_validate(d))
            except ValueError:
                continue
        base.analyzer = self.name
        base.brand, base.logo, base.model = finding("brand"), finding("logo"), finding("model")
        base.size_label, base.composition = finding("size_label"), finding("composition")
        base.product_code, base.category = finding("product_code"), finding("category")
        base.color, base.condition_estimate = finding("color"), finding("condition_estimate")
        base.defects = defects
        base.authenticity_concerns = [str(c)[:160] for c in data.get("authenticity_concerns", [])][:6]
        base.authenticity_positive_signals = [
            str(c)[:160] for c in data.get("authenticity_positive_signals", [])
        ][:6]
        base.photo_quality.has_label_photo = bool(data.get("has_label_photo"))
        positions = {i for i, _ in numbered}
        for f in data.get("photo_findings", [])[:60]:
            try:
                ev = PhotoEvidence(
                    photo=int(f["photo"]) - 1,
                    kind=str(f["kind"]),
                    verdict=f["verdict"],
                    certainty=Certainty(f.get("certainty", "probable")),
                    detail=str(f.get("detail") or "")[:200],
                    box=_box(f.get("box")),
                )
            except (KeyError, ValueError, TypeError):
                continue
            if ev.photo in positions:
                base.photo_findings.append(ev)
        local = {c.photo: c for c in base.photo_checks}
        for c in data.get("photo_checks", []):
            try:
                i = int(c["photo"]) - 1
            except (KeyError, ValueError, TypeError):
                continue
            # A photo is unusable if either the local measure or the visual check says so.
            if i in local and not c.get("usable", True) and local[i].usable:
                local[i].usable = False
                local[i].reason = c.get("reason") or "non leggibile"
        prov = data.get("provenance") or {}
        p = base.provenance
        p.stock_or_catalog = max(p.stock_or_catalog, int(prov.get("stock_or_catalog") or 0))
        p.screenshots = max(p.screenshots, int(prov.get("screenshots") or 0))
        p.foreign_watermarks = int(prov.get("foreign_watermarks") or 0)
        p.edited_or_generated = int(prov.get("edited_or_generated") or 0)
        p.notes += [str(n)[:160] for n in prov.get("notes", [])][:6]
        return base


def _box(raw: Any) -> list[float] | None:
    """[x, y, w, h] clamped to the photo, or None when malformed."""
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    x, y, w, h = (max(0.0, min(1.0, float(v))) for v in raw)
    if w <= 0 or h <= 0:
        return None
    return [round(x, 4), round(y, 4), round(min(w, 1 - x), 4), round(min(h, 1 - y), 4)]


def get_image_analyzer(llm: LLMClient) -> ImageAnalyzer:
    if llm.enabled and llm.settings.ai_vision_enabled:
        return ClaudeVisionAnalyzer(llm)
    return HeuristicImageAnalyzer()
