"""Phase 8b through the database: cases I (limits, dry run, kill switch, audit) and J (the verifier) of the brief."""

import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.autonomy import engine
from app.db.models import AutonomyAction, Event, Opportunity
from app.db.session import session_scope
from tests.api.test_api import API
from tests.conftest import NOW
from tests.integration.test_agent import seed

LAX = dict(
    daily_budget=400,
    weekly_budget=1000,
    max_per_item=80,
    min_flip=0,
    min_confidence=0,
    max_risk=100,
    dry_run_days=7,
)
LATER = NOW + timedelta(hours=1)


async def user_id(client: httpx.AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get(f"{API}/auth/me")).json()["id"])


async def enable(client: httpx.AsyncClient, skip_dry_run: bool = False, **limits: Any) -> dict[str, Any]:
    r = await client.put(
        f"{API}/autonomy/enable", json={"limits": {**LAX, **limits}, "skip_dry_run": skip_dry_run}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def cycle(uid: uuid.UUID, llm: Any = None) -> engine.CycleResult:
    async with session_scope() as s:
        res = await engine.run_cycle(s, uid, now=LATER, llm=llm)
        await s.flush()
        return res


async def actions(uid: uuid.UUID) -> list[AutonomyAction]:
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(AutonomyAction)
                    .where(AutonomyAction.user_id == uid)
                    .order_by(AutonomyAction.created_at)
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            s.expunge(r)
        return list(rows)


async def test_case_i_the_dry_run_decides_and_records_without_executing(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    state = await enable(auth_client)
    assert state["mode"] == "dry_run" and state["dry_run"] and state["dry_run_until"]
    res = await cycle(uid)
    assert res.state == "ran" and res.proposed >= 2 and res.executed >= 2 and res.failed == 0
    rows = await actions(uid)
    assert rows and {a.status for a in rows} == {"dry_run"}  # decided and recorded, never executed
    assert all(a.channel == "dry_run" and a.payload["would"] == "buy" and a.verifier["agrees"] for a in rows)
    audit = (await auth_client.get(f"{API}/autonomy/audit")).json()
    kinds = [e["kind"] for e in audit]
    assert "autonomy.enabled" in kinds and kinds.count("autonomy.action") == len(rows)
    rep = (await auth_client.get(f"{API}/autonomy/dry-run-report")).json()
    assert (
        rep["actions"] == len(rows)
        and rep["would_have_bought"] == len(rows)
        and rep["by_status"] == {"dry_run": len(rows)}
    )
    assert rep["verifier_disagreement_rate"] == 0
    # a second cycle does not propose the same purchases again
    assert (await cycle(uid)).proposed == 0


async def test_case_i_nothing_outside_the_limits_gets_through_and_every_refusal_is_logged(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client, max_per_item=5)
    res = await cycle(uid)
    assert res.blocked == res.proposed > 0 and res.executed == 0
    rows = await actions(uid)
    assert {a.status for a in rows} == {"blocked"}
    assert all("max_per_item" in {r["code"] for r in a.reasons} for a in rows)
    audit = (await auth_client.get(f"{API}/autonomy/audit")).json()
    assert sum(
        1 for e in audit if e["kind"] == "autonomy.action" and e["payload"]["status"] == "blocked"
    ) == len(rows)


async def test_case_i_the_daily_budget_is_shared_by_the_actions_of_one_cycle(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client, daily_budget=25, weekly_budget=1000)  # room for one item of ~18-20 EUR only
    res = await cycle(uid)
    statuses = [a.status for a in await actions(uid)]
    assert statuses.count("dry_run") == 1 and statuses.count("blocked") == res.proposed - 1
    blocked = [a for a in await actions(uid) if a.status == "blocked"]
    assert all("daily_budget" in {r["code"] for r in a.reasons} for a in blocked)


async def test_case_i_after_the_dry_run_actions_become_tasks_for_the_user_never_executed_on_vinted(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    state = await enable(auth_client, skip_dry_run=True)
    assert state["mode"] == "assisted" and not state["dry_run"]
    await cycle(uid)
    rows = await actions(uid)
    assert (
        rows and {a.status for a in rows} == {"pending_user"} and all(a.channel == "assisted" for a in rows)
    )
    assert all("Apri l'annuncio su Vinted" in a.payload["todo"] for a in rows)
    one = rows[0]
    done = await auth_client.post(f"{API}/autonomy/actions/{one.id}/resolve", json={"outcome": "done"})
    assert done.json()["status"] == "done"
    again = await auth_client.post(f"{API}/autonomy/actions/{one.id}/resolve", json={"outcome": "done"})
    assert again.status_code == 400 and again.json()["error"]["code"] == "not_pending"
    assert (await cycle(uid)).proposed == 0
    listed = (await auth_client.get(f"{API}/autonomy/actions?status=pending_user")).json()
    assert len(listed) == len(rows) - 1


async def test_case_i_the_kill_switch_stops_everything_at_once_and_resume_restarts_it(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    killed = (await auth_client.post(f"{API}/autonomy/kill")).json()
    assert killed["killed"] is True
    res = await cycle(uid)
    assert res.state == "killed" and res.proposed == 0 and await actions(uid) == []
    assert (await auth_client.post(f"{API}/autonomy/resume")).json()["killed"] is False
    assert (await cycle(uid)).proposed > 0
    kinds = [e["kind"] for e in (await auth_client.get(f"{API}/autonomy/audit")).json()]
    assert "autonomy.killed" in kinds and "autonomy.resumed" in kinds
    # disabling is also a stop
    await auth_client.post(f"{API}/autonomy/disable")
    assert (await cycle(uid)).state == "disabled"


async def test_case_i_an_anomaly_suspends_the_autonomy_until_the_user_resumes(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client, max_loss=50)
    bought = await auth_client.post(
        f"{API}/purchases",
        json={
            "title": "Giacca",
            "purchase_price": 100,
            "shipping_cost": 0,
            "buyer_protection_fee": 0,
            "purchase_date": "2026-09-20",
        },
    )
    await auth_client.post(
        f"{API}/sales",
        json={"purchase_id": bought.json()["purchase_id"], "sale_price": 10, "sale_date": "2026-09-25"},
    )  # a 90 EUR loss
    res = await cycle(uid)
    assert res.state == "suspended" and "Perdite" in res.suspended_for[0] and await actions(uid) == []
    st = (await auth_client.get(f"{API}/autonomy")).json()
    assert st["suspended"] and "Perdite realizzate" in st["suspended_reason"]
    assert (await cycle(uid)).state == "suspended"  # stays stopped
    assert any(
        e["kind"] == "autonomy.suspended" for e in (await auth_client.get(f"{API}/autonomy/audit")).json()
    )


async def test_the_audit_log_cannot_be_rewritten(auth_client: httpx.AsyncClient) -> None:
    await enable(auth_client)
    async with session_scope() as s:
        event_id = (await s.execute(select(Event.id).where(Event.kind == "autonomy.enabled"))).scalar_one()
    for sql in ("UPDATE events SET kind = 'x' WHERE id = :i", "DELETE FROM events WHERE id = :i"):
        with pytest.raises(DBAPIError):
            async with session_scope() as s:
                await s.execute(text(sql), {"i": event_id})


async def test_invalid_limits_are_refused_and_nothing_is_enabled(auth_client: httpx.AsyncClient) -> None:
    bad = await auth_client.put(f"{API}/autonomy/enable", json={"limits": {"daily_budget": -5}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_limits"
    assert (await auth_client.get(f"{API}/autonomy")).json()["enabled"] is False
    assert (await auth_client.put(f"{API}/autonomy/limits", json={"nope": 1})).status_code == 400


# ------------------------------------------------------------------ case J: the verifier
async def tamper(sql: str, **params: Any) -> None:
    async with session_scope() as s:
        await s.execute(text(sql), params)


async def test_case_j_a_wrong_calculation_is_caught_before_the_action_and_the_decision_comes_down(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await tamper(
        "UPDATE opportunities SET expected_profit = expected_profit + 40 WHERE id = :i", i=ids["buy"]
    )
    await enable(auth_client)
    await cycle(uid)
    rows = {str(a.opportunity_id): a for a in await actions(uid)}
    bad = rows[ids["buy"]]
    assert bad.status == "blocked" and bad.verifier["agrees"] is False
    assert {r["code"] for r in bad.reasons} == {"verifier"}
    assert any(i["code"] == "profit_mismatch" for i in bad.verifier["issues"])
    async with session_scope() as s:
        opp = await s.get(Opportunity, uuid.UUID(ids["buy"]))
        assert opp is not None and opp.decision_verdict == "WATCHLIST"
        assert opp.decision["reviewed_from"] == "BUY" and opp.decision["vetoes"][-1]["code"] == "verifier"
    # the untouched candidates went through
    ok = rows[ids["strong"]]
    assert ok.status == "dry_run" and ok.verifier["agrees"] is True


@pytest.mark.parametrize(
    ("sql", "issue"),
    [
        (
            "UPDATE opportunities SET total_acquisition_cost = listing_price - 5 WHERE id = :i",
            "cost_below_price",
        ),
        ("UPDATE opportunities SET expected_roi = expected_roi + 0.5 WHERE id = :i", "roi_mismatch"),
        (
            "UPDATE opportunities SET expected_sale_price = market_p75 * 2, expected_profit = expected_profit WHERE id = :i",
            "resale_above_market",
        ),
        ("UPDATE opportunities SET flip_score = 12 WHERE id = :i", "score_mismatch"),
        (
            'UPDATE opportunities SET dossier = \'{"contradictions": [{"code": "brand", "severity": "high", "detail": "marca dichiarata Nike, etichetta Zara"}]}\'::jsonb WHERE id = :i',
            "identity_contradiction",
        ),
        (
            "UPDATE listings SET status = 'sold' WHERE id = (SELECT listing_id FROM opportunities WHERE id = :i)",
            "not_active",
        ),
        (
            "UPDATE listings SET last_seen_at = last_seen_at - interval '5 days', last_verified_at = NULL WHERE id = (SELECT listing_id FROM opportunities WHERE id = :i)",
            "stale",
        ),
    ],
)
async def test_case_j_each_kind_of_inconsistency_is_intercepted(
    auth_client: httpx.AsyncClient, make_listing: Any, sql: str, issue: str
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await tamper(sql, i=ids["buy"])
    await enable(auth_client)
    await cycle(uid)
    bad = {str(a.opportunity_id): a for a in await actions(uid)}[ids["buy"]]
    assert bad.status == "blocked" and issue in {i["code"] for i in bad.verifier["issues"]}


class FakeLLM:
    enabled = True

    def __init__(self, answer: dict[str, Any] | None) -> None:
        self.answer, self.calls = answer, []

    async def structured(self, **kw: Any) -> dict[str, Any] | None:
        self.calls.append(kw)
        return self.answer


async def test_case_j_a_second_opinion_that_disagrees_stops_the_action_and_one_that_agrees_does_not(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    llm = FakeLLM({"agrees": False, "issues": ["la rivendita sembra ottimistica"]})
    await cycle(uid, llm=llm)
    rows = await actions(uid)
    assert rows and {a.status for a in rows} == {"blocked"}
    assert all(any(i["code"] == "second_opinion" for i in a.verifier["issues"]) for a in rows)
    # a different prompt and the cheaper model, and the listing text is wrapped as untrusted data
    call = llm.calls[0]
    assert call["tier"] == "cheap" and call["purpose"] == "verifier" and "scettico" in call["system"]
    assert any("annuncio_non_fidato" in c["text"] for c in call["content"])


async def test_a_model_that_does_not_answer_changes_nothing(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    await cycle(uid, llm=FakeLLM(None))
    assert {a.status for a in await actions(uid)} == {"dry_run"}
