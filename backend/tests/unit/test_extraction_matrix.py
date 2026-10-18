"""MATRIX-010: a correct spelling of the same brand, a stray capital letter and a garment word are not contradictions.

With photos analysed, an authentic Levi's whose label reads "LEVIS" showed "dichiarato Levi's, ma da etichetta si
legge Levi Strauss: ≈25 EUR di impatto" and the autonomy verifier dropped the purchase proposal; a "Piumino" with a
shell-only label was flagged as a 25% material fraud.
"""

from decimal import Decimal
from typing import Any

import pytest

from app.analysis.condition import ConditionReport
from app.analysis.consistency import (
    ConsistencyInput,
    build_matrix,
    check_brand,
    check_condition,
    check_material,
    check_title_description,
)
from app.analysis.labels import LabelReport, LabelState
from app.analysis.text import analyze_text
from tests.fixtures.extraction import batteries as B


def matrix_input(
    title: str = "x",
    desc: str = "",
    brand: str | None = None,
    vision: Any = None,
    labels: LabelReport | None = None,
    declared_material: str | None = None,
) -> ConsistencyInput:
    return ConsistencyInput(
        title=title, description=desc, price=Decimal("50"), declared_brand=brand, declared_size=None,
        declared_material=declared_material, declared_color=None, declared_condition="very_good",
        declared_category="jeans", declared_parent_category="bottoms", fair_market_value=Decimal("80"),
        brand_counterfeit_risk=0.1, text=analyze_text(title, desc), vision=vision,
        labels=labels or LabelReport([]), condition=ConditionReport("undeterminable", None, 0),
        roles_seen=set(), photo_count=4, analysed=True,
    )  # fmt: skip


def seen(value: str, certainty: str = "certain") -> dict[str, Any]:
    return {"brand": {"value": value, "certainty": certainty}}


@pytest.mark.parametrize(("declared", "label"), B.BRAND_SAME)
def test_the_same_brand_spelt_another_way_is_not_a_contradiction(declared: str, label: str) -> None:
    assert check_brand(matrix_input(brand=declared, vision=seen(label))).status == "ok"


@pytest.mark.parametrize(("declared", "label"), B.BRAND_DIFFERENT)
def test_another_brand_on_the_label_is_still_a_contradiction(declared: str, label: str) -> None:
    c = check_brand(matrix_input(brand=declared, vision=seen(label)))
    assert c.status == "discrepancy" and c.severity == "high"


def test_a_brand_read_by_ocr_alone_is_medium_until_a_second_source_agrees() -> None:
    ocr = LabelReport([LabelState("brand_label", "present_readable", 2, "Adidas", "probable")], brand_text="Adidas")
    one = check_brand(matrix_input(brand="Nike", labels=ocr))
    assert one.status == "discrepancy" and one.severity == "medium"
    both = check_brand(matrix_input(brand="Nike", labels=ocr, vision={"logo": {"value": "Adidas", "certainty": "probable"}}))
    assert both.severity == "high"  # label and logo agree on the other brand


def test_a_brand_the_catalogue_does_not_know_is_not_called_a_fraud() -> None:
    c = check_brand(matrix_input(brand="Nike", vision=seen("Zorbaz", "probable")))
    assert c.status == "discrepancy" and c.severity == "medium" and "non riconosciuta" in c.detail


@pytest.mark.parametrize(("title", "desc"), B.SIZE_TEXT_NOISE)
def test_an_italian_article_is_not_a_size(title: str, desc: str) -> None:
    assert check_title_description(matrix_input(title=title, desc=desc)).status == "ok"


def test_a_size_written_in_the_text_is_still_compared() -> None:
    c = check_title_description(matrix_input(title="Felpa Nike M", desc="Taglia L, la vendo perché piccola"))
    assert c.status == "discrepancy" and "taglia M nel titolo, L nella descrizione" in c.detail


@pytest.mark.parametrize(("title", "desc", "composition"), B.MATERIAL_NOISE)
def test_a_garment_word_or_denim_against_a_cotton_label_is_not_a_material_lie(
    title: str, desc: str, composition: dict[str, int]
) -> None:
    c = check_material(matrix_input(title=title, desc=desc, labels=LabelReport([], composition=composition)))
    assert c.status != "discrepancy", c.detail


def test_a_real_material_contradiction_is_kept_but_capped_while_the_label_is_only_probable() -> None:
    probable = LabelReport(
        [LabelState("composition", "present_readable", 3, "100% polyester", "probable")], composition={"polyester": 100}
    )
    claim = check_material(matrix_input(title="Maglione", desc="Maglione 100% cashmere", labels=probable))
    assert claim.status == "discrepancy" and claim.severity == "medium"
    certain = LabelReport(
        [LabelState("composition", "present_readable", 3, "100% polyester", "certain")], composition={"polyester": 100}
    )
    assert check_material(matrix_input(title="Maglione", desc="Maglione 100% cashmere", labels=certain)).severity == "high"


def test_a_defect_the_description_admits_against_a_better_declared_condition_is_a_discrepancy() -> None:
    """DEFECT-003: "Ottime condizioni" and "piccolo foro vicino al polsino" are two claims of the same seller."""
    c = check_condition(matrix_input(desc="Giacca bellissima, piccolo foro vicino al polsino"))
    assert c.status == "discrepancy" and c.severity == "medium"
    assert "Ottime condizioni" in c.detail and "foro" in c.detail and "non verificata" in c.detail
    assert c.impact_eur == 6.0  # 12% of 50 EUR: the step between "very good" and "good"
    severe = check_condition(matrix_input(desc="Ha un buco sulla manica"))
    assert severe.impact_eur == 15.0  # 30%: down to "satisfactory"
    assert any(x.code == "condition" and x.status == "discrepancy" for x in build_matrix(matrix_input(desc="Ha un buco sulla manica")).checks)


def test_no_discrepancy_when_the_text_admits_nothing_or_the_condition_already_says_so() -> None:
    assert check_condition(matrix_input(desc="Nessun difetto, difetti: nessuno")).status == "not_verifiable"
    assert check_condition(matrix_input(desc="Piccola macchiolina")).status == "not_verifiable"  # minor: no floor
    worn = matrix_input(desc="Ha un buco sulla manica")
    worn.declared_condition = "satisfactory"
    assert check_condition(worn).status == "not_verifiable"
