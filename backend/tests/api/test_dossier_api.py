"""The dossier through the chain: pipeline -> database -> API -> permanent record (cases B, C, D, F, W)."""

from datetime import timedelta
from decimal import Decimal as D
from typing import Any

import httpx
from sqlalchemy import select, text

from app.db.models import Analysis, Listing, Opportunity
from app.db.session import session_scope
from app.ingestion.service import IngestionService
from app.opportunities.pipeline import AnalysisPipeline
from tests.api.test_api import API
from tests.conftest import NOW
from tests.integration.test_pipeline import build_market

CLEAR = {"analyzer": "claude_vision", "photo_roles": [{"photo": i, "role": r} for i, r in enumerate(["front", "back", "label", "care_label", "detail", "worn"])],
         "photo_quality": {"has_label_photo": True}, "photo_hashes": list("abcdef"), "photo_checks": [{"photo": i, "usable": True, "sharpness": 0.8} for i in range(6)]}  # fmt: skip


async def analyse_one(
    make_listing: Any, vision: dict[str, Any] | None, price: float = 12, **kw: Any
) -> Opportunity:
    async with session_scope() as s:
        await build_market(s, make_listing)
        res = await IngestionService(s, "test").ingest(
            [make_listing(price=price, published_days_ago=0.05, **kw)], now=NOW
        )
        if vision is not None:
            li = await s.get(Listing, res.new_ids[0])
            assert li is not None
            li.identification = {**(li.identification or {}), "vision": vision}
        await s.flush()
        (o,) = await AnalysisPipeline(s).analyze_many(res.new_ids, now=NOW)
        opp = await s.get(Opportunity, o.opportunity_id)
        assert opp is not None
        s.expunge(opp)
        return opp


async def test_the_dossier_is_stored_and_served(auth_client: httpx.AsyncClient, make_listing: Any) -> None:
    opp = await analyse_one(make_listing, CLEAR)
    assert opp.dossier is not None and opp.analysis_coverage_score == opp.dossier["analysis_coverage"]
    detail = (await auth_client.get(f"{API}/opportunities/{opp.id}")).json()
    d = detail["dossier"]
    assert [p["code"] for p in d["passes"]] == [f"P{i}" for i in range(13)]
    assert d["category_plugin"]["key"] == "clothing" and d["signals"] and d["top_reasons"]
    assert detail["card"]["analysis_coverage_score"] == d["analysis_coverage"]
    card = (await auth_client.get(f"{API}/opportunities")).json()["items"][0]
    assert card["analysis_coverage_score"] == d["analysis_coverage"]


async def test_coverage_is_higher_with_analysed_photos_than_without(
    clean_db: None, make_listing: Any
) -> None:
    rich = await analyse_one(make_listing, CLEAR)
    async with session_scope() as s:
        await s.execute(text("TRUNCATE listings CASCADE"))
    plain = await analyse_one(make_listing, None)
    assert rich.analysis_coverage_score > plain.analysis_coverage_score
    assert plain.dossier["inspection_coverage"] is None and plain.dossier["missing_photos"]


async def test_case_b_the_hole_is_in_the_dossier_with_place_and_cost(
    clean_db: None, make_listing: Any
) -> None:
    v = {**CLEAR, "defects": [{"kind": "hole", "severity": "moderate", "certainty": "certain", "description": "foro",
                               "zone": "manica sinistra", "photo": 3, "box": [0.4, 0.5, 0.1, 0.1], "confidence": 0.9}]}  # fmt: skip
    opp = await analyse_one(make_listing, v, price=10, title="Felpa Nike grigia M", brand="Nike")
    d = opp.dossier
    cond = next(c for c in d["contradictions"] if c["code"] == "condition")
    assert "manica sinistra" in cond["detail"] and cond["impact_eur"] > 0
    assert d["condition"]["visible_defects"][0]["box"] == [0.4, 0.5, 0.1, 0.1]
    assert any("manica sinistra" in r["text"] for r in d["top_reasons"] if r["kind"] == "contradiction")


