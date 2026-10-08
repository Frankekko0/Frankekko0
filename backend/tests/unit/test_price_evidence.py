"""Price evidence (pure parts): source weights, negotiation discount, strict model matching,
merging into the pricing pool, provenance, statistics keys, outliers and the backtest gate."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.analytics.backtest import Case
from app.analytics.evidence import MIN_AFFECTED, VARIANTS, EvidenceBacktest, EvidencePool, decide_gate
from app.market.cleaning import outlier_ids
from app.market.model_stats import StatQuery, candidate_keys, level_of, stat_key
from app.market.negotiation import discount_from_samples
from app.opportunities.engine import estimate_from_similar
from app.pricing.comparables import SOURCE_WEIGHTS, ItemProfile, evidence_source, select_comparables
from app.pricing.evidence import (
    NEW_CAP_NOTE,
    EvidenceGate,
    ExternalRef,
    OwnRecord,
    PriceEvidence,
    build_provenance,
    cap_at_new_price,
    discount_from_state,
    evidence_condition,
    evidence_for_subject,
    same_model,
    with_evidence,
)
from app.pricing.market_value import SegmentPrior, estimate_market_value

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def item(
    price: float = 30,
    *,
    status: str = "active",
    source: str = "listing",
    model: str | None = "Air Max 90",
    **kw,
):
    return ItemProfile(
        **{
            "id": uuid.uuid4() if source == "listing" else None,
            "title": f"Nike {model or ''} bianche 42",
            "price": D(str(price)),
            "brand": "nike",
            "category": "sneakers",
            "parent_category": "footwear",
            "model": model,
            "condition": "very_good",
            "size": "EU42",
            "status": status,
            "published_at": NOW - timedelta(days=10),
            "sold_at": NOW - timedelta(days=2) if status == "sold" else None,
            "last_seen_at": NOW - timedelta(days=2),
            "source": source,
            **kw,
        }
    )


SUBJECT = item(25, status="active")


def own(
    rid: int, source: str, price: float, model: str = "Air Max 90", brand: str = "nike", **kw
) -> OwnRecord:
    return OwnRecord(
        id=rid,
        source=source,
        title=f"Nike {model}",
        price_eur=price,
        brand_slug=brand,
        category_slug="sneakers",
        model_name=model,
        size="EU42",
        condition="very_good",
        sold_at=NOW - timedelta(days=3),
        **kw,
    )


def ref(
    rid: int, kind: str, price: float, model: str = "Air Max 90", url: str | None = None, days: float = 3
) -> ExternalRef:
    return ExternalRef(
        id=rid,
        kind=kind,
        source="ebay.it",
        price=price,
        currency="EUR",
        price_eur=price,
        at=NOW - timedelta(days=days),
        condition="used",
        url=url or f"https://www.ebay.it/itm/{rid}",
        title=f"Nike {model} usate",
        brand_slug="nike",
        model_name=model,
    )


# ---------------------------------------------------------------------------- weights
def test_source_weights_replace_the_sold_bonus() -> None:
    sources = {
        "vinted_asking": item(status="active"),
        "vinted_sold": item(status="sold"),
        "own_sale": item(status="sold", source="own_sale"),
        "own_purchase": item(status="sold", source="own_purchase"),
        "external_sold": item(status="sold", source="external_sold"),
        "external_asking": item(status="active", source="external_asking"),
    }
    # Same item, same dates: only the source changes the weight.
    same_time = {
        k: replace(v, last_seen_at=NOW, sold_at=NOW if v.status == "sold" else None)
        for k, v in sources.items()
    }
    weights = {k: select_comparables(SUBJECT, [v], NOW)[0].weight for k, v in same_time.items()}
    for k, w in weights.items():
        assert evidence_source(same_time[k]) == k
        assert w / weights["vinted_asking"] == pytest.approx(SOURCE_WEIGHTS[k])
    assert SOURCE_WEIGHTS == {
        "own_sale": 5.0,
        "own_purchase": 1.5,
        "vinted_sold": 1.5,
        "external_sold": 1.0,
        "vinted_asking": 1.0,
        "external_asking": 0.5,
    }


# ---------------------------------------------------------------------------- discount
def test_negotiation_discount_needs_three_purchases_and_is_robust() -> None:
    assert discount_from_samples([0.1, 0.2]) == (None, 2)
    assert discount_from_samples([0.1, 0.05, 0.2]) == (0.1, 3)
    assert discount_from_samples([0.6, 0.7, 0.8]) == (0.5, 3)  # clipped at 50%
    assert discount_from_samples([-0.1, -0.2, 0.0]) == (0.0, 3)  # paid more: no discount
    assert discount_from_samples([0.1, 0.1, 0.1, 5.0, -3.0]) == (0.1, 3)  # data errors dropped
    assert discount_from_state({"discount": 0.08}) == 0.08
    assert discount_from_state({"discount": None}) is None and discount_from_state(None) is None
    assert discount_from_state({"discount": 0.0}) is None


# ---------------------------------------------------------------------------- matching
def test_strict_model_matching() -> None:
    assert same_model("Air Max 90", "air  max 90") and same_model("Éclair", "eclair")
    assert not same_model("Air Max 90", "Air Max 95") and not same_model(None, "Air Max 90")
    records = [
        own(1, "own_sale", 40),
        own(2, "own_sale", 90, model="Air Max 95"),
        own(3, "own_purchase", 30, brand="adidas"),
    ]
    refs = [
        ref(10, "sold", 38),
        ref(11, "sold", 99, model="Air Max 95"),
        ref(12, "asking", 45),
        ref(13, "asking", 41, url="https://www.ebay.it/itm/10"),  # same page as a sale: dropped
        ref(14, "new", 120),
        replace(ref(15, "sold", 39), brand_slug="adidas"),
    ]
    ev = evidence_for_subject(
        SUBJECT, records, refs, negotiation_discount=None, gate=EvidenceGate(), parent_of=lambda c: "footwear"
    )
    assert [p.ref_id for p in ev.own] == [1]
    assert sorted(p.ref_id for p in ev.external) == [10, 12]
    assert [r.id for r in ev.new_prices] == [14] and ev.new_median == 120
    assert set(ev.refs) == {10, 12, 14}
    sold = next(p for p in ev.external if p.ref_id == 10)
    assert sold.status == "sold" and sold.source == "external_sold" and sold.brand == "nike"
    assert sold.category == "sneakers" and sold.condition == "unknown"  # generic "used"
    # No model on the subject: nothing is strict enough to match.
    empty = evidence_for_subject(
        replace(SUBJECT, model=None),
        records,
        refs,
        negotiation_discount=None,
        gate=EvidenceGate(),
        parent_of=str,
    )
    assert not empty.own and not empty.external and not empty.refs


def test_evidence_condition() -> None:
    assert evidence_condition("good") == "good"
    assert (
        evidence_condition("new") == "new_without_tags" and evidence_condition("Nuovo") == "new_without_tags"
    )
    assert evidence_condition("used") == "unknown" and evidence_condition(None) == "unknown"


# ---------------------------------------------------------------------------- pool merge
def test_with_evidence_discounts_vinted_sales_and_counts_a_purchase_once() -> None:
    bought = item(30, status="sold")
    others = [item(32, status="sold"), item(40, status="active")]
    pool = select_comparables(SUBJECT, [bought, *others], NOW)
    before = {id(c): c.adjusted_price for c in pool}
    purchase = own(5, "own_purchase", 26, listing_id=bought.id)
    ev = evidence_for_subject(
        SUBJECT, [purchase], [], negotiation_discount=0.1, gate=EvidenceGate(), parent_of=lambda c: "footwear"
    )
    merged = with_evidence(SUBJECT, pool, ev, NOW)
    assert all(c.adjusted_price == before[id(c)] for c in pool)  # inputs never mutated
    ids = [c.item.id for c in merged if c.item.source == "listing"]
    assert bought.id not in ids  # recorded once, as the purchase
    sold_listing = next(c for c in merged if c.item.source == "listing" and c.item.status == "sold")
    assert sold_listing.adjusted_price == pytest.approx(32 * 0.9)
    ask = next(c for c in merged if c.item.status == "active")
    assert ask.adjusted_price == pytest.approx(40)  # asks are not discounted
    assert any(c.item.source == "own_purchase" for c in merged)
    # Gate off for purchases: the listing stays, the purchase does not enter.
    ev_off = replace(ev, gate=EvidenceGate(use_own_purchases=False))
    merged_off = with_evidence(SUBJECT, pool, ev_off, NOW)
    assert bought.id in [c.item.id for c in merged_off] and not any(
        c.item.source == "own_purchase" for c in merged_off
    )


def test_every_sale_source_counts_for_the_sold_first_rule() -> None:
    asks = [item(45 + i, status="active") for i in range(8)]
    similar = select_comparables(SUBJECT, [*asks, item(30, status="sold"), item(31, status="sold")], NOW)
    refs = [ref(20 + i, "sold", 30 + i) for i in range(3)]
    ev = evidence_for_subject(
        SUBJECT, [], refs, negotiation_discount=None, gate=EvidenceGate(), parent_of=str
    )
    _, market = estimate_from_similar(SUBJECT, similar, NOW, None, ev)
    assert market.n_sold == 5 and market.n_active == 0  # 2 Vinted + 3 elsewhere: asks are reference only
    _, plain = estimate_from_similar(SUBJECT, similar, NOW, None, None)
    assert plain.n_active > 0 and plain.expected_sale_price > market.expected_sale_price


def test_new_price_cap_only_for_used_items() -> None:
    comps = select_comparables(SUBJECT, [item(p, status="sold") for p in (80, 90, 100, 110, 130, 150)], NOW)
    market = estimate_market_value(comps, "very_good", NOW)
    ev = PriceEvidence(new_prices=[ref(1, "new", 95)], gate=EvidenceGate(use_new_cap=True))
    capped = cap_at_new_price(market, ev, "very_good")
    assert market.optimistic_sale_price > D("95")
    assert capped.optimistic_sale_price == max(D("95.00"), capped.expected_sale_price)
    assert cap_at_new_price(market, ev, "new_with_tags") is market
    assert cap_at_new_price(market, PriceEvidence(), "very_good") is market


class _WideCalibration:
    """An active calibration whose range is wider than the comparables' (as on a mature database)."""

    active = True
    n = 200

    def apply(self, expected, category, brand, condition, confidence):  # type: ignore[no-untyped-def]
        return expected * 0.8, expected, expected * 1.3


