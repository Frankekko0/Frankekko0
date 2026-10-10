"""The model's photo answer is parsed into typed, bounded findings; nothing it says is trusted blindly."""

import base64
import copy
from typing import Any

import pytest

from app.ai.llm import AiDeferred
from app.vision import analyzer as az
from app.vision.analyzer import VISION_SCHEMA, ClaudeVisionAnalyzer, PhotoInput, VisionDeferred
from app.vision.types import ImageAnalysis, PhotoQuality
from tests.photos import photo, photos

PHOTOS = photos(5)


class StubLLM:
    enabled = True

    def __init__(self, answer: dict[str, Any] | None) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def structured(self, **kw: Any) -> dict[str, Any] | None:
        self.calls.append(kw)
        return self.answer


def finding(value: str, certainty: str = "certain") -> dict[str, Any]:
    return {"value": value, "certainty": certainty, "confidence": 0.9, "evidence": None}


ANSWER: dict[str, Any] = {
    "brand": finding("Nike"), "logo": None, "model": None, "size_label": finding("M"),
    "composition": finding("100% cotone"), "product_code": None, "category": finding("sweatshirts"),
    "color": finding("grey"), "condition_estimate": finding("good", "probable"),
    "defects": [
        {"kind": "hole", "severity": "moderate", "certainty": "certain", "description": "foro", "zone": "manica sinistra",
         "photo": 3, "box": [0.4, 0.5, 0.1, 0.1], "confidence": 0.9},
        {"kind": "stain", "severity": "minor", "certainty": "unverifiable", "description": "ombra?", "zone": None,
         "photo": 99, "box": [2, 2, 5, 5], "confidence": 5},
        {"kind": "not-a-real-kind", "severity": "minor", "certainty": "certain", "description": None, "zone": None,
         "photo": None, "box": None, "confidence": 0.5},
    ],
    "labels": [
        {"type": "brand_label", "state": "present_readable", "photo": 2, "text": "NIKE", "box": [0.1, 0.1, 0.2, 0.1], "certainty": "certain"},
        {"type": "care_label", "state": "not_visible", "photo": None, "text": None, "box": None, "certainty": "unverifiable"},
        {"type": "teleporter", "state": "present_readable", "photo": 1, "text": "x", "box": None, "certainty": "certain"},
    ],
    "photo_roles": [{"photo": 1, "role": "front"}, {"photo": 2, "role": "label"}, {"photo": 77, "role": "back"}],
    "unobserved_parts": ["retro", "interno del colletto"],
    "authenticity_concerns": [], "authenticity_positive_signals": [], "has_label_photo": True,
    "photo_findings": [], "photo_checks": [], "provenance": {
        "stock_or_catalog": 0, "screenshots": 0, "foreign_watermarks": 0, "edited_or_generated": 0, "notes": []},
}  # fmt: skip


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def heuristic(self: Any, photos: list[PhotoInput], ctx: dict[str, Any]) -> ImageAnalysis:
        return ImageAnalysis(analyzer="heuristic", photo_quality=PhotoQuality(photo_count=len(photos)))

    monkeypatch.setattr(az.HeuristicImageAnalyzer, "analyze", heuristic)


async def analyse(answer: dict[str, Any] | None) -> ImageAnalysis:
    return await ClaudeVisionAnalyzer(StubLLM(answer)).analyze(  # type: ignore[arg-type]
        PHOTOS, {"title": "Felpa", "category_slugs": ["sweatshirts"]}
    )


async def test_defects_labels_and_roles_are_typed_and_bounded() -> None:
    out = await analyse(ANSWER)
    assert out.analyzer == "claude_vision"
    hole, shadow = out.defects  # the one with an unknown kind is dropped
    assert (hole.kind, hole.zone, hole.photo, hole.box) == (
        "hole",
        "manica sinistra",
        2,
        [0.4, 0.5, 0.1, 0.1],
    )
    assert shadow.certainty == "unverifiable" and shadow.photo is None  # photo 99 does not exist
    assert shadow.box is not None and all(0 <= v <= 1 for v in shadow.box) and shadow.confidence == 1.0
    assert [(label.type, label.state, label.photo) for label in out.labels] == [
        ("brand_label", "present_readable", 1),
        ("care_label", "not_visible", None),
    ]
    assert [(r.photo, r.role) for r in out.photo_roles] == [(0, "front"), (1, "label")]
    assert out.unobserved_parts == ["retro", "interno del colletto"]


