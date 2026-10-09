"""The decision through the whole chain: pipeline -> database -> API -> permanent analysis record."""

from typing import Any

import httpx
from sqlalchemy import select

from app.db.models import Analysis, Listing, Opportunity
from app.db.session import session_scope
from app.decision.engine import DecisionVerdict
from app.ingestion.service import IngestionService
from app.opportunities.pipeline import AnalysisPipeline
from tests.api.test_api import API, NOW, build_market, seed_deal
from tests.api.test_case_g_rare_item import rare_card

LEGACY = {
    "STRONG_BUY": "BUY",
    "BUY": "BUY",
    "NEGOTIATE": "CONSIDER",
    "WATCHLIST": "CONSIDER",
    "PASS": "SKIP",
    "INSUFFICIENT_EVIDENCE": "SKIP",
}


async def analyse(make_listing: Any, *visions: dict | None, price: float = 12) -> list[Opportunity]:
    """Seed one market, ingest a listing per entry of ``visions`` (each optionally with a photo
    analysis) and analyse them all against that same market."""
    async with session_scope() as s:
        await build_market(s, make_listing)
        res = await IngestionService(s, "test").ingest(
            [make_listing(price=price, published_days_ago=0.05) for _ in visions or [None]], now=NOW
        )
        for listing_id, vision in zip(res.new_ids, visions or [None], strict=True):
            if vision is not None:
                listing = await s.get(Listing, listing_id)
                assert listing is not None
                listing.identification = {**(listing.identification or {}), "vision": vision}
        await s.flush()
        outcomes = await AnalysisPipeline(s).analyze_many(res.new_ids, now=NOW)
        opps = []
        for o in outcomes:
            opp = await s.get(Opportunity, o.opportunity_id)
            assert opp is not None
            s.expunge(opp)
            opps.append(opp)
        return opps


async def test_card_and_detail_carry_the_decision(auth_client: httpx.AsyncClient, make_listing) -> None:
    opp_id = await seed_deal(make_listing)
    card = (await auth_client.get(f"{API}/opportunities")).json()["items"][0]
    assert card["decision_verdict"] in {v.value for v in DecisionVerdict}
    assert 0 <= card["data_completeness_score"] <= 100
    assert card["verdict"] == LEGACY[card["decision_verdict"]]  # the old vocabulary follows the decision

    detail = (await auth_client.get(f"{API}/opportunities/{opp_id}")).json()
    d = detail["decision"]
    assert d["verdict"] == card["decision_verdict"] and d["rules_version"] == "decision-v1"
    assert set(d["scores"]) == {"flip", "confidence", "risk", "completeness"}
    assert (
        d["scores"]["flip"] == card["flip_score"]
        and d["scores"]["completeness"] == card["data_completeness_score"]
    )
    assert detail["ai_analysis"]["verdict"] == card["verdict"]  # narrative and decision do not contradict
    assert d["strong_buy_requirements"] and d["completeness"]["missing"] is not None
    if d["verdict"] == "STRONG_BUY":
        assert all(r["met"] for r in d["strong_buy_requirements"])
    else:
        assert d["verdict"] != "STRONG_BUY"


async def test_without_photo_analysis_the_listing_never_reaches_strong_buy(
    clean_db: None, make_listing
) -> None:
    """No photo analysis ran (no model key in tests): the label is unverified, so no STRONG BUY."""
    (opp,) = await analyse(make_listing)
    assert opp.decision is not None
    assert opp.decision_verdict != "STRONG_BUY"
    unmet = {r["code"] for r in opp.decision["strong_buy_requirements"] if not r["met"]}
    assert unmet or opp.decision_verdict != "STRONG_BUY"


async def test_case_g_the_rare_item_is_insufficient_evidence(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    await seed_deal(make_listing)
    card = await rare_card(auth_client)
    assert card["decision_verdict"] == "INSUFFICIENT_EVIDENCE"
    assert card["verdict"] == "SKIP" and card["recommended_action"] == "watch"
    detail = (await auth_client.get(f"{API}/opportunities/{card['id']}")).json()
    assert detail["decision"]["threshold_price"] is None
    assert detail["decision"]["warnings"] and detail["decision"]["rank_value"] == 0


async def test_case_b_a_hole_the_photos_show_lowers_the_condition_and_the_deal(
    clean_db: None, make_listing
) -> None:
    clean, holed = await analyse(
        make_listing,
        {"analyzer": "claude", "defects": [], "photo_quality": {"has_label_photo": True}},
        {
            "analyzer": "claude",
            "defects": [
                {
                    "kind": "hole",
                    "severity": "severe",
                    "certainty": "certain",
                    "description": "foro sulla manica",
                }
            ],
            "photo_quality": {"has_label_photo": True},
        },
    )
    assert holed.decision is not None and clean.decision is not None
    assert any("peggiori di quelle dichiarate" in w for w in holed.decision["warnings"])
    assert not any("peggiori di quelle dichiarate" in w for w in clean.decision["warnings"])
    assert holed.expected_profit < clean.expected_profit  # the price was recomputed on the real condition
    assert holed.flip_score < clean.flip_score
    cond = holed.score_breakdown["insights"]["condition"]
    assert cond["effective"] == "satisfactory" and cond["differences"]


async def test_the_permanent_record_keeps_the_decision(clean_db: None, make_listing) -> None:
    (opp,) = await analyse(make_listing)
    async with session_scope() as s:
        row = (await s.execute(select(Analysis).where(Analysis.id == opp.analysis_id))).scalar_one()
    assert row.decision["decision_verdict"] == opp.decision_verdict
    assert row.decision["data_completeness_score"] == opp.data_completeness_score
    assert row.decision["decision"]["verdict"] == opp.decision_verdict


async def test_case_a_strong_buy_after_the_photos_were_checked_and_the_label_seen(
    clean_db: None, make_listing
) -> None:
    clear = {"analyzer": "claude", "defects": [], "photo_quality": {"has_label_photo": True}}
    no_label = {"analyzer": "claude", "defects": [], "photo_quality": {"has_label_photo": False}}
    unchecked = None  # no photo analysis at all
    a, b, c = await analyse(make_listing, clear, no_label, unchecked)
    assert a.decision_verdict == "STRONG_BUY"
    assert all(r["met"] for r in a.decision["strong_buy_requirements"])
    assert a.decision["reasons"][0].startswith("Tutti i requisiti verificati")
    for opp, missing in ((b, "label"), (c, "photos_checked")):
        assert opp.decision_verdict != "STRONG_BUY"
        unmet = {r["code"] for r in opp.decision["strong_buy_requirements"] if not r["met"]}
        assert missing in unmet