def test_new_price_cap_holds_after_calibration_and_the_note_tells_the_truth() -> None:
    from app.opportunities.engine import price_estimate

    cands = [item(p, status="sold") for p in (60, 70, 80, 90, 100, 110, 120, 130)]
    ev = PriceEvidence(
        new_prices=[ref(i, "new", 80) for i in range(3)],
        gate=EvidenceGate(use_external=True, use_own_purchases=True, use_new_cap=True),
    )
    _, _, market = price_estimate(SUBJECT, cands, NOW, None, _WideCalibration(), ev)  # type: ignore[arg-type]
    assert market.optimistic_sale_price <= max(D("80.00"), market.expected_sale_price)
    assert sum(n.startswith(NEW_CAP_NOTE) for n in market.notes) == 1
    # Without the cap in use the calibrated maximum stays and no cap note is shown.
    ev_off = replace(ev, gate=EvidenceGate(use_external=True, use_own_purchases=True, use_new_cap=False))
    _, _, free = price_estimate(SUBJECT, cands, NOW, None, _WideCalibration(), ev_off)  # type: ignore[arg-type]
    assert free.optimistic_sale_price > D("80") and not any(n.startswith(NEW_CAP_NOTE) for n in free.notes)


# ---------------------------------------------------------------------------- provenance
PROVENANCE = {
    "expected_price": {
        "value",
        "basis",
        "n",
        "by_source",
        "negotiation_discount",
        "calibrated",
        "prior",
        "label",
    },
    "price_range": {"low", "high", "basis", "label"},
    "days_to_sell": {"value", "n", "basis", "label"},
    "sale_probability": {"value", "n", "basis", "label"},
    "real_sales": {"total", "own", "vinted_sold", "external_sold"},
}


