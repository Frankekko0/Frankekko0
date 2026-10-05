"""Normalization and product identification."""

import pytest

from app.domain.enums import Certainty, Condition
from app.identification.engine import IdentificationEngine, ListingText
from app.ingestion.normalizer import (
    normalize_color,
    normalize_condition,
    normalize_material,
    normalize_size,
    size_distance,
    title_fingerprint,
)
from app.vision.types import ImageAnalysis, VisualFinding

engine = IdentificationEngine()


@pytest.mark.parametrize(
    "raw,category,expected",
    [
        ("M / 38 / 10", None, "M"),
        ("XXL / 44 / 16", None, "XXL"),
        ("2XL", None, "XXL"),
        ("43", "sneakers", "EU43"),
        ("EU 42.5", "sneakers", "EU42.5"),
        ("UK 8", "sneakers", "EU42"),
        ("W32 | L32", "jeans", "W32"),
        ("32", "jeans", "W32"),
        ("Taglia unica", None, "ONESIZE"),
        ("48", None, "M"),
        ("Medium", None, "M"),
        (None, None, None),
    ],
)
def test_size_normalization(raw: str | None, category: str | None, expected: str | None) -> None:
    assert normalize_size(raw, category) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Nuovo con cartellino", Condition.NEW_WITH_TAGS),
        ("New without tags", Condition.NEW_WITHOUT_TAGS),
        ("Ottime condizioni", Condition.VERY_GOOD),
        ("Très bon état", Condition.VERY_GOOD),
        ("Buone condizioni", Condition.GOOD),
        ("Discrete condizioni", Condition.SATISFACTORY),
        ("very_good", Condition.VERY_GOOD),  # canonical values from feeds / the API
        ("new_with_tags", Condition.NEW_WITH_TAGS),
        ("", Condition.UNKNOWN),
    ],
)
def test_condition_normalization(raw: str, expected: Condition) -> None:
    assert normalize_condition(raw) == expected


def test_vocabularies() -> None:
    assert normalize_color("Blu navy") == "navy"
    assert normalize_color("verde militare") == "khaki"
    assert normalize_material("100% cotone") == "cotton"
    assert size_distance("M", "L") == 1
    assert size_distance("M", "EU42") is None
    assert title_fingerprint("Felpa Ralph Lauren blu M") == title_fingerprint("ralph lauren FELPA blu m")


def test_incomplete_title_is_identified_with_low_confidence() -> None:
    r = engine.identify(ListingText(title="Polo blu uomo", size_field="M / 38 / 10"))
    assert r.category.value == "polo-shirts"
    assert r.brand.value is None and r.brand.certainty == Certainty.UNVERIFIABLE
    assert r.gender.value == "men"
    assert r.confidence < 55
    assert r.product_key is None


def test_description_reveals_brand_and_model() -> None:
    r = engine.identify(
        ListingText(
            title="Polo blu uomo",
            description="Originale Ralph Lauren, modello custom slim fit.",
            size_field="M",
        )
    )
    assert r.brand.value == "ralph-lauren" and r.brand.certainty == Certainty.PROBABLE
    assert r.model.value == "Custom Slim Fit"
    assert r.confidence > 70
    assert r.product_key == "ralph-lauren|polo-shirts|custom slim fit|men"


def test_structured_brand_field_is_certain() -> None:
    r = engine.identify(
        ListingText(
            title="Polo Ralph Lauren felpa con cappuccio grigia tg L",
            brand_field="Ralph Lauren",
            category_field="Felpe con cappuccio",
            size_field="L / 40 / 12",
        )
    )
    assert r.brand.certainty == Certainty.CERTAIN
    assert r.category.value == "hoodies"  # "polo" inside the brand alias must not mean polo shirt
    assert r.line.value == "Polo Ralph Lauren"


def test_distinctive_model_infers_brand_and_suspicious_terms_flag_authenticity() -> None:
    r = engine.identify(ListingText(title="Piumino Nuptse nero", description="Qualità AAA, simile originale"))
    assert r.brand.value == "the-north-face"
    assert r.category.value == "puffer-jackets"
    assert r.authenticity.value == "suspicious"
    assert r.suspicious_terms


def test_football_shirt_team_and_season() -> None:
    r = engine.identify(ListingText(title="Maglia Inter 2006/07 home Nike tg L", brand_field="Nike"))
    assert r.category.value == "football-shirts"
    assert r.model.value == "Inter"
    assert r.season.value == "2006/07"
    assert r.is_vintage


def test_product_code_and_defects() -> None:
    r = engine.identify(
        ListingText(
            title="Nike Air Force 1 bianca 43",
            description="Codice CW2288-111. Piccola macchia sulla punta.",
            brand_field="Nike",
            category_field="Sneakers",
            size_field="43",
        )
    )
    assert r.product_code.value == "CW2288-111"
    assert r.model.value == "Air Force 1"
    assert r.size.value == "EU43"
    assert "macchie" in r.defect_terms


def test_vintage_decade_is_not_kids_age() -> None:
    r = engine.identify(ListingText(title="Felpa vintage anni 90 Champion", brand_field="Champion"))
    assert r.is_vintage
    assert r.gender.value != "kids"


def test_vision_evidence_is_used_but_never_asserts_authenticity() -> None:
    vision = ImageAnalysis(
        analyzer="test",
        brand=VisualFinding(value="Ralph Lauren", certainty=Certainty.CERTAIN, confidence=0.9),
        authenticity_positive_signals=["etichetta coerente"],
    )
    r = engine.identify(ListingText(title="Polo blu uomo"), vision)
    assert r.brand.value == "ralph-lauren" and r.brand.source == "image"
    assert r.authenticity.value == "no_red_flags"
    assert r.authenticity.certainty == Certainty.UNVERIFIABLE
