"""Conditions, labels, coverage and the consistency matrix: cases B, C, D, F and U of the brief."""

from decimal import Decimal as D
from typing import Any

import pytest

from app.analysis.categories import BAGS_ACCESSORIES, CLOTHING, FOOTWEAR, GENERIC, plugin_for
from app.analysis.condition import build_condition_report
from app.analysis.consistency import ConsistencyInput, build_matrix
from app.analysis.coverage import photo_coverage, photo_quality_score, roles_seen
from app.analysis.labels import build_label_report
from app.analysis.text import analyze_text
from app.domain.enums import Certainty
from app.vision.types import (
    DefectFinding,
    ImageAnalysis,
    LabelObservation,
    PhotoCheck,
    PhotoQuality,
    PhotoRole,
    VisualFinding,
)


def vf(value: str, certainty: Certainty = Certainty.CERTAIN) -> VisualFinding:
    return VisualFinding(value=value, certainty=certainty, confidence=0.9)


def vision(**kw: Any) -> dict[str, Any]:
    """What a real photo analysis stores (the shape of ``ImageAnalysis``)."""
    base: dict[str, Any] = {
        "analyzer": "claude_vision",
        "photo_quality": PhotoQuality(photo_count=6, analyzed_count=6, has_label_photo=True),
    }
    base.update(kw)
    return ImageAnalysis(**base).model_dump(mode="json")


def roles(*names: str) -> list[PhotoRole]:
    return [PhotoRole(photo=i, role=n) for i, n in enumerate(names)]  # type: ignore[arg-type]


FULL_SET = roles("front", "back", "label", "care_label", "detail", "worn")


# ------------------------------------------------------------------ category plugins
def test_plugins_by_category() -> None:
    assert plugin_for("sweatshirts", "tops") is CLOTHING
    assert plugin_for("jackets", "outerwear") is CLOTHING and plugin_for("jeans", "bottoms") is CLOTHING
    assert plugin_for("sneakers", "footwear") is FOOTWEAR
    assert (
        plugin_for("bags", "accessories") is BAGS_ACCESSORIES
        and plugin_for("belts", "accessories") is BAGS_ACCESSORIES
    )
    assert plugin_for("toys", None) is GENERIC and plugin_for(None) is GENERIC
    assert not GENERIC.covered and GENERIC.confidence_factor < 1
    for p in (CLOTHING, FOOTWEAR, BAGS_ACCESSORIES, GENERIC):
        assert abs(sum(p.role_weights().values()) - 1) < 1e-9


# ------------------------------------------------------------------ coverage
def test_photo_quality_and_inspection_coverage_are_independent() -> None:
    sharp_but_incomplete = vision(
        photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.9) for i in range(3)],
        photo_hashes=["a", "b", "c"],
        photo_roles=roles("front", "front", "front"),
    )
    blurry_but_complete = vision(
        photo_checks=[PhotoCheck(photo=i, usable=False, reason="sfocata", sharpness=0.05) for i in range(5)],
        photo_hashes=list("abcde"),
        photo_roles=roles("front", "back", "label", "care_label", "detail"),
    )
    a = photo_coverage(sharp_but_incomplete, CLOTHING, 3)
    b = photo_coverage(blurry_but_complete, CLOTHING, 5)
    assert a.photo_quality is not None and b.photo_quality is not None
    assert a.photo_quality > 85 and a.inspection_coverage == 25  # only the front
    assert b.photo_quality < 35 and b.inspection_coverage == 100
    assert a.roles_missing == ("back", "label", "care_label", "detail")
    assert "Chiedi la foto dell'etichetta interna (marca e taglia)" in a.checklist
    assert b.checklist == ()


def test_without_a_photo_analysis_nothing_is_invented() -> None:
    cov = photo_coverage({"analyzer": "heuristic", "photo_checks": []}, CLOTHING, 4)
    assert cov.analysed is False and cov.inspection_coverage is None and cov.roles_missing == ()
    assert "non valutabili" in cov.checklist[0]
    assert photo_coverage(None, CLOTHING, 0).checklist == ("nessuna foto",)


def test_duplicates_and_screenshots_lower_the_photo_quality() -> None:
    clean = vision(
        photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.6) for i in range(4)],
        photo_hashes=list("abcd"),
    )
    dup = vision(
        photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.6, screenshot=i == 0) for i in range(4)],
        photo_hashes=list("aaab"),
    )
    assert photo_quality_score(clean) > photo_quality_score(dup)


