"""The minimal Cowork flow on the bundled test data: classifications, dedup, budget and no invented results."""

from __future__ import annotations

import json
from decimal import Decimal

from app.profit.calculator import CostProfile
from app.tools.cowork_smoke import SAMPLE, Classification, Thresholds, prudent_resale, run

D = Decimal


def _sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _run(**kwargs) -> dict:
    data = _sample()
    return run(
        data["items"],
        profile=CostProfile(**data["cost_profile"]),
        profile_confirmed=kwargs.pop("profile_confirmed", True),
        **kwargs,
    )


def _cards(result: dict) -> dict[str, dict]:
    return {c["item_id"]: c for c in result["cards"]}


def test_sample_classifications() -> None:
    cards = _cards(_run())
    assert cards["TEST-001"]["classification"] == Classification.OPPORTUNITA
    assert cards["TEST-003"]["classification"] == Classification.REVISIONE_UMANA  # no URL
    assert cards["TEST-004"]["classification"] == Classification.SCARTA  # counterfeit beats the margin
    assert cards["TEST-005"]["classification"] == Classification.SCARTA
    assert cards["TEST-006"]["classification"] == Classification.REVISIONE_UMANA  # asking prices only
    assert cards["TEST-007"]["classification"] == Classification.REVISIONE_UMANA  # unknown cleaning cost
    assert cards["TEST-008"]["classification"] == Classification.MONITORA  # weak demand
    assert cards["TEST-009"]["classification"] == Classification.MONITORA  # over the per-item spend


def test_prudent_profit_matches_the_formula() -> None:
    c = _cards(_run())["TEST-001"]
    # 12.50 + 1.33 protection + 2.99 shipping; resale = lower quartile of the sold prices minus packaging.
    assert D(c["total_cost"]) == D("16.82")
    assert D(c["prudent_resale"]) == D("32.50")
    assert D(c["net_profit"]) == D("32.00") - D("16.82")
    assert D(c["roi"]) == (D("15.18") / D("16.82")).quantize(D("0.0001"))


def test_duplicate_across_vinted_domains_is_skipped() -> None:
    result = _run()
    assert result["report"]["duplicati"] == ["TEST-002"]
    assert "TEST-002" not in _cards(result)


def test_nothing_is_spent_or_sent_and_grok_is_declared_missing() -> None:
    report = _run()["report"]
    assert report["capitale_impegnato"] == "0.00"
    assert report["alert_inviati"] == 0
    assert report["profitto_realizzato"] == "0.00"
    assert any("Grok" in x for x in report["integrazioni_non_disponibili"])
    assert all(c["grok"].startswith("non collegato") for c in _run()["cards"])


def test_unconfirmed_cost_profile_sends_everything_to_review() -> None:
    cards = _cards(_run(profile_confirmed=False))
    assert cards["TEST-001"]["classification"] == Classification.REVISIONE_UMANA
    assert cards["TEST-001"]["unknown_costs"]


def test_budget_downgrades_what_does_not_fit() -> None:
    result = _run(th=Thresholds(total_budget=D("10")))
    c = _cards(result)["TEST-001"]
    assert c["classification"] == Classification.REVISIONE_UMANA
    assert "budget residuo insufficiente" in c["reasons"]
    assert result["report"]["alert_preparati"] == []


def test_asking_prices_are_discounted_not_treated_as_sales() -> None:
    price, basis = prudent_resale(
        [{"price": "40", "sold": False}, {"price": "50", "sold": False}], Thresholds()
    )
    assert price == D("34.00")  # lower quartile 42.50, minus 20%
    assert basis is not None and "prezzi richiesti" in basis
    assert prudent_resale([], Thresholds()) == (None, None)
