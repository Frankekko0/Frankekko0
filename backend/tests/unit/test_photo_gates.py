"""V02/V05: photo quality and coverage are not decorative, and the price and the dossier tell one condition story.

* a Strong buy needs the photos to show enough of the item (coverage, quality, few unseen parts), and a gallery the
  provider only partly let the model read never opens the photo gates;
* the confidence and the chance of a hidden defect follow what was not seen;
* "no visible defect" is not claimed from a thin gallery;
* the condition used for the price is the dossier's: four moderate defects are «fair» in both.
"""

from typing import Any

import pytest

from app.analysis.categories import CLOTHING
from app.analysis.condition import build_condition_report, class_by_defects, claim_is_safe
from app.analysis.coverage import photo_gates
from app.decision.completeness import CompletenessInput, compute_completeness
from app.decision.engine import DecisionVerdict as V
from app.decision.engine import decide
from app.intelligence.assess import hidden_defect_probability
from app.opportunities.insights import effective_condition
from app.scoring.confidence import ConfidenceInput, compute_confidence
from app.vision.types import DEFECT_KINDS, DefectFinding, ImageAnalysis, PhotoCheck, PhotoQuality, PhotoRole
from tests.unit.test_decision_engine import strong
from tests.unit.test_listing_analysis import FULL_SET, roles, vision


def req(d: Any, code: str) -> bool:
    return next(r.met for r in d.strong_buy_requirements if r.code == code)


# ------------------------------------------------------------------ the Strong-buy requirement
def test_a_strong_buy_needs_the_photos_to_show_enough_of_the_item() -> None:
    """Three photos: one front, one blurry, one packshot. «No defects» and «label seen» say little about a back, a
    collar and an inside nobody photographed."""
    assert decide(strong()).verdict == V.STRONG_BUY  # callers that never evaluate coverage are unchanged
    thin = decide(strong(inspection_coverage=30, photo_quality=45, unobserved_parts=5))
    assert thin.verdict != V.STRONG_BUY and not req(thin, "coverage")
    ok = decide(strong(inspection_coverage=85, photo_quality=80, unobserved_parts=1))
    assert ok.verdict == V.STRONG_BUY and req(ok, "coverage")


@pytest.mark.parametrize(
    ("coverage", "quality", "unobserved", "met"),
    [
        (60, 50, 3, True),
        (59, 80, 0, False),
        (85, 49, 0, False),
        (85, 80, 4, False),
        (85, None, 0, True),
        (None, 10, 9, True),  # not evaluated: only for callers that do not know it
    ],
)
def test_the_coverage_requirement_boundaries(
    coverage: int | None, quality: int | None, unobserved: int, met: bool
) -> None:
    d = decide(strong(inspection_coverage=coverage, photo_quality=quality, unobserved_parts=unobserved))
    assert req(d, "coverage") is met and (d.verdict == V.STRONG_BUY) is met


def test_a_gallery_the_provider_only_partly_let_the_model_read_never_opens_the_photo_gate() -> None:
    d = decide(strong(photos_complete=False))
    assert not req(d, "photos_checked") and d.verdict != V.STRONG_BUY


def test_no_strong_buy_below_the_coverage_threshold_on_any_otherwise_perfect_case() -> None:
    """Count of Strong buys with a coverage under 60 over a grid of otherwise perfect listings: zero (target of V02)."""
    leaks = [
        (cov, q, n)
        for cov in range(0, 60, 5)
        for q in (None, 20, 80, 100)
        for n in (0, 2, 6)
        if decide(strong(inspection_coverage=cov, photo_quality=q, unobserved_parts=n)).verdict
        == V.STRONG_BUY
    ]
    assert leaks == []
    # ... and the same grid at or above the thresholds still reaches it, so the gate is not a blanket block.
    assert (
        decide(strong(inspection_coverage=60, photo_quality=50, unobserved_parts=3)).verdict == V.STRONG_BUY
    )


# ------------------------------------------------------------------ what was not seen lowers the confidence
def conf_input(**kw: Any) -> ConfidenceInput:
    base: dict[str, Any] = dict(
        market_confidence=80,
        identification_confidence=80,
        photo_count=6,
        description_length=200,
        size_known=True,
        condition_known=True,
        seller_known=True,
        demand_observations=30,
    )
    base.update(kw)
    return ConfidenceInput(**base)