def _prov(market, ev=None, prior=None, **kw):
    return build_provenance(
        market,
        evidence=ev,
        prior=prior,
        calibrated=kw.get("calibrated", False),
        calibration_n=40,
        velocity_days=9.0,
        velocity_n=kw.get("velocity_n", 3),
        days_basis=kw.get("days_basis", "sold"),
        p_sale=kw.get("p_sale", {"p": 0.6, "n": 14, "horizon_days": 30, "source": "similar"}),
    )


def test_provenance_shape_and_basis() -> None:
    ev = evidence_for_subject(
        SUBJECT,
        [own(1, "own_sale", 33)],
        [ref(2, "sold", 31), ref(3, "asking", 45), ref(4, "new", 99)],
        negotiation_discount=0.07,
        gate=EvidenceGate(),
        parent_of=str,
    )
    similar = select_comparables(SUBJECT, [item(30, status="sold"), item(28, status="sold"), item(40)], NOW)
    _, market = estimate_from_similar(SUBJECT, similar, NOW, None, ev)
    prior = SegmentPrior(30, 25, 35, 12, level="model")
    p = _prov(market, ev, prior, calibrated=True)
    assert set(p) == {*PROVENANCE, "new_price", "external"}
    for key, fields in PROVENANCE.items():
        assert set(p[key]) == fields
    exp = p["expected_price"]
    assert exp["basis"] == "mixed" and exp["n"] == market.n_used
    assert exp["by_source"]["own_sale"] == 1 and exp["by_source"]["vinted_sold"] == 2
    assert exp["by_source"]["external_sold"] == 1 and exp["by_source"]["external_asking"] == 1
    assert exp["negotiation_discount"] == 0.07 and exp["calibrated"] is True
    assert exp["prior"] == {"used": market.used_prior, "level": "model", "n": 12}
    assert "4 vendite concluse (1 tua, 2 Vinted, 1 da altri mercati)" in exp["label"]
    assert "scontato del 7%" in exp["label"]
    assert p["real_sales"] == {"total": 4, "own": 1, "vinted_sold": 2, "external_sold": 1}
    assert p["price_range"]["basis"] == "calibration" and "40 vendite reali" in p["price_range"]["label"]
    assert p["new_price"]["value"] == 99 and set(p["new_price"]) == {"value", "n", "sources", "label"}
    assert [e["kind"] for e in p["external"]][:2] == ["sold", "asking"]
    assert all(e["used_in_estimate"] for e in p["external"] if e["kind"] in ("sold", "asking"))
    assert not next(e for e in p["external"] if e["kind"] == "new")["used_in_estimate"]
    assert p["sale_probability"]["basis"] == "similar" and p["sale_probability"]["value"] == 0.6


