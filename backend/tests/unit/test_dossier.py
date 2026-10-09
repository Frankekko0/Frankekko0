"""The dossier: passes P0-P12, coverage, signals, reasons, deltas (tests C, D, T, W of the brief)."""

from decimal import Decimal as D
from typing import Any

from app.analysis.dossier import (
    NOT_APPLICABLE,
    NOT_POSSIBLE,
    PARTIAL,
    PASS_NAMES,
    DossierFacts,
    build_dossier,
    compact_dossier,
    finalize_dossier,
)
from app.domain.enums import Certainty
from app.vision.types import (
    DefectFinding,
    LabelObservation,
    PhotoCheck,
    VisualFinding,
)
from tests.unit.test_listing_analysis import FULL_SET, roles, vf, vision

DECISION = {
    "verdict": "BUY", "label": "BUY", "scores": {"flip": 80, "confidence": 70, "risk": 20, "completeness": 80},
    "vetoes": [], "reasons": ["Prezzo 40% sotto il mercato", "Profitto atteso €15 (ROI 60%)"], "warnings": [],
    "missing_info": [],
}  # fmt: skip


def facts(**over: Any) -> DossierFacts:
    base: dict[str, Any] = dict(
        title="Felpa Ralph Lauren blu M",
        description="Felpa in cotone, ottime condizioni, spedizione tracciata.",
        price=D("20"),
        declared_brand="Ralph Lauren",
        brand_counterfeit_risk=0.12,
        category="sweatshirts",
        parent_category="tops",
        declared_size="M",
        declared_color="blue",
        declared_material="cotone",
        declared_condition="very_good",
        model=None,
        photo_count=6,
        shipping_known=True,
        is_repost=False,
        identification={},
        identification_confidence=70,
        vision=None,
        seller={"known": True, "rating": 4.8, "reviews": 120, "anomalies": [], "score": 85, "level": "high"},
        market={
            "has_value": True,
            "n_used": 14,
            "n_sold": 9,
            "confidence": 78,
            "data_quality": "ok",
            "reason": None,
        },
        fair_market_value=D("33"),
        authenticity={"verdict": "not_verifiable", "label": "Non verificabile", "p_authentic": 0.8},
        economics={"cost_status": "estimated", "unknown_costs": []},
        decision=DECISION,
        completeness={"score": 80},
    )
    base.update(over)
    return DossierFacts(**base)


def by_code(d: dict[str, Any]) -> dict[str, Any]:
    return {p["code"]: p for p in d["passes"]}


def test_the_thirteen_passes_are_always_there_with_a_status_and_a_reason_when_not_done() -> None:
    d = build_dossier(facts())
    assert [p["code"] for p in d["passes"]] == list(PASS_NAMES)
    for p in d["passes"]:
        assert p["name"] == PASS_NAMES[p["code"]] and p["fingerprint"]
        if p["status"] in (PARTIAL, NOT_POSSIBLE):
            assert p["reason"], p["code"]


def test_without_a_photo_analysis_the_photo_passes_say_what_they_need() -> None:
    d = build_dossier(facts())
    p = by_code(d)
    assert p["P4"]["status"] == p["P5"]["status"] == NOT_POSSIBLE
    assert "foto non analizzate" in p["P4"]["reason"] and "analisi delle foto" in p["P5"]["needs"]
    assert p["P2"]["status"] == PARTIAL and p["P6"]["status"] == PARTIAL
    names = {n["code"] for n in d["not_analysable"]}
    assert {"P2", "P4", "P5", "P6"} <= names
    assert d["inspection_coverage"] is None  # never invented
    assert 0 < d["analysis_coverage"] < 100


