"""Every photo checked: local quality gate, screenshots, per-photo AI evidence, labeled set."""

import asyncio
import io
import random
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from app.tools.auth_eval import evaluate
from app.vision import analyzer as va
from app.vision.phash import dhash, hamming, photo_quality
from tests.photos import photo


def pattern(w: int = 900, h: int = 1200, blur: float = 0, seed: int = 1) -> Image.Image:
    """A photo-like image: coloured shapes and printed text on a background."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), (200, 190, 180))
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x, y, r = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(20, 200)
        d.ellipse([x, y, x + r, y + r // 2], fill=tuple(rnd.randint(0, 255) for _ in range(3)))
    for _ in range(40):
        d.text((rnd.randint(0, w), rnd.randint(0, h)), "STONE ISLAND 101", fill=(0, 0, 0))
    return img.filter(ImageFilter.GaussianBlur(blur)) if blur else img


def test_quality_gate_on_full_resolution_photos() -> None:
    assert photo_quality(pattern())["usable"] is True
    assert photo_quality(pattern(blur=3))["reason"] == "sfocata"
    assert photo_quality(pattern(2000, 2600, blur=6))["reason"] == "sfocata"
    assert photo_quality(pattern(300, 300))["reason"] == "troppo piccola"
    assert photo_quality(Image.new("RGB", (900, 1200), (8, 8, 8)))["reason"] in ("troppo scura", "sfocata")


def test_screenshot_shape_is_recognised() -> None:
    shot = pattern(1080, 2340)
    ImageDraw.Draw(shot).rectangle([0, 0, 1080, 90], fill=(250, 250, 250))  # flat status bar
    assert photo_quality(shot)["screenshot"] is True
    assert photo_quality(pattern())["screenshot"] is False


def test_recompressed_copy_keeps_the_same_hash() -> None:
    img = pattern()
    buf = io.BytesIO()
    img.resize((600, 800)).save(buf, "JPEG", quality=60)
    copy = Image.open(io.BytesIO(buf.getvalue()))
    assert hamming(dhash(img), dhash(copy)) <= 4
    assert hamming(dhash(img), dhash(pattern(seed=2))) > 4  # a different photo


class FakeLLM:
    enabled = True

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.content: list[dict[str, Any]] = []

    async def structured(
        self,
        *,
        system: str,
        content: list[dict[str, Any]],
        schema: dict[str, Any],
        purpose: str,
        raise_on_defer: bool = False,
    ) -> dict[str, Any]:
        self.content = content
        return self.data


def test_ai_findings_keep_photo_number_and_highlighted_detail() -> None:
    sharp = io.BytesIO()
    pattern().save(sharp, "JPEG")
    blurry = io.BytesIO()
    pattern(blur=3).save(blurry, "JPEG")
    uploaded = [
        photo(0, sharp.getvalue()),
        photo(1, blurry.getvalue()),
        photo(2, sharp.getvalue()),
    ]
    llm = FakeLLM(
        {
            "brand": None,
            "logo": None,
            "model": None,
            "size_label": None,
            "composition": None,
            "product_code": None,
            "category": None,
            "color": None,
            "condition_estimate": None,
            "defects": [],
            "authenticity_concerns": [],
            "authenticity_positive_signals": [],
            "has_label_photo": True,
            "photo_findings": [
                {
                    "photo": 3,
                    "kind": "label",
                    "verdict": "concern",
                    "certainty": "certain",
                    "detail": "Font diverso",
                    "box": [0.8, 0.1, 0.5, 0.2],
                },
                {
                    "photo": 9,
                    "kind": "logo",
                    "verdict": "consistent",
                    "certainty": "certain",
                    "detail": "foto inesistente",
                    "box": None,
                },
            ],
            "photo_checks": [{"photo": 3, "usable": False, "reason": "tagliata"}],
            "provenance": {
                "stock_or_catalog": 0,
                "screenshots": 0,
                "foreign_watermarks": 1,
                "edited_or_generated": 0,
                "notes": [],
            },
        }
    )
    result = asyncio.run(
        va.ClaudeVisionAnalyzer(llm).analyze(  # type: ignore[arg-type]
            uploaded,
            {"title": "Felpa", "brand_rules": {"key_photos": ["label", "code"], "checks": ["Codice"]}},
        )
    )
    texts = [c["text"] for c in llm.content if c["type"] == "text"]
    assert (
        texts[:3] == ["Foto 1", "Foto 2", "Foto 3"]
        and "Foto chiave per questo brand: label, code" in texts[-1]
    )
    assert len(result.photo_findings) == 1  # the finding on a photo that does not exist is dropped
    f = result.photo_findings[0]
    assert f.photo == 2 and f.box == [0.8, 0.1, 0.2, 0.2]  # 0-based, clamped inside the photo
    checks = {c.photo: c for c in result.photo_checks}
    assert checks[1].usable is False and checks[1].reason == "sfocata"  # measured locally
    assert checks[2].usable is False and checks[2].reason == "tagliata"  # seen by the AI
    assert result.provenance.foreign_watermarks == 1
    assert all(h for h in result.photo_hashes)


def test_labeled_set_false_negatives_and_positives() -> None:
    r = evaluate()
    assert r["counterfeit"] >= 15 and r["authentic"] >= 15
    assert r["false_negatives"] == []  # no counterfeit ever judged "probably authentic"
    assert r["false_positive_rate"] <= 0.07
    assert r["flagged_at_risk"] >= 0.9
