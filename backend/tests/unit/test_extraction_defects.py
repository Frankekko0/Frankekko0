"""DEFECT-003 + PX-08: what the seller's own words admit is wrong.

One negation-aware extractor feeds the risk score, the dossier, the AI context and the price: a declared hole
lowers the condition the price follows and shows a repair as an unknown cost, instead of pricing the item as
"ottime condizioni". "No defects" is never read as a defect.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.analysis.text import analyze_text
from app.decision.engine import DecisionVerdict, strong_buy_requirements
from app.identification.engine import IdentificationEngine, ListingText
from app.opportunities.engine import EconomicTargets, SubjectContext, run_analysis
from app.pricing.comparables import ItemProfile
from app.profit.calculator import CostProfile
from app.scoring.seller import SellerProfile

NOW = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
ENGINE = IdentificationEngine()


def extract(text: str):
    from app.analysis.defects import extract_defects

    return extract_defects(text)


# ---------------------------------------------------------------------------- the extractor
@pytest.mark.parametrize(
    ("text", "severity"),
    [
        ("Ha un buco sulla manica", "severe"),
        ("piccolo foro vicino al polsino", "moderate"),
        ("Cerniera rotta", "severe"),
        ("Zip bloccata", "severe"),
        ("Cucitura aperta sul fianco", "moderate"),
        ("Cavallo rammendato e fondo scucito / orlo aperto, vedere foto.", "moderate"),
        ("Suola consumata", "moderate"),
        ("Scollamento della suola", "severe"),
        ("Piccola macchia sul colletto", "minor"),
        ("Presenta pilling", "minor"),
        ("Zipper broken", "severe"),
        ("Hole in the pocket", "severe"),
        ("Missing button", "moderate"),
        ("Fermeture cassée", "severe"),
        ("Trou au coude", "severe"),
        ("Loch im Ärmel", "severe"),
        ("Reißverschluss defekt", "severe"),
        ("Agujero en el bolsillo", "severe"),
        ("senza difetti tranne una macchia", "moderate"),
        ("no stains, no holes, hole in the pocket lining", "severe"),
    ],
)
def test_a_declared_defect_is_found_with_its_severity(text: str, severity: str) -> None:
    assert extract(text).worst == severity


@pytest.mark.parametrize(
    "text",
    [
        "Nessun difetto",
        "Senza difetti",
        "Non ha macchie né buchi",
        "Nessuna macchia, nessuno strappo",
        "senza macchie, buchi o strappi",
        "No defects",
        "No stains or holes",
        "Sans défauts",
        "Pas de trous ni de taches",
        "Sin defectos",
        "Keine Mängel",
        "Non presenta difetti",
        "Non è rotto nulla",
        "Cerniera funzionante, nessun filo tirato",
        "Come nuovo, senza segni di usura",
        "No rips, no tears, no fading",
        "Zip funziona perfettamente",
    ],
)
def test_a_reassurance_is_not_a_defect(text: str) -> None:
    report = extract(text)
    assert report.hits == []
    # ...and the same sentence through the identification engine and the text analysis.
    assert ENGINE.identify(ListingText(title="Giacca", description=text)).defect_terms == []
    assert analyze_text("Giacca", text).declared_defects == []


def test_a_hashtag_is_not_a_defect_and_a_hit_keeps_the_phrase_it_was_read_from() -> None:
    assert extract("Bella giacca #FadedDenim #WornDenim #Distressed").hits == []
    report = extract("Vendo perché ha un buco sulla manica sinistra")
    hit = report.hits[0]
    assert (hit.kind, hit.severity, hit.source) == ("hole", "severe", "declared")
    assert "buco" in hit.phrase and report.as_dict()["hits"][0]["provenance"] == "declared"
    assert report.needs_restoration


def test_the_real_levis_page_declares_a_repaired_crotch_and_an_open_hem() -> None:
    """The description of the real fixture (tests/fixtures/vinted/real): the identification used to see nothing."""
    text = (
        "Levi’s 501 vintage Made in France in denim blu chiaro con naturali segni di slavatura.\n\n"
        "Cavallo rammendato e fondo scucito / orlo aperto, vedere foto.\n\n#FadedDenim #WornDenim"
    )
    r = ENGINE.identify(ListingText(title="Vintage Levi’s 501", description=text, brand_field="Levi's"))
    assert {"rammendi", "cuciture aperte"} <= set(r.defect_terms)
    assert {h["kind"] for h in r.defect_hits} >= {"repair", "seam", "fading"}
    t = analyze_text("Vintage Levi’s 501", text)
    assert t.defect_severity == "moderate" and t.defect_hits and "faded" not in t.declared_defects


# ---------------------------------------------------------------------------- price and verdict (PX-08)
def comparables() -> list[ItemProfile]:
    prices = [26, 27, 28, 28, 29, 28, 30, 31, 27, 28, 29, 28]
    return [
        ItemProfile(
            id=uuid.uuid4(),
            title="Polo Ralph Lauren blu M",
            price=D(p),
            brand="ralph-lauren",
            category="polo-shirts",
            parent_category="tops",
            model=None,
            condition="very_good",
            size="M",
            status="sold",
            published_at=NOW - timedelta(days=i + 6),
            sold_at=NOW - timedelta(days=i + 2),
            last_seen_at=NOW - timedelta(days=i + 2),
        )
        for i, p in enumerate(prices)
    ]


def analysed(description: str, *, condition: str = "very_good"):
    profile = ItemProfile(
        id=None,
        title="Polo Ralph Lauren blu M",
        price=D("11"),
        brand="ralph-lauren",
        category="polo-shirts",
        parent_category="tops",
        model=None,
        condition=condition,
        size="M",
    )
    subject = SubjectContext(
        profile=profile,
        description_length=len(description),
        photo_count=6,
        favourite_count=3,
        listing_age_hours=2,
        shipping_fee=D("2.99"),
        brand_name="Ralph Lauren",
        brand_counterfeit_risk=0.12,
        category_baseline_days=10,
        identification={},
        identification_confidence=85,
        seller=SellerProfile(D("4.9"), 80),
        description=description,
    )
    return run_analysis(subject, comparables(), NOW, CostProfile(), EconomicTargets())


CLEAN = "Polo Ralph Lauren taglia M, ottime condizioni, nessun difetto."
DEFECTIVE = "Polo Ralph Lauren taglia M, piccolo buco sulla manica e macchia sul colletto, leggermente scolorita, da lavare."


def test_a_declared_hole_and_stain_lower_the_price_and_cannot_be_strong_buy() -> None:
    clean, defective = analysed(CLEAN), analysed(DEFECTIVE)
    assert clean.market.fair_market_value and defective.market.fair_market_value
    # Priced as "good" (0.88 of very good), not as the "ottime condizioni" the field says.
    assert defective.market.fair_market_value <= clean.market.fair_market_value * D("0.88") + D("0.5")
    assert defective.decision is not None and defective.decision.verdict != DecisionVerdict.STRONG_BUY
    assert defective.insights["condition"]["effective"] == "good"
    assert "descrizione ammette" in defective.insights["condition"]["differences"][0]
    # The repair is a cost nobody quantified: said as unknown (profit is gross of it), no invented figure.
    assert "Ripristino" in defective.economics["unknown_costs"]
    assert defective.economics["declared_defects"]["source"] == "declared"
    assert "Ripristino" not in clean.economics["unknown_costs"]


def test_a_reassurance_changes_nothing_about_the_price() -> None:
    plain = analysed("Polo Ralph Lauren taglia M, ottime condizioni.")
    assured = analysed(CLEAN)
    assert plain.market.fair_market_value == assured.market.fair_market_value
    assert "declared_defects" not in assured.economics


def test_a_declared_defect_is_a_warning_and_a_strong_buy_requirement() -> None:
    from app.decision.engine import decide
    from tests.unit.test_decision_engine import strong

    defective = analysed(DEFECTIVE)
    assert defective.decision is not None
    assert any("difetti" in w for w in defective.decision.warnings)
    assert decide(strong()).verdict == DecisionVerdict.STRONG_BUY
    severe = decide(strong(declared_defect_severity="severe", declared_defects=("buco sulla manica",)))
    assert severe.verdict != DecisionVerdict.STRONG_BUY
    assert any("no_declared_defects" == r.code and not r.met for r in strong_buy_requirements(strong(declared_defect_severity="severe")))
    # A slight pilling is a warning, not a bar.
    assert decide(strong(declared_defect_severity="minor")).verdict == DecisionVerdict.STRONG_BUY