def test_coverage_rises_when_the_photos_are_analysed_and_is_not_the_other_two_scores() -> None:
    plain = build_dossier(facts())
    v = vision(
        photo_roles=FULL_SET, brand=vf("Ralph Lauren"), size_label=vf("M"), composition=vf("100% cotone"),
        photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.8) for i in range(6)], photo_hashes=list("abcdef"),
        labels=[LabelObservation(type="brand_label", state="present_readable", photo=2, text="Ralph Lauren")],
    )  # fmt: skip
    rich = build_dossier(
        facts(
            vision=v,
            authenticity={
                "verdict": "probably_authentic",
                "label": "Probabilmente autentico",
                "p_authentic": 0.9,
            },
        )
    )
    assert rich["analysis_coverage"] > plain["analysis_coverage"]
    assert rich["inspection_coverage"] == 100 and rich["photo_quality"] is not None
    assert by_code(rich)["P4"]["status"] == by_code(rich)["P5"]["status"] == "done"
    assert {s["name"] for s in rich["signals"]} >= {"brand", "size", "size_label", "composition"}


def test_a_card_reading_has_no_text_pass_and_no_seller() -> None:
    d = build_dossier(facts(description="", seller={"known": False, "score": 50, "level": "new"}))
    p = by_code(d)
    assert p["P1"]["status"] == NOT_POSSIBLE and "card" in p["P1"]["reason"]
    assert p["P9"]["status"] == NOT_POSSIBLE and p["P9"]["needs"]


def test_measures_only_apply_to_clothes_and_shoes() -> None:
    bag = build_dossier(facts(category="bags", parent_category="accessories"))
    assert by_code(bag)["P7"]["status"] == NOT_APPLICABLE
    assert "P7" not in {n["code"] for n in bag["not_analysable"]}
    with_measures = build_dossier(facts(description="Felpa in cotone, larghezza 53 cm, lunghezza 68 cm"))
    assert by_code(with_measures)["P7"]["status"] == "done"


def test_an_unknown_category_is_analysed_generically_and_says_so() -> None:
    d = build_dossier(facts(category="toys", parent_category=None))
    assert d["category_plugin"] == {"key": "generic", "label": "Categoria generica", "covered": False}


# ------------------------------------------------------------------ case C: signed jumper, 2 photos, no label
def test_case_c_missing_information_is_listed_and_authenticity_is_never_asserted() -> None:
    v = vision(
        photo_roles=roles("front", "front"),
        photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.7) for i in range(2)], photo_hashes=["a", "b"],
    )  # fmt: skip
    d = build_dossier(
        facts(
            title="Maglione firmato Burberry", declared_brand="Burberry", brand_counterfeit_risk=0.45, price=D("40"),
            photo_count=2, vision=v, category="knitwear", parent_category="tops",
            authenticity={"verdict": "not_verifiable", "label": "Non verificabile", "p_authentic": 0.5},
            decision={**DECISION, "verdict": "WATCHLIST", "label": "WATCHLIST", "reasons": [],
                      "vetoes": [{"code": "authenticity_unproven", "label": "Marca spesso contraffatta senza prove", "ceiling": "WATCHLIST", "binding": True}]},
        )
    )  # fmt: skip
    assert d["inspection_coverage"] is not None and d["inspection_coverage"] < 40
    assert "Chiedi la foto dell'etichetta interna (marca e taglia)" in d["missing_photos"]
    assert "Chiedi la foto dell'etichetta di lavaggio e composizione" in d["missing_photos"]
    states = {s["type"]: s["state"] for s in d["labels"]["states"]}
    assert states["brand_label"] == "not_visible" and states["care_label"] == "not_visible"
    p6 = by_code(d)["P6"]
    assert "100%" in p6["summary"] and p6["data"]["verdict"] == "not_verifiable"
    assert "autentico" not in d["top_reasons"][0]["text"].lower().replace("autenticit", "")
    assert d["top_reasons"][0] == {"kind": "veto", "text": "Marca spesso contraffatta senza prove"}