def test_a_readable_label_counts_as_a_label_photo() -> None:
    v = vision(labels=[LabelObservation(type="brand_label", state="present_readable", photo=2, text="Nike")])
    assert "label" in roles_seen(v)


# ------------------------------------------------------------------ case B: a hole the seller did not mention
def test_case_b_a_hole_on_the_sleeve_contradicts_ottime_condizioni() -> None:
    v = vision(
        photo_roles=FULL_SET,
        defects=[
            DefectFinding(
                kind="hole",
                severity="moderate",
                certainty=Certainty.CERTAIN,
                zone="manica sinistra",
                photo=3,
                box=[0.4, 0.5, 0.1, 0.1],
                confidence=0.9,
            )
        ],
    )
    r = build_condition_report(v, "very_good", CLOTHING, 6)
    assert r.condition_class in ("good", "fair")
    assert r.score is not None and r.score < 80
    d = r.visible_defects[0]
    assert (d["kind"], d["zone"], d["photo"], d["box"]) == (
        "hole",
        "manica sinistra",
        3,
        [0.4, 0.5, 0.1, 0.1],
    )
    c = r.contradictions[0]
    assert c["code"] == "declared_better_than_seen" and c["severity"] in ("medium", "high")
    assert "manica sinistra" in c["detail"] and c["photo"] == 3 and c["box"] == [0.4, 0.5, 0.1, 0.1]
    assert r.price_impact_pct and 10 <= r.price_impact_pct <= 60  # the price follows the real condition


def test_no_visible_defect_is_not_no_defect() -> None:
    r = build_condition_report(vision(photo_roles=roles("front")), "very_good", CLOTHING, 1)
    assert r.visible_defects == [] and any("non significa" in n for n in r.notes)
    assert r.confidence < 60  # a single photo of the front proves little
    full = build_condition_report(
        vision(
            photo_roles=FULL_SET,
            photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.8) for i in range(6)],
        ),
        "very_good",
        CLOTHING,
        6,
    )
    assert full.confidence > r.confidence


def test_a_flaw_that_may_be_a_shadow_is_potential_never_scored() -> None:
    v = vision(
        photo_roles=FULL_SET,
        defects=[
            DefectFinding(kind="stain", severity="moderate", certainty=Certainty.UNVERIFIABLE, zone="petto")
        ],
    )
    r = build_condition_report(v, "very_good", CLOTHING, 6)
    assert r.visible_defects == [] and len(r.potential_defects) == 1
    assert r.score is not None and r.score >= 90 and r.contradictions == []
    assert any("ombre o pieghe" in n for n in r.notes)


def test_new_with_tags_needs_the_tag_in_a_photo() -> None:
    clean = vision(photo_roles=FULL_SET)
    no_tag = build_condition_report(clean, "new_with_tags", CLOTHING, 6)
    assert no_tag.condition_class == "new_without_tags_unverifiable" or no_tag.condition_class == "like_new"
    assert any(c["code"] == "declared_tags_not_shown" for c in no_tag.contradictions)
    with_tag = vision(
        photo_roles=FULL_SET,
        labels=[LabelObservation(type="paper_tag", state="present_readable", photo=4, text="NWT")],
    )
    assert build_condition_report(with_tag, "new_with_tags", CLOTHING, 6).condition_class == "new_with_tags"
    assert (
        build_condition_report(clean, "new_without_tags", CLOTHING, 6).condition_class
        == "new_without_tags_unverifiable"
    )


def test_without_a_photo_analysis_the_condition_is_undeterminable() -> None:
    r = build_condition_report({"analyzer": "heuristic"}, "very_good", CLOTHING, 4)
    assert r.condition_class == "undeterminable" and r.score is None and r.confidence == 0
    assert build_condition_report(None, "good", CLOTHING, 0).condition_class == "undeterminable"


def test_a_generic_category_lowers_the_confidence() -> None:
    v = vision(photo_roles=roles("front", "back", "label", "care_label", "detail"))  # complete for both
    generic, clothing = (
        build_condition_report(v, "good", GENERIC, 5),
        build_condition_report(v, "good", CLOTHING, 5),
    )
    assert generic.confidence < clothing.confidence
    assert any("generica" in n for n in generic.notes) and not any("generica" in n for n in clothing.notes)


# ------------------------------------------------------------------ labels: case C and F
def test_labels_not_shown_are_not_absent() -> None:
    v = vision(photo_roles=roles("front", "front"))
    rep = build_label_report(v, CLOTHING)
    states = {s.type: s.state for s in rep.states}
    assert states["brand_label"] == "not_visible" and states["care_label"] == "not_visible"
    assert "absence_verifiable" not in states.values()
    assert "etichetta del marchio" in rep.unread


