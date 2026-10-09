"""One alert per analysis, and none for a verdict that is not worth acting on."""

import uuid
from decimal import Decimal as D

from app.alerts.rules import WatchlistRule, decide_alerts
from app.domain.enums import AlertPriority, AlertType
from tests.unit.test_misc_engines import THRESHOLDS, cand


def watch(name: str) -> WatchlistRule:
    return WatchlistRule(id=uuid.uuid4(), name=name, brands=("ralph-lauren",), min_flip=10)


def test_everything_at_once_is_a_single_alert_that_names_the_other_reasons() -> None:
    c = cand(is_ultra=True, flip_score=95, previous_price=D("35"), price=D("23"), is_new=True)
    out = decide_alerts(c, THRESHOLDS, [watch("Polo"), watch("Felpe")])
    assert len(out) == 1
    a = out[0]
    assert a.type == AlertType.ULTRA_DEAL and a.priority == AlertPriority.HIGH
    assert "Anche:" in a.body and "prezzo ribassato" in a.body
    assert "«Polo»" in a.body and "«Felpe»" in a.body


def test_the_order_of_importance_is_ultra_then_drop_then_watchlist_then_new() -> None:
    drop = cand(previous_price=D("45"), price=D("27"), is_new=True, flip_score=88)
    out = decide_alerts(drop, THRESHOLDS, [watch("Polo")])
    assert [d.type for d in out] == [AlertType.PRICE_DROP]
    watching = decide_alerts(cand(), THRESHOLDS, [watch("Polo")])
    assert [d.type for d in watching] == [AlertType.WATCHLIST_MATCH]
    assert "nuova opportunità" in watching[0].body


def test_a_single_reason_is_left_alone() -> None:
    out = decide_alerts(cand(), THRESHOLDS, [])
    assert len(out) == 1 and "Anche:" not in out[0].body


def test_the_verdict_is_part_of_the_message() -> None:
    out = decide_alerts(cand(decision_verdict="STRONG_BUY"), THRESHOLDS, [])
    assert "STRONG BUY" in out[0].body


def test_a_pass_or_insufficient_evidence_never_raises_an_alert() -> None:
    for verdict in ("PASS", "INSUFFICIENT_EVIDENCE"):
        c = cand(
            decision_verdict=verdict, is_ultra=True, flip_score=95, previous_price=D("45"), price=D("20")
        )
        assert decide_alerts(c, THRESHOLDS, [watch("Polo")]) == [], verdict


def test_a_watchlist_item_alerts_only_the_person_who_asked() -> None:
    c = cand(
        decision_verdict="WATCHLIST", is_ultra=True, flip_score=95, previous_price=D("45"), price=D("20")
    )
    out = decide_alerts(c, THRESHOLDS, [watch("Polo")])
    assert [d.type for d in out] == [AlertType.WATCHLIST_MATCH]  # no Ultra, no price-drop, no "new"
    assert decide_alerts(c, THRESHOLDS, []) == []


def test_without_a_known_verdict_nothing_is_gated() -> None:
    c = cand(decision_verdict=None, is_ultra=True, flip_score=95)
    assert [d.type for d in decide_alerts(c, THRESHOLDS, [])] == [AlertType.ULTRA_DEAL]


def test_negotiate_is_worth_an_alert() -> None:
    assert decide_alerts(cand(decision_verdict="NEGOTIATE"), THRESHOLDS, [])