# ------------------------------------------------------------------ case D: hidden gem
def test_case_d_a_generic_title_with_a_recognisable_brand_is_a_hidden_gem_to_verify() -> None:
    v = vision(
        photo_roles=FULL_SET,
        brand=VisualFinding(value="Ralph Lauren", certainty=Certainty.PROBABLE, confidence=0.7),
        logo=vf("Polo pony", Certainty.PROBABLE),
    )
    d = build_dossier(facts(title="Felpa blu uomo taglia M", declared_brand=None, vision=v, price=D("12")))
    gem = d["hidden_gem"]
    assert gem["possibly_undervalued"] is True and gem["certainty"] == "inferred"
    assert "Ralph Lauren" in gem["reasons"][0] and "modello esatto" in gem["to_verify"]
    assert any(c["code"] == "brand" and c["severity"] == "low" for c in d["contradictions"])
    assert any(r["kind"] == "lead" for r in d["top_reasons"])
    assert {"name": "possibly_undervalued", "value": True, "provenance": "inferred"}.items() <= next(
        s for s in d["signals"] if s["name"] == "possibly_undervalued"
    ).items()


def test_a_misspelled_brand_is_a_lead_too() -> None:
    d = build_dossier(
        facts(
            declared_brand=None,
            identification={
                "flags": {"misspelled_brand": {"written": "ralph laurem", "brand": "Ralph Lauren"}}
            },
        )
    )
    assert d["hidden_gem"]["possibly_undervalued"] and "ralph laurem" in d["hidden_gem"]["reasons"][0]


def test_a_normal_listing_is_not_a_lead() -> None:
    assert build_dossier(facts())["hidden_gem"] == {
        "possibly_undervalued": False,
        "reasons": [],
        "to_verify": [],
        "certainty": "inferred",
    }


# ------------------------------------------------------------------ case F inside the dossier
def test_case_f_size_discrepancy_reaches_the_dossier_with_its_cost() -> None:
    v = vision(photo_roles=FULL_SET, size_label=vf("M"))
    d = build_dossier(facts(declared_size="L", vision=v, price=D("40")))
    c = next(c for c in d["contradictions"] if c["code"] == "size")
    assert "dichiarata L, etichetta M" in c["detail"] and c["impact_eur"] == 3.2
    assert any(r["kind"] == "contradiction" and "3 € di impatto" in r["text"] for r in d["top_reasons"])


# ------------------------------------------------------------------ signals and reasons
def test_every_signal_has_a_provenance() -> None:
    v = vision(
        photo_roles=FULL_SET,
        defects=[
            DefectFinding(
                kind="hole",
                severity="moderate",
                certainty=Certainty.CERTAIN,
                zone="manica",
                photo=3,
                confidence=0.9,
            )
        ],
    )
    d = build_dossier(facts(vision=v, description="Felpa. Contattami su WhatsApp, larghezza 53 cm"))
    assert d["signals"] and {s["provenance"] for s in d["signals"]} <= {"observed", "declared", "inferred"}
    defect = next(s for s in d["signals"] if s["name"] == "defect:hole")
    assert defect["provenance"] == "observed" and defect["value"] == "manica" and "foto 4" in defect["source"]
    assert (
        next(s for s in d["signals"] if s["name"] == "risk:off_platform_contact")["provenance"] == "observed"
    )
    assert next(s for s in d["signals"] if s["name"] == "measure:chest_flat")["provenance"] == "declared"
    assert next(s for s in d["signals"] if s["name"] == "condition_class")["provenance"] == "inferred"


def test_at_most_five_distinct_reasons_vetoes_first() -> None:
    dec = {**DECISION, "vetoes": [{"code": "x", "label": "Rischio contraffazione elevato", "ceiling": "PASS", "binding": True}],
           "warnings": ["w1", "w2", "w3"], "missing_info": [{"code": "m", "label": "Modello non identificato", "blocks": "strong_buy"}]}  # fmt: skip
    d = build_dossier(facts(decision=dec))
    reasons = d["top_reasons"]
    assert len(reasons) == 5 and reasons[0]["kind"] == "veto"
    assert len({r["text"] for r in reasons}) == 5