def test_an_unanalysed_listing_has_insufficient_information_everywhere() -> None:
    rep = build_label_report(None, CLOTHING)
    assert {s.state for s in rep.states} == {"insufficient_information"}


def test_labels_are_read_into_facts() -> None:
    v = vision(
        size_label=vf("M"),
        composition=vf("67% cotone 33% poliestere"),
        product_code=vf("RN 12345"),
        labels=[
            LabelObservation(type="brand_label", state="present_readable", photo=2, text="Polo Ralph Lauren"),
            LabelObservation(type="care_label", state="present_unreadable", photo=3),
        ],
    )
    rep = build_label_report(v, CLOTHING)
    assert rep.size_text == "M" and rep.composition == {"cotton": 67, "polyester": 33}
    assert rep.codes["sku"] == "RN 12345"
    assert (
        rep.state_of("size_label") == "present_readable"
        and rep.state_of("care_label") == "present_unreadable"
    )
    assert "etichetta di lavaggio" in rep.unread


def test_a_stated_absence_beats_not_visible() -> None:
    v = vision(
        labels=[
            LabelObservation(type="paper_tag", state="absence_verifiable", photo=1),
            LabelObservation(type="paper_tag", state="not_visible"),
        ]
    )
    assert build_label_report(v, CLOTHING).state_of("paper_tag") == "absence_verifiable"


# ------------------------------------------------------------------ the matrix
def matrix_for(**over: Any):
    base: dict[str, Any] = dict(
        title="Felpa Ralph Lauren blu M",
        description="Felpa in cotone, ottime condizioni.",
        price=D("40"),
        declared_brand="Ralph Lauren",
        declared_size="M",
        declared_material="cotone",
        declared_color="blue",
        declared_condition="very_good",
        declared_category="sweatshirts",
        declared_parent_category="tops",
        fair_market_value=D("45"),
        brand_counterfeit_risk=0.12,
        vision=None,
        roles_seen=set(),
        photo_count=6,
        analysed=False,
    )
    base.update(over)
    v = base["vision"]
    base.setdefault("text", analyze_text(base["title"], base["description"]))
    base.setdefault("labels", build_label_report(v, CLOTHING))
    base.setdefault(
        "condition", build_condition_report(v, base["declared_condition"], CLOTHING, base["photo_count"])
    )
    return build_matrix(ConsistencyInput(**base))


def by_code(m: Any) -> dict[str, Any]:
    return {c.code: c for c in m.checks}


def test_a_consistent_listing_has_no_discrepancy() -> None:
    v = vision(
        photo_roles=FULL_SET, brand=vf("Ralph Lauren"), size_label=vf("M"), composition=vf("100% cotone"),
        color=vf("blue"), category=vf("sweatshirts"),
        labels=[LabelObservation(type="brand_label", state="present_readable", text="Ralph Lauren")],
    )  # fmt: skip
    m = matrix_for(vision=v, analysed=True)
    assert m.discrepancies == []
    c = by_code(m)
    assert c["brand"].status == c["size"].status == c["material"].status == c["color"].status == "ok"


def test_the_checks_that_cannot_be_made_say_why_and_are_never_ok() -> None:
    m = matrix_for()  # no photo analysis at all
    c = by_code(m)
    for code in (
        "title_photos",
        "brand",
        "size",
        "material",
        "color",
        "condition",
        "category",
        "season",
        "closet",
    ):
        assert c[code].status == "not_verifiable" and c[code].detail, code
    assert len(m.checks) == 13


def test_case_f_size_l_declared_label_says_m() -> None:
    v = vision(photo_roles=FULL_SET, size_label=vf("M"))
    m = matrix_for(vision=v, analysed=True, declared_size="L", title="Felpa Ralph Lauren blu L")
    c = by_code(m)["size"]
    assert c.status == "discrepancy" and "dichiarata L, etichetta M" in c.detail
    assert c.severity == "medium" and c.impact_eur == pytest.approx(3.2)  # one size apart: 8% of 40 EUR


def test_two_sizes_apart_is_worse() -> None:
    v = vision(photo_roles=FULL_SET, size_label=vf("S"))
    c = by_code(matrix_for(vision=v, analysed=True, declared_size="L"))["size"]
    assert c.severity == "high" and c.impact_eur == pytest.approx(6.0)