async def test_case_d_the_generic_title_with_a_brand_in_the_photos_is_flagged(
    clean_db: None, make_listing: Any
) -> None:
    v = {**CLEAR, "brand": {"value": "Ralph Lauren", "certainty": "probable", "confidence": 0.7, "evidence": None},
         "logo": {"value": "Polo pony", "certainty": "probable", "confidence": 0.7, "evidence": None}}  # fmt: skip
    opp = await analyse_one(
        make_listing, v, price=12, title="Felpa blu uomo taglia M", brand=None, category="Felpa"
    )
    gem = opp.dossier["hidden_gem"]
    assert gem["possibly_undervalued"] and "modello esatto" in gem["to_verify"]
    assert opp.decision_verdict != "STRONG_BUY"  # a lead is something to verify, not a certainty


async def test_case_f_declared_l_label_m_reaches_the_pipeline(clean_db: None, make_listing: Any) -> None:
    v = {**CLEAR, "size_label": {"value": "M", "certainty": "certain", "confidence": 0.9, "evidence": None}}
    opp = await analyse_one(make_listing, v, size="L")
    c = next(c for c in opp.dossier["contradictions"] if c["code"] == "size")
    assert "dichiarata L, etichetta M" in c["detail"] and c["severity"] == "medium"


async def test_case_c_two_photos_and_no_label(clean_db: None, make_listing: Any) -> None:
    thin = {"analyzer": "claude_vision", "photo_roles": [{"photo": 0, "role": "front"}, {"photo": 1, "role": "front"}],
            "photo_hashes": ["a", "b"], "photo_checks": [{"photo": 0, "usable": True, "sharpness": 0.7}, {"photo": 1, "usable": True, "sharpness": 0.7}]}  # fmt: skip
    opp = await analyse_one(
        make_listing, thin, price=40, title="Maglione firmato Burberry", brand="Burberry", photos=2
    )
    d = opp.dossier
    assert d["inspection_coverage"] < 40 and d["missing_photos"]
    states = {s["type"]: s["state"] for s in d["labels"]["states"]}
    assert states["brand_label"] == "not_visible"
    assert opp.decision_verdict not in ("STRONG_BUY", "BUY")
    assert "autentico al 100%" not in str(d).lower() or "mai «autentico al 100%»" in str(d)


async def test_case_w_a_price_change_leaves_the_photo_passes_alone_and_is_told_in_words(
    clean_db: None, make_listing: Any
) -> None:
    async with session_scope() as s:
        await build_market(s, make_listing)
        pl = make_listing(price=35, published_days_ago=0.05)
        res = await IngestionService(s, "test").ingest([pl], now=NOW)
        li = await s.get(Listing, res.new_ids[0])
        assert li is not None
        li.identification = {**(li.identification or {}), "vision": CLEAR}
        await s.flush()
        lid = res.new_ids[0]
        await AnalysisPipeline(s).analyze_many([lid], now=NOW)
    async with session_scope() as s:
        await IngestionService(s, "test").ingest(
            [pl.model_copy(update={"price": D("23")})], now=NOW + timedelta(hours=1)
        )
        await AnalysisPipeline(s).analyze_many([lid], now=NOW + timedelta(hours=1))
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(Analysis).where(Analysis.listing_id == lid).order_by(Analysis.created_at)
                )
            )
            .scalars()
            .all()
        )
        opp = (await s.execute(select(Opportunity).where(Opportunity.listing_id == lid))).scalar_one()
    assert len(rows) == 2 and rows[1].trigger == "price_change"
    d = opp.dossier
    assert d["delta"] == ["Prezzo da 35 € a 23 €"]
    assert {"P1", "P2", "P4", "P5", "P6", "P8", "P9"}.isdisjoint(d["changed_passes"])
    assert "P12" in d["changed_passes"]
    # The permanent record keeps the same account, in its compact form.
    record = rows[1].decision["dossier"]
    assert record["delta"] == d["delta"] and record["changed_passes"] == d["changed_passes"]
    assert "signals" not in record and record["analysis_coverage"] == d["analysis_coverage"]
