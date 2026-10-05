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
from app.vision.phash import brightness, dhash, load_image, sharpness
from app.vision.types import DefectFinding, ImageAnalysis, PhotoQuality, VisualFinding

log = get_logger(__name__)
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 6
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
    name = "heuristic"

    async def analyze(
        self, image_urls: list[str], provided_hashes: list[str | None], context: dict[str, Any]
    ) -> ImageAnalysis:
        hashes = [h for h in provided_hashes if h]
        sharp: list[float] = []
        bright: list[float] = []
        analyzed = 0
        remote = [u for u in image_urls[:MAX_IMAGES] if u.startswith("https://")]
        if remote:
            async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "FlipFinder/0.1"}) as client:
                blobs = await asyncio.gather(*(fetch_image(client, u) for u in remote))
            for blob in blobs:
                if not blob:
                    continue
                try:
                    img = load_image(blob)
                except Exception as exc:
                    log.info("vision.image_decode_failed", error=type(exc).__name__)
                    continue
                analyzed += 1
                hashes.append(dhash(img))
                sharp.append(sharpness(img))
                bright.append(brightness(img))
        count = len(image_urls)
        score = min(100, 20 * min(count, 4) + (20 if analyzed and sum(sharp) / len(sharp) > 0.3 else 0))
        quality = PhotoQuality(
            photo_count=count,
            analyzed_count=analyzed,
            avg_sharpness=round(sum(sharp) / len(sharp), 3) if sharp else None,
            avg_brightness=round(sum(bright) / len(bright), 3) if bright else None,
            score=max(0, score),
        )
        notes = []
        if quality.avg_sharpness is not None and quality.avg_sharpness < 0.15:
            notes.append("Foto poco nitide")
        if quality.avg_brightness is not None and quality.avg_brightness < 0.2:
            notes.append("Foto molto scure")
        return ImageAnalysis(
            analyzer=self.name, photo_quality=quality, perceptual_hashes=sorted(set(hashes)), notes=notes
        )


VISION_SYSTEM = """Analizzi le foto di un annuncio di abbigliamento usato per un reseller.
Riporta solo ciò che vedi. Per ogni informazione indica la certezza:
- "certain": letta chiaramente (es. testo di un'etichetta leggibile);
- "probable": dedotta con buona evidenza visiva;
- "unverifiable": non verificabile dalle foto.
Non dichiarare MAI che un articolo è autentico. Puoi elencare elementi coerenti con l'originale
(authenticity_positive_signals) o elementi sospetti (authenticity_concerns: logo deformato,
cuciture irregolari, etichette incoerenti, font errati...). Se un'informazione non è visibile, usa null."""


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
        public = [u for u in image_urls[:MAX_IMAGES] if u.startswith("https://")]
        public = [u for u in public if await asyncio.to_thread(is_public_https_url, u)]
        if not public:
            return base
        content: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "url", "url": u}} for u in public
        ]
        content.append(
            {
                "type": "text",
                "text": f"Titolo: {context.get('title', '')}\nBrand dichiarato: {context.get('brand') or 'non indicato'}\n"
                f"Valori ammessi per 'category': {', '.join(context.get('category_slugs', []))}\n"
                "Analizza le foto e compila i campi richiesti.",
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
        return base


def get_image_analyzer(llm: LLMClient) -> ImageAnalyzer:
    if llm.enabled and llm.settings.ai_vision_enabled:
        return ClaudeVisionAnalyzer(llm)
    return HeuristicImageAnalyzer()