def test_case_u_every_planted_contradiction_is_found_with_severity_and_impact() -> None:
    """Title says Ralph Lauren L, 100% cashmere, navy; the photos say otherwise on every point."""
    v = vision(
        photo_roles=FULL_SET,
        brand=vf("Tommy Hilfiger"),
        size_label=vf("M"),
        composition=vf("60% cotone 40% poliestere"),
        color=vf("rosso"),
        category=vf("t-shirts"),
        defects=[
            DefectFinding(
                kind="stain",
                severity="severe",
                certainty=Certainty.CERTAIN,
                zone="petto",
                photo=1,
                box=[0.3, 0.3, 0.2, 0.2],
            )
        ],
        labels=[
            LabelObservation(type="brand_label", state="present_readable", photo=2, text="Tommy Hilfiger")
        ],
    )
    m = matrix_for(
        vision=v,
        analysed=True,
        title="Maglione Ralph Lauren cashmere L blu navy",
        description="Maglione 100% cashmere, taglia L, colore navy, ottime condizioni. Larghezza 62 cm.",
        declared_size="L",
        declared_material="cashmere",
        declared_color="navy",
        declared_category="knitwear",
        declared_parent_category="tops",
        declared_condition="very_good",
        price=D("100"),
    )
    c = by_code(m)
    found = {code for code, chk in c.items() if chk.status == "discrepancy"}
    assert {"brand", "size", "material", "color", "condition", "category"} <= found
    assert c["brand"].severity == "high" and c["brand"].impact_eur == pytest.approx(50.0)
    assert c["material"].severity == "high" and c["material"].impact_eur == pytest.approx(
        25.0
    )  # a value driver
    assert c["size"].status == "discrepancy" and c["condition"].severity in ("medium", "high")
    assert c["condition"].impact_eur and c["condition"].impact_eur > 0
    ordered = [x.code for x in m.discrepancies]
    assert ordered[0] == "brand"  # most severe, biggest cost first
    assert m.total_impact_eur == pytest.approx(sum(x.impact_eur or 0 for x in m.discrepancies))
    assert m.as_dict()["impact_basis"].startswith("stima")


def test_measures_that_do_not_fit_the_size_are_flagged() -> None:
    m = matrix_for(description="Felpa taglia M, larghezza 62 cm", declared_size="M")
    c = by_code(m)["size"]
    assert c.status == "discrepancy" and c.severity == "high" and "tabella indicativa" in c.detail
    ok = matrix_for(description="Felpa taglia M, larghezza 53 cm", declared_size="M")
    assert by_code(ok)["size"].status == "ok"


def test_a_circumference_is_halved_before_comparing() -> None:
    assert (
        by_code(matrix_for(description="Felpa taglia M, torace 106 cm", declared_size="M"))["size"].status
        == "ok"
    )


def test_title_and_description_that_disagree() -> None:
    m = matrix_for(title="Felpa blu M", description="Felpa nera taglia L, cotone")
    c = by_code(m)["title_description"]
    assert c.status == "discrepancy" and "taglia M nel titolo, L nella descrizione" in c.detail
    assert by_code(matrix_for(description=""))["title_description"].status == "not_verifiable"


def test_price_far_below_the_market_on_a_risky_brand() -> None:
    c = by_code(matrix_for(price=D("12"), fair_market_value=D("60"), brand_counterfeit_risk=0.5))["price"]
    assert c.status == "discrepancy" and c.severity == "high" and c.impact_eur == 12.0
    assert by_code(matrix_for(price=D("40"), fair_market_value=D("45")))["price"].status == "ok"


def test_reused_photos_are_a_discrepancy_with_the_sellers_closet() -> None:
    assert by_code(matrix_for(photos_reused=True))["closet"].severity == "high"


def test_tags_box_and_receipt_claims_not_shown_are_not_verifiable() -> None:
    v = vision(photo_roles=FULL_SET)
    m = matrix_for(
        vision=v,
        analysed=True,
        description="Con scatola e scontrino, cartellino attaccato",
        declared_condition="new_with_tags",
    )
    c = by_code(m)["claims"]
    assert c.status == "not_verifiable" and "scatola" in c.detail and "cartellino" in c.detail


def test_case_d_a_generic_title_with_a_recognisable_brand_in_the_photos_is_a_lead() -> None:
    v = vision(
        photo_roles=FULL_SET,
        brand=vf("Ralph Lauren", Certainty.PROBABLE),
        logo=vf("Polo pony", Certainty.PROBABLE),
    )
    m = matrix_for(vision=v, analysed=True, title="Felpa blu uomo taglia M", declared_brand=None)
    c = by_code(m)["brand"]
    assert c.status == "discrepancy" and c.severity == "low" and "possibile occasione nascosta" in c.detail
    assert c.impact_eur is None  # a lead to check, not a cost