# ------------------------------------------------------------------ test W: changes redo only what they touch
INPUTS = {
    "price": "35",
    "currency": "EUR",
    "status": "active",
    "photos": ["k1", "k2", "k3", "k4"],
    "title": "t1",
    "description": "d1",
}


def test_unchanged_facts_change_no_pass() -> None:
    first = build_dossier(facts())
    again = finalize_dossier(build_dossier(facts(), first), first, INPUTS, dict(INPUTS))
    assert again["changed_passes"] == [] and again["delta"] == []


def test_a_new_price_redoes_costs_and_decision_not_text_or_photos() -> None:
    first = build_dossier(facts())
    cheaper = facts(
        price=D("12"),
        decision={
            **DECISION,
            "scores": {"flip": 90, "confidence": 70, "risk": 20, "completeness": 80},
            "verdict": "STRONG_BUY",
        },
    )
    now = finalize_dossier(build_dossier(cheaper, first), first, INPUTS, {**INPUTS, "price": "23"})
    assert set(now["changed_passes"]) <= {"P10", "P11", "P12"}
    assert {"P1", "P2", "P4", "P5", "P6", "P8"}.isdisjoint(now["changed_passes"])
    assert "P12" in now["changed_passes"] and now["delta"] == ["Prezzo da 35 € a 23 €"]


def test_added_photos_redo_the_photo_passes_and_say_so() -> None:
    v1 = vision(
        photo_roles=roles("front", "back"),
        photo_hashes=["a", "b"],
        photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.8) for i in range(2)],
    )
    v2 = vision(
        photo_roles=FULL_SET,
        photo_hashes=list("abcdef"),
        photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.8) for i in range(6)],
    )
    first = build_dossier(facts(vision=v1, photo_count=2))
    now = finalize_dossier(
        build_dossier(facts(vision=v2, photo_count=6), first),
        first,
        INPUTS,
        {**INPUTS, "photos": ["k1", "k2", "k3", "k4", "k5", "k6"]},
    )
    assert {"P2", "P4", "P5"} <= set(now["changed_passes"])
    assert "P1" not in now["changed_passes"] and "P8" not in now["changed_passes"]
    assert now["delta"] == ["2 foto aggiunte"]


def test_a_defect_added_to_the_description_after_the_first_analysis_is_reported() -> None:
    first = build_dossier(facts(description="Felpa in ottime condizioni."))
    now_facts = facts(description="Felpa in ottime condizioni. Piccola macchia sul colletto.")
    now = finalize_dossier(build_dossier(now_facts, first), first, INPUTS, {**INPUTS, "description": "d2"})
    assert (
        "P1" in now["changed_passes"]
        and "P2" not in now["changed_passes"]
        and "P6" not in now["changed_passes"]
    )
    assert now["delta"] == ["Descrizione cambiata: difetto dichiarato dopo la prima analisi «macchia»"]


def test_the_first_analysis_has_every_pass_changed_and_no_delta() -> None:
    first = finalize_dossier(build_dossier(facts()), None, None, INPUTS)
    assert first["changed_passes"] == list(PASS_NAMES) and first["delta"] == []


def test_the_permanent_record_keeps_a_compact_form() -> None:
    import json

    full = finalize_dossier(build_dossier(facts()), None, None, INPUTS)
    small = compact_dossier(full)
    assert len(json.dumps(small)) < len(json.dumps(full)) / 2
    assert small["analysis_coverage"] == full["analysis_coverage"]
    assert [p["code"] for p in small["passes"]] == list(PASS_NAMES)
    assert all(set(p) == {"code", "status", "fingerprint", "reason"} for p in small["passes"])
    assert small["top_reasons"] == full["top_reasons"] and "signals" not in small