def test_confidence_follows_what_the_photos_showed() -> None:
    full = compute_confidence(conf_input()).score
    assert compute_confidence(conf_input(inspection_coverage=100, photo_quality=100)).score == pytest.approx(
        full, abs=1
    )
    thin = compute_confidence(conf_input(inspection_coverage=30, photo_quality=45)).score
    assert thin < full - 8
    assert (
        compute_confidence(conf_input(inspection_coverage=30, photo_quality=45)).components["photo_view"]
        < 0.5
    )
    # No analysis, no coverage: nothing is invented.
    assert "photo_view" not in compute_confidence(conf_input()).components


def test_the_hidden_defect_chance_grows_with_the_parts_nobody_saw() -> None:
    seen = hidden_defect_probability(True, True, 100, 0)
    assert seen == pytest.approx(0.06)  # the baseline of a fully seen item is unchanged
    chain = [hidden_defect_probability(True, True, 100, n) for n in range(0, 7)]
    assert chain == sorted(chain) and chain[4] == chain[6] > chain[0]  # grows, up to four unseen parts
    assert hidden_defect_probability(True, True, 30, 5) > hidden_defect_probability(True, True, 80, 5)
    assert hidden_defect_probability(True, True, 30, 5) > seen + 0.15
    assert hidden_defect_probability(True, True) == seen  # not evaluated: as before
    assert hidden_defect_probability(False, True, 30, 5) == hidden_defect_probability(False, True)


def test_a_partial_or_thin_analysis_is_listed_in_what_is_missing() -> None:
    base: dict[str, Any] = dict(
        brand_known=True,
        model_known=True,
        size_known=True,
        condition_known=True,
        photo_count=6,
        description_length=200,
        shipping_known=True,
        seller_known=True,
        label_photo_seen=True,
        photos_analysed=True,
        identification_confidence=90,
    )
    clean = compute_completeness(CompletenessInput(**base))
    assert not [m for m in clean.missing if m.code in ("photos_partial", "photo_coverage")]
    partial = compute_completeness(CompletenessInput(**base, photos_complete=False, inspection_coverage=40))
    assert {"photos_partial", "photo_coverage"} <= {m.code for m in partial.missing}
    assert partial.score < clean.score
    unreadable = compute_completeness(
        CompletenessInput(**{**base, "label_photo_seen": False}, label_unreadable=True)
    )
    assert any("non leggibile" in m.label for m in unreadable.missing)


# ------------------------------------------------------------------ the gates, from the stored analysis
def test_photo_gates_read_the_validation_of_the_stored_analysis() -> None:
    full = vision(
        photo_roles=FULL_SET,
        validation={"version": 1, "complete": True},
        photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.8) for i in range(6)],
    )
    g = photo_gates(full, CLOTHING, 6)
    assert g.verified and g.complete and g.label_seen is True and (g.inspection_coverage or 0) >= 85
    partial = {**full, "validation": {"version": 1, "complete": False}}
    assert photo_gates(partial, CLOTHING, 6).verified is False and photo_gates(partial, CLOTHING, 6).analysed
    ordered = {**full, "injection_suspected": True}
    assert photo_gates(ordered, CLOTHING, 6).verified is False  # text in the photos that gives orders
    # A local-only analysis and no analysis at all open nothing.
    assert photo_gates({"analyzer": "heuristic"}, CLOTHING, 6).verified is False
    assert photo_gates(None, CLOTHING, 0).label_seen is None
    # A record stored before the validation existed counts as it did, but its coverage is unknown without roles.
    legacy = {"analyzer": "claude_vision", "photo_quality": {"has_label_photo": True}, "defects": []}
    g = photo_gates(legacy, CLOTHING, 6)
    assert g.verified and g.inspection_coverage is None


# ------------------------------------------------------------------ «no visible defect» needs enough photos
def test_no_visible_defect_is_not_claimed_from_a_thin_gallery() -> None:
    thin = vision(
        photo_roles=roles("front", "packshot"), unobserved_parts=["retro", "colletto", "interno", "suola"]
    )
    r = build_condition_report(thin, "very_good", CLOTHING, 2)
    assert r.no_defects_claimable is False and r.condition_class == "very_good"  # never «like new»
    assert any("non è affermabile" in n for n in r.notes)
    rich = vision(
        photo_roles=FULL_SET, photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.8) for i in range(6)]
    )
    r2 = build_condition_report(rich, "very_good", CLOTHING, 6)
    assert r2.no_defects_claimable is True and r2.condition_class == "like_new"
    assert r.confidence < r2.confidence  # and the unseen parts lower the confidence, never the class
    assert claim_is_safe(60, 50, 3) and not claim_is_safe(None, 90, 0) and not claim_is_safe(40, 90, 0)


