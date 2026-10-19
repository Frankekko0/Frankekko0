"""V03: authenticity is never certified from photos.

Labels, codes, QR codes, holograms, certificates and logos are copied by counterfeiters, so details that "look
right" on photos can only shrink the list of what to ask the seller and lift the probability by a small, fixed
amount. "Probably authentic" needs a proof from outside the photos that the user entered (declared)."""

import pytest

from app.authenticity.assess import (
    PHOTO_POSITIVE_CAP,
    AuthInput,
    PhotoFinding,
    PhotoQuality,
    assess,
    load_rules,
    photo_evidence,
)
from tests.unit.test_decision_engine import strong

KEY_KINDS = ("label", "care_tag", "code", "logo", "stitching", "zip", "material")
BRANDS = [None, *load_rules()["brands"]]


def convincing(photos: int = 5) -> list[PhotoFinding]:
    """The model says every key detail is consistent and clearly read: what a good fake photographed looks like."""
    return [
        PhotoFinding(i % photos, kind, "consistent", "certain", f"{kind} ok")
        for i, kind in enumerate(KEY_KINDS)
    ]


def inp(slug: str | None, price: float = 60.0, risk: float = 0.35, **kw: object) -> AuthInput:
    return AuthInput(
        brand_slug=slug,
        brand_name=slug,
        brand_counterfeit_risk=risk,
        price=price,
        market_value=120.0,
        photo_count=5,
        findings=convincing(),
        quality=[PhotoQuality(i, True) for i in range(5)],
        photos_analyzed=True,
        seller_reviews=120,
        seller_rating=4.9,
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("slug", BRANDS)
@pytest.mark.parametrize("price", [48.0, 60.0, 84.0, 110.0])
@pytest.mark.parametrize("risk", [0.35, 0.05])  # a brand that is often faked, and one that rarely is
def test_no_brand_and_no_set_of_consistent_findings_reaches_probably_authentic(
    slug: str | None, price: float, risk: float
) -> None:
    a = assess(inp(slug, price, risk))
    assert a.verdict != "probably_authentic", (slug, price, risk, a.p_authentic)


def test_the_stone_island_counterfeit_of_the_audit_stays_uncertain() -> None:
    """A fake at 55% of the market, an established seller, a copied label, code and compass badge: before, the verdict
    was «probably authentic», which lifted the veto for at-risk brands and let a STRONG BUY through."""
    a = assess(inp("stone-island", 66.0))
    assert a.verdict == "uncertain" and a.photos_consistent is True


def test_details_that_look_right_lift_the_probability_by_the_cap_at_most() -> None:
    plain = inp("stone-island", 84.0)
    plain.findings = []
    with_photos = inp("stone-island", 84.0)
    gain = assess(with_photos).p_authentic - assess(plain).p_authentic
    assert 0 < gain <= PHOTO_POSITIVE_CAP + 1e-9


def test_a_concern_is_not_capped_and_a_certified_looking_fake_still_flags_the_negative() -> None:
    a = assess(inp("stone-island", 60.0))
    b = inp("stone-island", 60.0)
    b.findings = [*convincing(), PhotoFinding(2, "label", "concern", "certain", "font diverso")]
    assert assess(b).p_authentic < a.p_authentic - 0.1
    assert assess(b).verdict == "counterfeit_risk"


def test_the_only_way_to_probably_authentic_is_a_proof_the_user_entered() -> None:
    proof = "Certificato Entrupy n. 123 verificato da me"
    a = assess(inp("stone-island", 110.0, external_proof=proof))
    assert a.verdict == "probably_authentic" and a.external_proof == proof
    assert any(e.direction == "+" and "Prova esterna" in e.label for e in a.evidence)
    # ... and it does not outvote evidence against.
    bad = inp("stone-island", 110.0, external_proof=proof, suspicious_terms=["simile"])
    assert assess(bad).verdict != "probably_authentic"


def test_the_positive_signals_the_model_lists_are_never_scored() -> None:
    v = {
        "analyzer": "claude_vision",
        "authenticity_positive_signals": ["Certificato di autenticità incluso, QR code valido"],
        "authenticity_concerns": [],
    }
    assert photo_evidence(v)["findings"] == []
    v2 = {**v, "authenticity_concerns": ["Font strano"]}
    assert [f.verdict for f in photo_evidence(v2)["findings"]] == ["concern"]


def test_a_brand_label_verified_absent_on_an_at_risk_brand_is_a_concern_and_asks_for_the_photo() -> None:
    labels = [{"type": "brand_label", "state": "absence_verifiable", "photo": 1}]
    ev = photo_evidence({"analyzer": "claude_vision", "labels": labels})
    assert ev["absent_labels"] == ["brand_label"]
    risky = assess(inp("moncler", 100.0, absent_labels=ev["absent_labels"]))
    assert any(e.direction == "-" and "nel punto in cui dovrebbero stare" in e.label for e in risky.evidence)
    quiet = inp("ralph-lauren", 100.0, absent_labels=ev["absent_labels"])
    quiet.brand_counterfeit_risk = 0.1  # a brand that is rarely faked: a missing label alone is no red flag
    calm = assess(quiet)
    assert not any("nel punto in cui dovrebbero stare" in e.label for e in calm.evidence)
    # An absence claimed without a photo of the place is not verified (and is not extracted at all).
    assert (
        photo_evidence({"analyzer": "claude_vision", "labels": [{**labels[0], "photo": None}]})[
            "absent_labels"
        ]
        == []
    )


def test_strong_buy_is_impossible_for_an_at_risk_brand_without_the_external_proof() -> None:
    from app.decision.engine import DecisionVerdict as V
    from app.decision.engine import decide

    for slug in (None, "stone-island", "moncler"):
        a = assess(inp(slug, 66.0))
        d = decide(strong(brand_counterfeit_risk=0.45, authenticity_verdict=a.verdict))
        assert d.verdict != V.STRONG_BUY
        assert not next(r for r in d.strong_buy_requirements if r.code == "authenticity").met
    proved = assess(inp("stone-island", 110.0, external_proof="ricevuta verificata"))
    d = decide(strong(brand_counterfeit_risk=0.45, authenticity_verdict=proved.verdict))
    assert d.verdict == V.STRONG_BUY