def test_provenance_basis_variants() -> None:
    asks = select_comparables(SUBJECT, [item(40 + i) for i in range(4)], NOW)
    _, asking = estimate_from_similar(SUBJECT, asks, NOW, None, None)
    p = _prov(asking, p_sale={"p": None, "n": 2, "reason": "Solo 2 annunci simili con esito noto."})
    assert (
        p["expected_price"]["basis"] == "asking"
        and "Nessuna vendita conclusa" in p["expected_price"]["label"]
    )
    assert p["sale_probability"] == {
        "value": None,
        "n": 2,
        "basis": "insufficient",
        "label": "Solo 2 annunci simili con esito noto.",
    }
    assert p["new_price"] is None and p["external"] == []
    sold = select_comparables(SUBJECT, [item(30 + i, status="sold") for i in range(6)], NOW)
    _, only_sold = estimate_from_similar(SUBJECT, sold, NOW, None, None)
    p = _prov(only_sold, days_basis="segment", velocity_n=0)
    assert p["expected_price"]["basis"] == "sold" and "non ancora misurato" in p["expected_price"]["label"]
    assert p["days_to_sell"]["basis"] == "segment" and p["price_range"]["basis"] == "percentiles"
    _, none = estimate_from_similar(SUBJECT, sold[:2], NOW, None, None)
    p = _prov(none)
    assert p["expected_price"]["basis"] == "none" and p["expected_price"]["value"] is None
    assert p["price_range"]["low"] is None and "Dati insufficienti" in p["expected_price"]["label"]


def test_gate_state() -> None:
    gate = EvidenceGate.from_state(None)
    assert gate.use_external and gate.use_own_purchases and not gate.use_new_cap
    assert "Non ancora misurabile" in gate.note
    gate = EvidenceGate.from_state(
        {"use_external": False, "use_own_purchases": True, "use_new_cap": True, "note": "x"}
    )
    assert not gate.use_external and gate.use_new_cap and gate.note == "x"
    ev = PriceEvidence(
        own=[item(status="sold", source="own_sale"), item(status="sold", source="own_purchase")],
        external=[item(status="sold", source="external_sold")],
        gate=EvidenceGate(use_external=False, use_own_purchases=False),
    )
    assert [p.source for p in ev.candidates()] == ["own_sale"]  # own resales are never gated


# ---------------------------------------------------------------------------- statistics
def test_stat_keys_and_fallback_order() -> None:
    assert stat_key(3, 8, "Air Max 90", "EU42", "good") == "3|8|air max 90|EU42|good"
    assert stat_key(3, None, None, None, None) == "3|*|*|*|*"
    q = StatQuery("1", 3, 8, "Air Max 90", "EU42", "good")
    assert [lvl for lvl, _ in candidate_keys(q)] == [
        "model_size_condition",
        "model_condition",
        "model_size",
        "model",
        "brand_category",
    ]
    assert candidate_keys(q)[0][1] == "3|*|air max 90|EU42|good"  # model levels: any category
    assert candidate_keys(q)[-1][1] == "3|8|*|*|*"
    unknown = StatQuery("2", 3, None, "Air Max 90", None, "unknown")
    assert [lvl for lvl, _ in candidate_keys(unknown)] == ["model"]
    assert candidate_keys(StatQuery("3", None, 8, "x", None, None)) == []
    assert level_of("x", "M", None) == "model_size" and level_of(None, None, None) == "brand_category"