def test_an_empty_analysis_is_not_like_new() -> None:
    """The audit's “empty model answer gives like_new, confidence 28”: with no roles there is no coverage to claim from."""
    empty = ImageAnalysis(analyzer="claude_vision", photo_quality=PhotoQuality(photo_count=5)).model_dump(
        mode="json"
    )
    r = build_condition_report(empty, "good", CLOTHING, 5)
    assert r.condition_class != "like_new" and r.no_defects_claimable is False


# ------------------------------------------------------------------ one condition model (V05)
def vis_with(*defects: DefectFinding) -> dict[str, Any]:
    return ImageAnalysis(
        analyzer="claude_vision", defects=list(defects), photo_roles=[PhotoRole(photo=0, role="front")]
    ).model_dump(mode="json")


def dfx(kind: str, severity: str, certainty: str = "certain") -> DefectFinding:
    return DefectFinding(kind=kind, severity=severity, certainty=certainty)  # type: ignore[arg-type]


def test_four_moderate_defects_are_fair_in_the_dossier_and_priced_as_satisfactory() -> None:
    four = vis_with(
        dfx("stain", "moderate"),
        dfx("seam", "moderate"),
        dfx("zipper", "moderate"),
        dfx("fading", "moderate"),
    )
    dossier = build_condition_report(four, "very_good", CLOTHING, 5)
    assert dossier.condition_class == "fair"
    assert effective_condition("very_good", four) == "satisfactory"  # was «good»: profit overstated
    assert effective_condition("good", four) == "satisfactory"
    three = vis_with(dfx("stain", "moderate"), dfx("seam", "moderate"), dfx("zipper", "moderate"))
    assert effective_condition("very_good", three) == "satisfactory"


KINDS = [k for k in DEFECT_KINDS if k in CLOTHING.defect_kinds]
RANK = {"new_with_tags": 0, "new_without_tags": 1, "very_good": 2, "good": 3, "satisfactory": 4}
CLASS_TO_PRICE = {
    "like_new": "very_good",
    "very_good": "very_good",
    "good": "good",
    "fair": "satisfactory",
    "poor": "satisfactory",
}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("severity", ["minor", "moderate", "severe"])
@pytest.mark.parametrize("certainty", ["certain", "probable"])
def test_the_price_is_never_set_on_a_better_condition_than_the_dossier_reports(
    kind: str, severity: str, certainty: str
) -> None:
    v = vis_with(dfx(kind, severity, certainty))
    dossier = build_condition_report(v, "very_good", CLOTHING, 5).condition_class
    priced = effective_condition("very_good", v)
    assert RANK[priced] >= RANK[CLASS_TO_PRICE[dossier]], (kind, severity, certainty, dossier, priced)
    assert class_by_defects(v) in ("very_good", "good", "fair", "poor")


def test_a_single_severe_defect_caps_the_class_whatever_the_score() -> None:
    # A faint, probable severe pilling scores very good (87) on points alone; a severe one is never «very good».
    v = vis_with(dfx("pilling", "severe", "probable"))
    assert build_condition_report(v, "very_good", CLOTHING, 5).condition_class == "fair"
    assert effective_condition("very_good", v) == "satisfactory"
    # A potential defect (may be a shadow) lowers nothing, in either model.
    shadow = vis_with(dfx("hole", "severe", "unverifiable"))
    assert effective_condition("very_good", shadow) == "very_good"
    assert build_condition_report(shadow, "very_good", CLOTHING, 5).condition_class != "fair"


def test_the_models_condition_estimate_can_only_lower_the_price_condition() -> None:
    v = vis_with()
    v["condition_estimate"] = {"value": "satisfactory", "certainty": "probable", "confidence": 0.8}
    assert effective_condition("very_good", v) == "satisfactory"
    v["condition_estimate"] = {"value": "new_with_tags", "certainty": "certain", "confidence": 0.9}
    assert effective_condition("good", v) == "good"
    v["condition_estimate"] = {"value": "satisfactory", "certainty": "unverifiable", "confidence": 0.9}
    assert effective_condition("good", v) == "good"
