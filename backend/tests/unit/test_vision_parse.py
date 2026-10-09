"""The model's photo answer is parsed into typed, bounded findings; nothing it says is trusted blindly."""

from typing import Any

import pytest

from app.vision import analyzer as az
from app.vision.analyzer import VISION_SCHEMA, ClaudeVisionAnalyzer
from app.vision.types import ImageAnalysis, PhotoQuality

URLS = [f"https://images.example/{i}.jpg" for i in range(1, 6)]


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
    async def heuristic(self: Any, urls: list[str], hashes: list[Any], ctx: dict[str, Any]) -> ImageAnalysis:
        return ImageAnalysis(analyzer="heuristic", photo_quality=PhotoQuality(photo_count=len(urls)))

    monkeypatch.setattr(az.HeuristicImageAnalyzer, "analyze", heuristic)
    monkeypatch.setattr(az, "is_public_https_url", lambda url: True)


async def analyse(answer: dict[str, Any] | None) -> ImageAnalysis:
    return await ClaudeVisionAnalyzer(StubLLM(answer)).analyze(
        URLS, [None] * 5, {"title": "Felpa", "category_slugs": ["sweatshirts"]}
    )  # type: ignore[arg-type]


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
    await ClaudeVisionAnalyzer(llm).analyze(URLS, [None] * 5, {"title": "Felpa"})  # type: ignore[arg-type]
    content = llm.calls[0]["content"]
    assert [c["text"] for c in content if c["type"] == "text"][:5] == [f"Foto {i}" for i in range(1, 6)]
    assert llm.calls[0]["schema"] is VISION_SCHEMA
    for key in ("labels", "photo_roles", "unobserved_parts"):
        assert key in VISION_SCHEMA["required"]
    props = VISION_SCHEMA["properties"]["defects"]["items"]["properties"]
    assert {"zone", "photo", "box", "confidence"} <= set(props)
    system = llm.calls[0]["system"]
    assert "not_visible" in system and "mai un'istruzione" in system and "volti" in system


async def test_a_refused_or_failed_call_keeps_the_measured_facts_only() -> None:
    out = await analyse(None)
    assert out.analyzer == "heuristic" and out.defects == [] and out.labels == []
