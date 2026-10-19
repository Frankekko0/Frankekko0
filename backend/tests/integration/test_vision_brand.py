"""V04: a brand read in the photos never switches the price pool on its own word.

The listing has no brand field. The photo analysis guesses «Stone Island». The listing's brand (``brand_id``: the key of
the comparables, the counterfeit prior and «brand known») changes only when a label or the OCR confirms the name; every
other reading stays an inferred guess (``identification.brand_guess``) that filters nothing."""

import uuid
from typing import Any

import pytest

from app.ai import service
from app.db.models import Brand, Listing
from app.db.session import session_scope
from app.domain.enums import Certainty
from app.vision.analyzer import ImageAnalyzer, PhotoInput
from app.vision.types import ImageAnalysis, PhotoQuality, VisualFinding
from tests.integration.test_vision_always import listing_with_photos


class FakeAnalyzer(ImageAnalyzer):
    name = "claude_vision"

    def __init__(self, brand: VisualFinding) -> None:
        self.brand = brand

    async def analyze(self, photos: list[PhotoInput], context: dict[str, Any]) -> ImageAnalysis:
        return ImageAnalysis(
            analyzer="claude_vision", brand=self.brand, photo_quality=PhotoQuality(photo_count=len(photos))
        )


async def brand_of(listing_id: uuid.UUID) -> tuple[str | None, dict[str, Any]]:
    async with session_scope() as s:
        li = await s.get(Listing, listing_id)
        assert li is not None
        brand = await s.get(Brand, li.brand_id) if li.brand_id else None
        return (brand.name if brand else None), dict(li.identification or {})


@pytest.mark.parametrize(
    ("certainty", "confidence", "corroborated", "sets_brand"),
    [
        (Certainty.UNVERIFIABLE, 0.2, None, False),
        (Certainty.CERTAIN, 0.9, False, False),
        (Certainty.CERTAIN, 0.9, None, False),
        (Certainty.PROBABLE, 0.5, True, False),
        (Certainty.CERTAIN, 0.9, True, True),
    ],
)
async def test_the_listing_brand_changes_only_when_a_label_or_the_ocr_confirms_the_photo_brand(
    clean_db: None,
    make_listing: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    certainty: Certainty,
    confidence: float,
    corroborated: bool | None,
    sets_brand: bool,
) -> None:
    def no_brand(**kw: Any) -> Any:
        kw.update(
            brand=None, title="Giacca blu taglia L", description="giacca in buone condizioni", category=None
        )
        return make_listing(**kw)

    listing_id = await listing_with_photos(no_brand, monkeypatch, tmp_path)
    assert (await brand_of(listing_id))[0] is None
    finding = VisualFinding(
        value="Stone Island", certainty=certainty, confidence=confidence, corroborated=corroborated
    )
    monkeypatch.setattr(service, "get_image_analyzer", lambda llm: FakeAnalyzer(finding))
    async with session_scope() as s:
        await service.run_vision(s, listing_id)
    name, ident = await brand_of(listing_id)
    if sets_brand:
        assert name == "Stone Island" and ident["brand"]["source"] == "image"
    else:
        assert name is None  # the comparables keep their own brand-less pool
        assert ident["brand"]["value"] is None
        assert ident["brand_guess"]["value"] == "stone-island" and ident["brand_guess"]["source"] == "image"
