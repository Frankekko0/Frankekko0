"""Phase 8c through the analysis: the distribution, the pre-mortem, the value of information and the
seller's scam risk travel with the decision (cases L and M of the brief)."""

from typing import Any

from sqlalchemy import select

from app.db.models import Listing, Opportunity
from app.db.session import session_scope
from app.ingestion.service import IngestionService
from app.opportunities.pipeline import AnalysisPipeline
from app.workers.vision_queue import vision_order, worth_vision
from tests.api.test_api import NOW, build_market
from tests.integration.test_agent import CLEAR


async def analyse(make_listing: Any, specs: list[dict[str, Any]]) -> list[Any]:
    """Seed one market, ingest one listing per spec (optionally with a photo analysis and extra
    identification flags) and analyse them all; returns the pipeline outcomes."""
    async with session_scope() as s:
        await build_market(s, make_listing)
        res = await IngestionService(s, "test").ingest(
            [make_listing(published_days_ago=0.05, **sp["listing"]) for sp in specs], now=NOW
        )
        for lid, sp in zip(res.new_ids, specs, strict=True):
            li = await s.get(Listing, lid)
            assert li is not None
            ident = dict(li.identification or {})
            if sp.get("vision") is not None:
                ident["vision"] = sp["vision"]
            ident.update(sp.get("flags", {}))
            li.identification = ident
        await s.flush()
        outcomes = await AnalysisPipeline(s).analyze_many(res.new_ids, now=NOW)
        for o in outcomes:  # the later checks read them outside this session
            if o.listing is not None:
                s.expunge(o.listing)
        return outcomes


def block(outcome: Any) -> dict[str, Any]:
    return outcome.result.decision.intelligence


async def test_every_decision_carries_its_profit_distribution_and_seller_risk(
    clean_db: None, make_listing: Any
) -> None:
    (o,) = await analyse(make_listing, [{"listing": dict(price=12), "vision": CLEAR}])
    b = block(o)
    d = b["distribution"]
    assert d["p10"] <= d["p50"] <= d["p90"] and 0 <= d["p_loss"] <= 1 and d["n"] > 0
    assert b["seller_risk"]["level"] in ("low", "medium", "high")
    async with session_scope() as s:  # stored with the opportunity, so the page can show it
        opp = (await s.execute(select(Opportunity))).scalars().one()
        assert opp.decision["intelligence"]["distribution"]["p_loss"] == d["p_loss"]
    assert any(r.code == "downside" for r in o.result.decision.strong_buy_requirements)


async def test_the_premortem_is_there_above_the_cost_threshold_with_checks(
    clean_db: None, make_listing: Any
) -> None:
    (cheap,) = await analyse(make_listing, [{"listing": dict(price=5), "vision": CLEAR}])
    assert block(cheap)["premortem"]["required"] is False and block(cheap)["premortem"]["modes"] == []
    (dear,) = await analyse(make_listing, [{"listing": dict(price=30), "vision": None}])
    pre = block(dear)["premortem"]
    assert pre["required"] is True and len(pre["modes"]) == 3
    assert all(m["check"] in ("verified_ok", "contradicted", "unverifiable") for m in pre["modes"])
    assert [m["expected_loss"] for m in pre["modes"]] == sorted(
        (m["expected_loss"] for m in pre["modes"]), reverse=True
    )


async def test_case_l_a_suspect_seller_is_scored_high_and_nothing_is_bought(
    clean_db: None, make_listing: Any
) -> None:
    (o,) = await analyse(
        make_listing,
        [
            {
                "listing": dict(
                    price=12,
                    seller_reviews=0,
                    description="Contattami su WhatsApp, pago con PayPal amici fuori da Vinted. Foto vere.",
                ),
                "vision": CLEAR,
                "flags": {"photos_reused_by_other_seller": True},
            }
        ],
    )
    risk = block(o)["seller_risk"]
    assert risk["score"] >= 70 and risk["level"] == "high"
    assert {f["label"] for f in risk["factors"]} >= {
        "Foto già usate da altri venditori",
        "Chiede pagamento o contatti fuori dalla piattaforma",
    }
    d = o.result.decision
    assert d.verdict.value == "PASS" and any(v.code == "seller_scam_risk" and v.binding for v in d.vetoes)


async def test_case_m_deep_analysis_is_spent_at_the_boundary_and_not_on_a_clear_pass(
    clean_db: None, make_listing: Any
) -> None:
    outcomes = await analyse(
        make_listing,
        [
            {"listing": dict(price=40), "vision": None},  # far above the market: a clear PASS
            {"listing": dict(price=15), "vision": None},  # the boundary: profitable if the photos are fine
        ],
    )
    clear_pass, boundary = outcomes
    assert clear_pass.result.decision.verdict.value in ("PASS", "WATCHLIST")
    assert block(clear_pass)["voi"]["worth_it"] is False
    assert block(boundary)["voi"]["worth_it"] is True and block(boundary)["voi"]["resolvable"] > 0.5 * 1.5
    # The queue follows the value of information, and every photo request is justified by it.
    for o in outcomes:  # simulate photos uploaded: the queue only looks at listings that have them
        for img in o.listing.images:
            img.local_path = "ab/x.jpg"
    assert not worth_vision(clear_pass) and worth_vision(boundary)
    # AI_VISION_ALWAYS: no value-of-information gate, every complete gallery is checked (best candidate first).
    assert worth_vision(clear_pass, always=True) and worth_vision(boundary, always=True)
    assert set(vision_order(outcomes, always=True)) == {str(clear_pass.listing_id), str(boundary.listing_id)}
    assert vision_order(outcomes) == [str(boundary.listing_id)]
    for o in outcomes:
        if worth_vision(o):
            v = block(o)["voi"]
            assert v["resolvable"] > v["cost"]
