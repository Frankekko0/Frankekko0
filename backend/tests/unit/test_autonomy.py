"""Phase 8b, pure logic: limits, the policy, the channels and the anomaly rules (case I of the brief)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.autonomy import anomalies, channel, policy
from app.autonomy.limits import Limits, LimitsError, parse_limits
from app.intelligence.exposure import Holding

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
GOOD = dict(
    daily_budget=100,
    weekly_budget=300,
    max_per_item=60,
    max_items=10,
    min_flip=70,
    min_confidence=60,
    max_risk=40,
)


def limits(**kw: object) -> Limits:
    return parse_limits({**GOOD, **kw})


def opp(**kw: object) -> policy.OppFacts:
    base = dict(
        verdict="BUY",
        flip=80,
        confidence=75,
        risk=20,
        brand="ralph-lauren",
        category="felpe",
        premortem_done=True,
        verifier_agrees=True,
    )
    return policy.OppFacts(**{**base, **kw})  # type: ignore[arg-type]


def on(
    enabled: bool = True, killed: bool = False, suspended: bool = False, dry_days: float | None = None
) -> policy.Switches:
    until = NOW + timedelta(days=dry_days) if dry_days is not None else None
    return policy.Switches(enabled, killed, suspended, until, NOW)


def buy(cost: float = 30, **kw: object) -> policy.Proposed:
    return policy.Proposed(policy.Kind.BUY, D(str(cost)), opp(**kw))


def codes(r: policy.PolicyResult) -> set[str]:
    return {v.code for v in r.violations}


# ------------------------------------------------------------------ limits
def test_limits_are_validated_and_round_trip() -> None:
    lim = limits(allowed_brands=["Ralph Lauren", "Nike"], exposure={"brand": 0.5, "min_items": 3})
    assert (
        lim.allowed_brands == ("ralph lauren", "nike")
        and lim.exposure.brand == 0.5
        and lim.exposure.min_items == 3
    )
    assert parse_limits(lim.as_dict()) == lim  # what is stored can be read back
    assert Limits().daily_budget is None  # nothing is allowed until the user sets a budget
    for bad, msg in [
        ({"daily_budget": -1}, "fuori intervallo"),
        ({"daily_budget": "abc"}, "non valido"),
        ({"min_flip": 101}, "intero"),
        ({"min_flip": 70.5}, "intero"),
        ({"max_items": True}, "intero"),
        ({"max_error_rate": 2}, "tra"),
        ({"allowed_brands": "nike"}, "elenco"),
        ({"teleport": 1}, "sconosciuti"),
        ({"daily_budget": 500, "weekly_budget": 100}, "supera"),
        ({"exposure": {"nope": 1}}, "esposizione"),
    ]:
        with pytest.raises(LimitsError, match=msg):
            parse_limits(bad)


# ------------------------------------------------------------------ the policy (case I)
def test_inside_the_limits_an_action_is_allowed_and_dry_run_only_records_it() -> None:
    usage = policy.Usage(owned_items=2)
    assert policy.evaluate(buy(), limits(), usage, on()).mode == "execute"
    dry = policy.evaluate(buy(), limits(), usage, on(dry_days=5))
    assert dry.mode == "dry_run" and dry.allowed and dry.violations == []
    ended = policy.evaluate(buy(), limits(), usage, on(dry_days=-1))
    assert ended.mode == "execute"  # the period is over


def test_the_switches_stop_everything_before_any_limit_is_looked_at() -> None:
    for sw, code in [
        (on(killed=True), "kill_switch"),
        (on(suspended=True), "suspended"),
        (on(enabled=False), "disabled"),
    ]:
        r = policy.evaluate(buy(), limits(), policy.Usage(), sw)
        assert r.mode == "blocked" and codes(r) == {code}
        assert not policy.evaluate(
            policy.Proposed(policy.Kind.MESSAGE, text="Ciao!"), limits(), policy.Usage(), sw
        ).allowed


def test_every_violation_is_reported_not_only_the_first() -> None:
    r = policy.evaluate(
        buy(
            cost=90,
            flip=50,
            confidence=30,
            risk=80,
            brand="zara",
            verifier_agrees=False,
            premortem_done=False,
        ),
        limits(daily_budget=50, weekly_budget=60, allowed_brands=["ralph-lauren"]),
        policy.Usage(spent_today=D(0), spent_week=D(0), owned_items=10),
        on(),
    )
    assert r.mode == "blocked"
    assert codes(r) >= {
        "min_flip", "min_confidence", "max_risk", "brand_not_allowed", "premortem_missing", "verifier",
        "daily_budget", "weekly_budget", "max_per_item", "max_items",
    }  # fmt: skip


def test_without_budgets_nothing_is_bought() -> None:
    r = policy.evaluate(buy(), Limits(), policy.Usage(), on())
    assert {"no_budget", "no_max_per_item"} <= codes(r)
    only_daily = policy.evaluate(
        buy(), parse_limits({"daily_budget": 50, "max_per_item": 60}), policy.Usage(), on()
    )
    assert "no_budget" in codes(only_daily)


def test_budgets_count_what_was_already_used() -> None:
    usage = policy.Usage(spent_today=D("80"), spent_week=D("290"))
    r = policy.evaluate(buy(cost=30), limits(), usage, on())
    assert codes(r) == {"daily_budget", "weekly_budget"}
    assert codes(policy.evaluate(buy(cost=20), limits(), usage, on())) == {"weekly_budget"}  # 290 + 20 > 300
    assert policy.evaluate(buy(cost=10), limits(), usage, on()).mode == "execute"  # exactly at both limits


def test_only_actionable_verdicts_and_fresh_listings_are_bought() -> None:
    for verdict in ("WATCHLIST", "PASS", "INSUFFICIENT_EVIDENCE", "NEGOTIATE"):
        assert "verdict" in codes(policy.evaluate(buy(verdict=verdict), limits(), policy.Usage(), on()))
    assert policy.evaluate(buy(verdict="STRONG_BUY"), limits(), policy.Usage(), on()).mode == "execute"
    assert "not_available" in codes(policy.evaluate(buy(available=False), limits(), policy.Usage(), on()))
    # an offer may follow a NEGOTIATE verdict
    offer = policy.Proposed(policy.Kind.OFFER, D(20), opp(verdict="NEGOTIATE"))
    assert policy.evaluate(offer, limits(), policy.Usage(), on()).mode == "execute"


def test_concentration_is_checked_against_what_is_already_held() -> None:
    held = tuple(Holding(30, "ralph-lauren", "felpe", "M", "20-40") for _ in range(4))
    r = policy.evaluate(
        policy.Proposed(policy.Kind.BUY, D(30), opp(brand="ralph-lauren", category="felpe", size="M", price_band="20-40")),
        limits(), policy.Usage(owned_items=4, holdings=held), on(),
    )  # fmt: skip
    assert "exposure" in codes(r) and "Concentrazione" in r.violations[0].label


def test_messages_are_capped_and_never_pressure() -> None:
    msg = lambda t: policy.Proposed(policy.Kind.MESSAGE, text=t)  # noqa: E731
    assert (
        policy.evaluate(
            msg("Ciao, potresti valutare 22 €? Grazie!"),
            limits(max_messages_per_day=2),
            policy.Usage(messages_today=1),
            on(),
        ).mode
        == "execute"
    )
    capped = policy.evaluate(
        msg("Ciao!"), limits(max_messages_per_day=2), policy.Usage(messages_today=2), on()
    )
    assert codes(capped) == {"max_messages"}
    assert codes(
        policy.evaluate(msg("Ultimo prezzo, ci sono altri interessati!"), limits(), policy.Usage(), on())
    ) == {"pressure"}
    assert codes(policy.evaluate(msg("  "), limits(), policy.Usage(), on())) == {"empty_message"}


def test_a_markdown_never_goes_below_the_floor() -> None:
    ok = policy.Proposed(policy.Kind.REPRICE, price=D("28"), floor=D("25"))
    low = policy.Proposed(policy.Kind.REPRICE, price=D("20"), floor=D("25"))
    assert policy.evaluate(ok, limits(), policy.Usage(), on()).mode == "execute"
    assert codes(policy.evaluate(low, limits(), policy.Usage(), on())) == {"below_floor"}
    assert codes(policy.evaluate(policy.Proposed(policy.Kind.REPRICE), limits(), policy.Usage(), on())) == {
        "no_price"
    }


# ------------------------------------------------------------------ channels
async def test_only_the_dry_run_and_assisted_channels_exist_for_vinted() -> None:
    dry = await channel.get_channel("vinted", "dry_run").execute(policy.Kind.BUY, {"title": "Felpa"})
    assert dry.status == "dry_run" and dry.detail["would"] == "buy"
    task = await channel.get_channel("vinted", "assisted").execute(policy.Kind.REPRICE, {"price": "28"})
    assert (
        task.status == "pending_user"
        and "Aggiorna il prezzo" in task.detail["todo"]
        and task.detail["price"] == "28"
    )
    for marketplace, mode in [("vinted", "auto"), ("vinted", "api"), ("ebay", "assisted"), ("vinted", "")]:
        with pytest.raises(channel.UnsupportedPlatform):
            channel.get_channel(marketplace, mode)
    assert set(channel.CHANNELS) == {("vinted", "dry_run"), ("vinted", "assisted")}


# ------------------------------------------------------------------ anomalies and automatic suspension
def stats(**kw: object) -> anomalies.RecentStats:
    base = dict(actions=10, failed=0, realized_loss=D(0), forecast_error=0.1, sales=5)
    return anomalies.RecentStats(**{**base, **kw})  # type: ignore[arg-type]


def test_anomalies_suspend_on_errors_losses_and_diverging_forecasts() -> None:
    lim = limits()
    assert anomalies.detect(stats(), lim) == []
    assert [a.code for a in anomalies.detect(stats(failed=4), lim)] == ["error_rate"]
    assert [a.code for a in anomalies.detect(stats(realized_loss=D("80")), lim)] == ["loss"]
    assert [a.code for a in anomalies.detect(stats(forecast_error=0.6), lim)] == ["divergence"]
    assert len(anomalies.detect(stats(failed=9, realized_loss=D(500), forecast_error=2.0), lim)) == 3
    # too little to judge: no verdict
    assert anomalies.detect(stats(actions=3, failed=3), lim) == []
    assert anomalies.detect(stats(forecast_error=2.0, sales=2), lim) == []
    assert anomalies.detect(stats(forecast_error=None), lim) == []