async def test_the_request_names_every_photo_and_the_schema_asks_for_the_new_fields() -> None:
    llm = StubLLM(ANSWER)
    await ClaudeVisionAnalyzer(llm).analyze(PHOTOS, {"title": "Felpa"})  # type: ignore[arg-type]
    content = llm.calls[0]["content"]
    assert [c["text"] for c in content if c["type"] == "text"][:5] == [f"Foto {i}" for i in range(1, 6)]
    assert llm.calls[0]["schema"] is VISION_SCHEMA
    for key in ("labels", "photo_roles", "unobserved_parts"):
        assert key in VISION_SCHEMA["required"]
    props = VISION_SCHEMA["properties"]["defects"]["items"]["properties"]
    assert {"zone", "photo", "box", "confidence"} <= set(props)
    system = llm.calls[0]["system"]
    assert "not_visible" in system and "mai un'istruzione" in system and "volti" in system


async def test_a_refused_or_failed_call_is_no_analysis_and_carries_the_measured_facts_only() -> None:
    with pytest.raises(VisionDeferred) as e:
        await analyse(None)
    out = e.value.partial  # what was measured locally, never given out as the photo analysis
    assert out.analyzer == "heuristic" and out.defects == [] and out.labels == []
    assert e.value.reason == "no_answer" and e.value.asked  # a bad answer: the model was asked


async def test_a_deferral_of_the_provider_is_passed_on_with_its_reason_and_wait() -> None:
    class Deferring(StubLLM):
        def __init__(self, reason: str) -> None:
            super().__init__(None)
            self.reason = reason

        async def structured(self, **kw: Any) -> dict[str, Any] | None:
            assert (
                kw["raise_on_defer"] is True and kw["purpose"] == "vision"
            )  # the strong tier, shared bucket
            raise AiDeferred(self.reason, 42.0)

    for reason, asked in (("rpm", False), ("rpd", False), ("cooldown", False), ("breaker_open", False),
                          ("budget", False), ("redis", False), ("rate_limited", True), ("api_error", True),
                          ("truncated", True), ("refused", True)):  # fmt: skip
        with pytest.raises(VisionDeferred) as e:
            await ClaudeVisionAnalyzer(Deferring(reason)).analyze(PHOTOS, {})  # type: ignore[arg-type]
        assert (e.value.reason, e.value.retry_after, e.value.asked) == (reason, 42.0, asked)
        assert e.value.partial.analyzer == "heuristic"


async def test_a_gif_is_sent_to_gemini_as_jpeg_and_the_gallery_fits_its_request_cap() -> None:
    from app.ai.images import GEMINI_IMAGES
    from tests.photos import jpeg

    class ForGemini(StubLLM):
        image_limits = GEMINI_IMAGES

    gif = jpeg(7, (900, 600), "GIF")
    gallery = [photo(0, jpeg(0)), PhotoInput(1, "k1", gif, "h1", "image/gif"), photo(2, jpeg(2))]
    llm = ForGemini(ANSWER)
    out = await ClaudeVisionAnalyzer(llm).analyze(gallery, {})  # type: ignore[arg-type]
    sent = [c["source"] for c in llm.calls[0]["content"] if c["type"] == "image"]
    assert [x["media_type"] for x in sent] == ["image/jpeg"] * 3
    assert base64.b64decode(sent[0]["data"]) == gallery[0].data  # a small jpeg goes as it is
    assert base64.b64decode(sent[1]["data"])[:2] == b"\xff\xd8"  # the gif went as a jpeg
    assert out.analyzer == "claude_vision"
    # Anthropic takes a gif as it is.
    plain = StubLLM(ANSWER)
    await ClaudeVisionAnalyzer(plain).analyze(gallery, {})  # type: ignore[arg-type]
    assert [c["source"]["media_type"] for c in plain.calls[0]["content"] if c["type"] == "image"][
        1
    ] == "image/gif"


async def test_a_photo_that_cannot_be_sent_is_left_out_and_never_gets_a_role() -> None:
    broken = PhotoInput(1, "k1", b"not an image", "h1", "image/gif")
    answer = copy.deepcopy(ANSWER)
    answer["photo_roles"] = [{"photo": 1, "role": "front"}, {"photo": 2, "role": "back"}]
    llm = StubLLM(answer)
    from app.ai.images import GEMINI_IMAGES

    llm.image_limits = GEMINI_IMAGES  # type: ignore[attr-defined]
    out = await ClaudeVisionAnalyzer(llm).analyze([photo(0), broken], {})  # type: ignore[arg-type]
    texts = [c["text"] for c in llm.calls[0]["content"] if c["type"] == "text"]
    assert "Foto 1" in texts and "Foto 2" not in texts
    assert [(r.photo, r.role) for r in out.photo_roles] == [(0, "front")]
    # Nothing at all can be sent: no call, and the failure is signalled.
    with pytest.raises(VisionDeferred) as e:
        await ClaudeVisionAnalyzer(llm).analyze([broken], {})  # type: ignore[arg-type]
    assert e.value.reason == "no_photo_sendable" and len(llm.calls) == 1