def test_outlier_ids_per_group() -> None:
    rows = [(i, "a", p, "very_good") for i, p in enumerate((30, 31, 29, 32, 300))]
    rows += [(10, "b", 30, "good"), (11, "b", 300, "good")]  # two prices: cannot tell
    rows += [
        (20, "c", 100, "new_with_tags"),
        (21, "c", 70, "satisfactory"),
        (22, "c", 88, "good"),
        (23, "c", 92, "very_good"),
    ]
    assert outlier_ids(rows) == {4}  # condition-adjusted, the "c" group is consistent


# ---------------------------------------------------------------------------- backtest gate
def test_pool_has_no_look_ahead() -> None:
    late = replace(own(2, "own_sale", 50), sold_at=NOW + timedelta(days=5))
    pool = EvidencePool.build(
        [own(1, "own_sale", 40), late], [ref(3, "sold", 38, days=1), ref(4, "sold", 39, days=-1)], None
    )
    key = ("nike", "air max 90")
    assert [r.id for r in pool.own_before(key, NOW)] == [1]
    assert [r.id for r in pool.external_before(key, NOW)] == [3]
    assert [r.id for r in pool.external_before(key, NOW + timedelta(days=2))] == [3, 4]


def _case(i: int, expected: float | None, realized: float = 50, **kw) -> Case:
    return Case(
        None,
        ("nike", "sneakers"),
        "good",
        NOW + timedelta(hours=i),
        realized,
        expected,
        None,
        None,
        10,
        70,
        **kw,
    )  # type: ignore[arg-type]


def _bt(**variants: list[Case]) -> EvidenceBacktest:
    base = variants.pop("listings")
    cases = {"baseline": base, "listings": base}
    for name in VARIANTS.values():
        cases[name] = variants.get(name, variants.get(name.removesuffix("_cap"), base))
    return EvidenceBacktest(cases=cases, split=NOW, discount=None)


def test_gate_keeps_sources_until_measurable_and_drops_harmful_ones() -> None:
    n = MIN_AFFECTED + 5
    listings = [_case(i, 55) for i in range(n)]
    # External data used on few test sales: not measurable, kept with its low weight.
    few = [_case(i, 70, n_external=1 if i < 5 else 0) for i in range(n)]
    gate = decide_gate(_bt(listings=listings, external_own=few, external_sales=few))
    assert gate["use_external"] is True and gate["use_own_purchases"] is True and gate["use_new_cap"] is False
    assert "non ancora misurabili" in gate["note"] and gate["affected"]["external"] == 5
    assert set(gate) >= {
        "measured_at",
        "without_external",
        "with_external",
        "use_external",
        "use_own_purchases",
        "use_new_cap",
        "n_subjects",
        "note",
    }
    assert gate["n_subjects"] == n
    # Measurable and harmful: switched off; measurable and helpful: kept.
    worse = [_case(i, 70, n_external=1) for i in range(n)]
    gate = decide_gate(_bt(listings=listings, external_own=worse, external_sales=worse))
    assert gate["use_external"] is False and gate["variant"] == "own"
    assert gate["with_external"]["mae_eur"] == pytest.approx(20) and gate["without_external"][
        "mae_eur"
    ] == pytest.approx(5)
    better = [_case(i, 51, n_external=1) for i in range(n)]
    gate = decide_gate(_bt(listings=listings, external_own=better, external_sales=better))
    assert gate["use_external"] is True and gate["variant"] == "external_own"
    # Own purchases measurable and harmful.
    bad_own = [_case(i, 65, n_own_purchase=1) for i in range(n)]
    gate = decide_gate(_bt(listings=listings, own=bad_own, own_sales=listings))
    assert gate["use_own_purchases"] is False and gate["variant"] in ("external_sales", "own_sales")


def test_a_bought_listing_is_never_its_own_comparable() -> None:
    mine = own(1, "own_purchase", 40.0, listing_id=SUBJECT.id)
    other = own(2, "own_purchase", 44.0, listing_id=uuid.uuid4())
    ev = evidence_for_subject(
        SUBJECT, [mine, other], [], negotiation_discount=None, gate=EvidenceGate(), parent_of=str
    )
    assert len(ev.own) == 1 and float(ev.own[0].price) == 44.0
