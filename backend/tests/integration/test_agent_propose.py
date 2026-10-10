"""The agent that proposes purchases and markdowns: it prepares records for the user, it never executes.

Through the database with a scripted model (no provider is called): the same policy as the autonomy cycle, the
switches, the verifier, the audit, de-duplication, the per-user scope and the guarantees that nothing leaves the
server and that a model can only lower things.
"""

import json
import uuid
from datetime import timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.agent import propose as propose_mod
from app.agent.model import ScriptedModel
from app.agent.review import TOOLS as REVIEW_TOOLS
from app.agent.tools import ToolContext, proposal_registry
from app.autonomy import engine, policy
from app.autonomy.channel import CHANNELS
from app.autonomy.limits import parse_limits
from app.core.config import Settings
from app.db.models import (
    AgentRun,
    AutonomyAction,
    AutonomySettings,
    Event,
    InventoryItem,
    Opportunity,
    Purchase,
    User,
)
from app.db.session import session_scope
from app.opportunities.pipeline import default_cost_profile, default_targets
from app.selling import service as selling
from tests.api.test_api import API
from tests.api.test_selling_api import buy
from tests.integration.test_agent import INJECTION, call, run_review, say, seed, submit
from tests.integration.test_autonomy_cycle import LATER, cycle, enable, tamper, user_id

LISTED_DAYS = 20  # long enough for a markdown of the plan to be due


def cfg(**kw: Any) -> Settings:
    return Settings(**{"agent_propose_enabled": True, "agent_max_proposals_per_run": 3, **kw})


async def run_propose(
    uid: uuid.UUID, model: Any, *, settings: Settings | None = None, **kw: Any
) -> AgentRun | None:
    kw.setdefault("now", LATER)
    async with session_scope() as s:
        run = await propose_mod.propose_for_user(s, uid, model=model, settings=settings or cfg(), **kw)
        if run is not None:
            await s.flush()
            s.expunge(run)
        return run


async def all_actions(uid: uuid.UUID) -> list[AutonomyAction]:
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(AutonomyAction)
                    .where(AutonomyAction.user_id == uid)
                    .order_by(AutonomyAction.created_at, AutonomyAction.id)
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            s.expunge(r)
        return list(rows)


async def agent_actions(uid: uuid.UUID) -> list[AutonomyAction]:
    return [a for a in await all_actions(uid) if a.payload.get("source") == "agent"]


async def list_item(client: httpx.AsyncClient, opp_id: str, days: float = LISTED_DAYS, **extra: Any) -> str:
    """Buy the opportunity, list it at the plan's start price and make it ``days`` old at the cycle's time."""
    pid = await buy(client, opp_id)
    plan = (await client.get(f"{API}/selling/inventory/{pid}/plan")).json()["plan"]
    assert plan is not None
    r = await client.patch(
        f"{API}/selling/inventory/{pid}",
        json={
            "stage": "listed",
            "listed_price": plan["start_price"],
            "reason": "prezzo di partenza",
            "listing_url": "https://www.vinted.it/items/123-polo",
            **extra,
        },
    )
    assert r.status_code == 200, r.text
    await tamper(
        "UPDATE inventory SET listed_at = :t WHERE purchase_id = :p",
        t=LATER - timedelta(days=days),
        p=uuid.UUID(pid),
    )
    return pid


async def advice_for(pid: str) -> selling.RepriceView:
    async with session_scope() as s:
        p = await s.get(Purchase, uuid.UUID(pid))
        assert p is not None
        item = (await s.execute(select(InventoryItem).where(InventoryItem.purchase_id == p.id))).scalar_one()
        costs, min_profit = default_cost_profile(), float(default_targets().min_profit)
        return await selling.reprice_advice(s, p, item, costs, min_profit, LATER)


def tool_steps(run: AgentRun) -> list[dict[str, Any]]:
    return [s for s in run.steps if s["kind"] == "tool"]


def ctx_for(
    session: Any, uid: uuid.UUID | None, ids: dict[str, str], pids: list[str] | None = None, **kw: Any
) -> ToolContext:
    scope = frozenset(v for k, v in ids.items() if k in ("strong", "buy", "inject"))
    return ToolContext(
        session,
        uuid.uuid4(),
        cfg(),
        scope,
        user_id=uid,
        inventory_scope=frozenset(pids or []),
        proposals_remaining=kw.pop("proposals_remaining", 3),
        now=LATER,
        **kw,
    )


# ------------------------------------------------------------------ a whole run
async def test_the_agent_proposes_a_purchase_and_a_markdown_and_nothing_is_executed(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    pid = await list_item(auth_client, ids["watch"])
    advice = (await advice_for(pid)).advice
    assert advice is not None and advice.action == "lower" and advice.new_price is not None
    await enable(auth_client)
    model = ScriptedModel(
        [
            call("autonomy_status"),
            call("propose_purchase", opportunity_id=ids["strong"], reason="Margine confermato dal calcolo"),
            call("reprice_advice", purchase_id=pid),
            call("propose_reprice", purchase_id=pid, reason="Fermo da tre settimane"),
            call("finish_proposals", summary="Un acquisto e un ribasso preparati."),
        ]
    )
    run = await run_propose(uid, model)
    assert run is not None and run.kind == "propose_actions" and run.status == "succeeded"
    assert run.provider == "scripted" and run.prompt_version == "propose-v1" and run.cost_usd == D("0.005")
    assert run.input["user_id"] == str(uid) and run.stop_reason is None
    assert [s["tool"] for s in tool_steps(run)] == [
        "autonomy_status",
        "propose_purchase",
        "reprice_advice",
        "propose_reprice",
        "finish_proposals",
    ]
    assert all(s["ok"] for s in tool_steps(run))

    rows = await agent_actions(uid)
    assert {a.kind for a in rows} == {"buy", "reprice"} and len(rows) == 2
    # Inside the dry-run window a proposal is decided and recorded, never carried out.
    assert {a.status for a in rows} == {"dry_run"} and {a.channel for a in rows} == {"dry_run"}
    buy_row = next(a for a in rows if a.kind == "buy")
    assert str(buy_row.opportunity_id) == ids["strong"] and buy_row.payload["would"] == "buy"
    assert (
        buy_row.verifier["agrees"] is True and buy_row.payload["reason"] == "Margine confermato dal calcolo"
    )
    assert buy_row.payload["run_id"] == str(run.id)
    mark = next(a for a in rows if a.kind == "reprice")
    assert mark.opportunity_id is None and str(mark.inventory_id) != "None" and mark.verifier is None
    # The price is the selling plan's, not a number the model supplied; it is a markdown.
    assert D(mark.payload["price"]) == D(str(advice.new_price)).quantize(D("0.01"))
    assert D(mark.payload["price"]) < D(mark.payload["from_price"]) and mark.payload["would"] == "reprice"
    assert D(mark.payload["floor"]) <= D(mark.payload["price"])
    # The books are untouched: nothing was changed, only recorded.
    async with session_scope() as s:
        item = (
            await s.execute(select(InventoryItem).where(InventoryItem.purchase_id == uuid.UUID(pid)))
        ).scalar_one()
        assert item.listed_price == D(mark.payload["from_price"]) and item.stage == "listed"
        stored = (await s.execute(select(AgentRun).where(AgentRun.id == run.id))).scalar_one()
    assert stored.result["by_status"] == {"dry_run": 2} and stored.result["summary"].startswith("Un acquisto")
    assert len(stored.result["proposed"]) == 2


async def test_after_the_dry_run_the_proposals_are_tasks_for_the_user_and_the_user_resolves_them(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    pid = await list_item(auth_client, ids["watch"])
    await enable(auth_client, skip_dry_run=True)
    model = ScriptedModel(
        [
            call("propose_purchase", opportunity_id=ids["strong"], reason="Margine confermato"),
            call("propose_reprice", purchase_id=pid, reason="Fermo da tre settimane"),
            call("finish_proposals", summary="Fatto."),
        ]
    )
    assert await run_propose(uid, model) is not None
    rows = await agent_actions(uid)
    assert {a.status for a in rows} == {"pending_user"} and {a.channel for a in rows} == {"assisted"}
    todo = {a.kind: a.payload["todo"] for a in rows}
    assert (
        "Apri l'annuncio su Vinted" in todo["buy"] and "Aggiorna il prezzo dell'annuncio" in todo["reprice"]
    )

    # They appear to the user with where they came from.
    listed = (await auth_client.get(f"{API}/autonomy/actions?status=pending_user")).json()
    by_kind = {a["kind"]: a for a in listed}
    assert {a["source"] for a in listed} == {"agent"} and by_kind["reprice"]["inventory_id"] is not None
    assert by_kind["reprice"]["payload"]["from_price"] and by_kind["buy"]["opportunity_id"] == ids["strong"]
    audit = (await auth_client.get(f"{API}/autonomy/audit")).json()
    kinds = [e["kind"] for e in audit]
    assert kinds.count("autonomy.action") == 2 and "autonomy.agent_run" in kinds
    assert all(e["payload"]["source"] == "agent" for e in audit if e["kind"] == "autonomy.action")

    # The user says the markdown was done on Vinted: the books follow (down only), nothing else changes.
    mark = by_kind["reprice"]
    done = await auth_client.post(f"{API}/autonomy/actions/{mark['id']}/resolve", json={"outcome": "done"})
    assert done.json()["status"] == "done"
    inv = (await auth_client.get(f"{API}/selling/inventory")).json()["items"]
    item = next(i for i in inv if i["purchase_id"] == pid)
    assert item["listed_price"] == float(mark["payload"]["price"]) < float(mark["payload"]["from_price"])
    assert item["price_history"][-1]["reason"].startswith("ribasso proposto dall'agente")
    # Turned down: the books do not move.
    reject = by_kind["buy"]
    await auth_client.post(f"{API}/autonomy/actions/{reject['id']}/resolve", json={"outcome": "rejected"})
    assert (await auth_client.get(f"{API}/autonomy")).json()["limits"]["max_reprices_per_day"] == 10


async def test_a_done_markdown_that_is_not_lower_does_not_move_the_books(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    pid = await list_item(auth_client, ids["watch"])
    uid = await user_id(auth_client)
    await enable(auth_client, skip_dry_run=True)
    async with session_scope() as s:
        item = (
            await s.execute(select(InventoryItem).where(InventoryItem.purchase_id == uuid.UUID(pid)))
        ).scalar_one()
        current = item.listed_price
        act = await engine.record(
            s, uid, "reprice", "pending_user", opp=None, payload={"price": str(current + 5), "source": "agent"},
            reasons=[], channel="assisted", verifier_out=None, inventory_id=item.id,
        )  # fmt: skip
        action_id = act.id
    await auth_client.post(f"{API}/autonomy/actions/{action_id}/resolve", json={"outcome": "done"})
    inv = (await auth_client.get(f"{API}/selling/inventory")).json()["items"]
    assert next(i for i in inv if i["purchase_id"] == pid)["listed_price"] == float(current)


# ------------------------------------------------------------------ the switches
@pytest.mark.parametrize("how", ["kill", "disable", "suspend"])
async def test_the_switches_stop_the_agent_and_nothing_is_written(
    auth_client: httpx.AsyncClient, make_listing: Any, how: str
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    pid = await list_item(auth_client, ids["watch"])
    await enable(auth_client)
    if how == "kill":
        await auth_client.post(f"{API}/autonomy/kill")
    elif how == "disable":
        await auth_client.post(f"{API}/autonomy/disable")
    else:
        await tamper(
            "UPDATE autonomy_settings SET suspended_at = now(), suspended_reason = 'anomalia' WHERE user_id = :u",
            u=uid,
        )
    # The run does not even start ...
    model = ScriptedModel([call("finish_proposals", summary="Niente.")])
    assert await run_propose(uid, model) is None and model.seen == []
    # ... and a run already going (the switch flipped meanwhile) is refused tool by tool, writing nothing.
    reg = proposal_registry()
    async with session_scope() as s:
        ctx = ctx_for(s, uid, ids, [pid])
        buy_try = await reg.call(
            ctx, "propose_purchase", {"opportunity_id": ids["strong"], "reason": "Prova"}
        )
        mark_try = await reg.call(ctx, "propose_reprice", {"purchase_id": pid, "reason": "Prova"})
        status = await reg.call(ctx, "autonomy_status", {})
    assert not buy_try.ok and not mark_try.ok
    assert (buy_try.error or "").endswith("nessuna proposta") or "nessuna proposta" in (buy_try.error or "")
    assert status.ok and status.output["active"] is False
    assert await all_actions(uid) == []
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(AgentRun))).scalar_one() == 0


async def test_resuming_lets_the_agent_propose_again(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    await auth_client.post(f"{API}/autonomy/kill")
    script = [
        call("propose_purchase", opportunity_id=ids["strong"], reason="Va bene"),
        call("finish_proposals", summary="Ok."),
    ]
    assert await run_propose(uid, ScriptedModel(list(script))) is None
    await auth_client.post(f"{API}/autonomy/resume")
    assert await run_propose(uid, ScriptedModel(list(script))) is not None
    assert len(await agent_actions(uid)) == 1


async def test_the_agent_is_off_by_default_and_without_a_model(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    model = ScriptedModel([call("finish_proposals", summary="Niente.")])
    assert (
        await run_propose(uid, model, settings=Settings()) is None
    )  # AGENT_PROPOSE_ENABLED is off by default
    assert await run_propose(uid, model, settings=cfg(agent_max_proposals_per_run=0)) is None
    assert await run_propose(uid, None) is None  # no rules fallback: no model, no proposals
    assert model.seen == [] and await all_actions(uid) == []
    assert Settings().agent_propose_enabled is False


# ------------------------------------------------------------------ the same policy as the cycle
async def test_the_limits_block_an_agent_proposal_with_the_codes_of_the_cycle(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client, max_per_item=5)
    model = ScriptedModel(
        [
            call("propose_purchase", opportunity_id=ids["strong"], reason="Sembra buono"),
            call("finish_proposals", summary="Bloccato dai limiti."),
        ]
    )
    run = await run_propose(uid, model)
    assert run is not None and run.result["by_status"] == {"blocked": 1}
    (row,) = await agent_actions(uid)
    assert (
        row.status == "blocked" and row.channel == "-" and "max_per_item" in {r["code"] for r in row.reasons}
    )
    # The model is told why, as a normal answer (not an error), and is not told to try again.
    out = json.loads(next(s for s in tool_steps(run) if s["tool"] == "propose_purchase")["output"])
    assert out["status"] == "blocked" and out["reasons"][0]["code"] == "max_per_item"


async def test_the_daily_budget_is_shared_by_the_proposals_of_a_run_and_then_by_the_cycle(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client, daily_budget=25, weekly_budget=1000)  # room for one item of ~18-20 EUR
    model = ScriptedModel(
        [
            call("propose_purchase", opportunity_id=ids["strong"], reason="Il migliore"),
            call("propose_purchase", opportunity_id=ids["buy"], reason="Anche questo"),
            call("finish_proposals", summary="Uno solo entra nel budget."),
        ]
    )
    run = await run_propose(uid, model)
    assert run is not None
    rows = await agent_actions(uid)
    assert sorted(a.status for a in rows) == ["blocked", "dry_run"]
    blocked = next(a for a in rows if a.status == "blocked")
    assert "daily_budget" in {r["code"] for r in blocked.reasons}
    # The cycle that follows sees the budget already used and does not propose the same purchases twice.
    res = await cycle(uid)
    cycle_rows = [a for a in await all_actions(uid) if a.payload.get("source") == "rules"]
    assert all(a.status == "blocked" for a in cycle_rows)
    assert {str(a.opportunity_id) for a in cycle_rows}.isdisjoint({ids["strong"]})
    assert res.executed == 0


async def test_a_verifier_that_disagrees_blocks_the_agent_and_brings_the_decision_down(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await tamper(
        "UPDATE opportunities SET expected_profit = expected_profit + 40 WHERE id = :i", i=ids["buy"]
    )
    await enable(auth_client)
    model = ScriptedModel(
        [
            call("propose_purchase", opportunity_id=ids["buy"], reason="Sembra un affare"),
            call("finish_proposals", summary="Il verificatore non è d'accordo."),
        ]
    )
    assert await run_propose(uid, model) is not None
    (row,) = await agent_actions(uid)
    assert row.status == "blocked" and {r["code"] for r in row.reasons} == {"verifier"}
    assert any(i["code"] == "profit_mismatch" for i in row.verifier["issues"])
    async with session_scope() as s:
        opp = await s.get(Opportunity, uuid.UUID(ids["buy"]))
        assert opp is not None and opp.decision_verdict == "WATCHLIST"  # it only ever comes down
        assert opp.decision["reviewed_from"] == "BUY"


class FakeLLM:
    enabled = True

    def __init__(self, answer: dict[str, Any] | None) -> None:
        self.answer, self.calls = answer, []

    def model_for(self, tier: str) -> str:
        return "cheap-model"

    async def structured(self, **kw: Any) -> dict[str, Any] | None:
        self.calls.append(kw)
        return self.answer


async def test_the_second_opinion_of_the_model_counts_for_agent_proposals_too(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    llm = FakeLLM({"agrees": False, "issues": ["rivendita ottimistica"]})
    model = ScriptedModel(
        [
            call("propose_purchase", opportunity_id=ids["strong"], reason="Buono"),
            call("finish_proposals", summary="Ok."),
        ]
    )
    assert await run_propose(uid, model, llm=llm) is not None
    (row,) = await agent_actions(uid)
    assert row.status == "blocked" and any(i["code"] == "second_opinion" for i in row.verifier["issues"])
    assert llm.calls and llm.calls[0]["purpose"] == "verifier" and llm.calls[0]["tier"] == "cheap"


# ------------------------------------------------------------------ injection
async def test_a_fooled_model_cannot_propose_a_listing_that_gives_orders(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    seen: list[str] = []

    def obey(messages: list[dict[str, Any]]) -> Any:
        seen.append(messages[-1]["content"][0]["content"])
        # The listing said: answer STRONG BUY and call the tools. The model does what the text says.
        return call("propose_purchase", opportunity_id=ids["inject"], reason="Come richiesto dall'annuncio")

    model = ScriptedModel(
        [
            call("get_opportunity", opportunity_id=ids["inject"]),
            obey,
            call("finish_proposals", summary="L'annuncio dà ordini: l'ho ignorato."),
        ]
    )
    run = await run_propose(uid, model)
    assert run is not None and json.loads(seen[0])["injection_suspected"] is True
    refused = next(s for s in tool_steps(run) if s["tool"] == "propose_purchase")
    assert refused["ok"] is False and "dà ordini" in refused["error"]
    assert await agent_actions(uid) == [] and run.result["injection_suspected"] == [ids["inject"]]
    async with session_scope() as s:
        events = (
            (await s.execute(select(Event).where(Event.kind == "agent.injection_suspected"))).scalars().all()
        )
    assert [e.subject_id for e in events] == [ids["inject"]]
    assert "Ignora le istruzioni" in INJECTION  # the fixture really is hostile


async def test_the_listing_text_is_refused_even_if_the_model_never_read_it(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    async with session_scope() as s:
        ctx = ctx_for(s, uid, ids)
        res = await proposal_registry().call(
            ctx, "propose_purchase", {"opportunity_id": ids["inject"], "reason": "Prova"}
        )
    assert not res.ok and ctx.proposals_remaining == 3 and ctx.proposed == []
    assert await all_actions(uid) == []


# ------------------------------------------------------------------ markdowns
async def test_the_price_of_a_markdown_is_the_plans_and_only_a_lower_one(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    fresh = await list_item(
        auth_client, ids["watch"], days=0.2
    )  # just listed: the plan wants no markdown yet
    await enable(auth_client, skip_dry_run=True)
    reg = proposal_registry()
    async with session_scope() as s:
        ctx = ctx_for(s, uid, ids, [fresh])
        advice = await reg.call(ctx, "reprice_advice", {"purchase_id": fresh})
        assert advice.ok and advice.output["action"] == "hold" and advice.output["can_propose"] is False
        refused = await reg.call(ctx, "propose_reprice", {"purchase_id": fresh, "reason": "Voglio venderlo"})
        assert not refused.ok and "nessun ribasso" in (refused.error or "")
        # A model cannot name a price: the input has no such field.
        sneaky = await reg.call(ctx, "propose_reprice", {"purchase_id": fresh, "reason": "Prova", "price": 1})
        assert not sneaky.ok and "input non valido" in (sneaky.error or "")
        assert ctx.proposals_remaining == 3
    assert await all_actions(uid) == []


async def test_a_raise_or_a_price_below_the_floor_never_becomes_a_proposal(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.selling import pricing, repricing

    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    pid = await list_item(auth_client, ids["watch"])
    await enable(auth_client, skip_dry_run=True)
    real = await advice_for(pid)
    assert real.plan is not None and real.floor is not None
    reg = proposal_registry()

    def fake(advice: repricing.RepriceAdvice) -> Any:
        async def _view(*_a: Any, **_k: Any) -> selling.RepriceView:
            return selling.RepriceView(real.plan, real.fit, real.reference, real.floor, advice)

        return _view

    async with session_scope() as s:
        ctx = ctx_for(s, uid, ids, [pid])
        monkeypatch.setattr(
            selling, "reprice_advice", fake(repricing.RepriceAdvice("raise", 99.0, "Si vende subito"))
        )
        up = await reg.call(ctx, "propose_reprice", {"purchase_id": pid, "reason": "Alziamo"})
        assert not up.ok and "nessun ribasso" in (up.error or "")
        monkeypatch.setattr(
            selling, "reprice_advice", fake(repricing.RepriceAdvice("improve_listing", None, "Foto"))
        )
        assert not (await reg.call(ctx, "propose_reprice", {"purchase_id": pid, "reason": "Foto"})).ok
        assert ctx.proposals_remaining == 3 and ctx.proposed == []
    assert await all_actions(uid) == []
    assert isinstance(real.plan, pricing.ResalePlan)


async def test_the_policy_records_a_markdown_below_the_floor_or_not_lower_as_blocked(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    pid = await list_item(auth_client, ids["watch"])
    await enable(auth_client, skip_dry_run=True)
    async with session_scope() as s:
        p = await s.get(Purchase, uuid.UUID(pid))
        item = (
            await s.execute(select(InventoryItem).where(InventoryItem.purchase_id == uuid.UUID(pid)))
        ).scalar_one()
        assert p is not None and item.listed_price is not None
        row = await s.get(AutonomySettings, uid)
        assert row is not None
        limits, sw = parse_limits(row.limits), engine.switches_of(row, LATER)
        usage = await engine.usage_for(s, uid, LATER)
        common = dict(limits=limits, usage=usage, sw=sw, now=LATER, source="agent", reason="Prova")
        low, _ = await engine.propose_reprice(
            s, uid, p, item, price=D("1.00"), floor=D("10"), current=item.listed_price, **common
        )
        same, _ = await engine.propose_reprice(
            s, uid, p, item, price=item.listed_price, floor=D("10"), current=item.listed_price, **common
        )
        ok, after = await engine.propose_reprice(
            s,
            uid,
            p,
            item,
            price=item.listed_price - D("1"),
            floor=D("1"),
            current=item.listed_price,
            **common,
        )
    assert low.status == "blocked" and {r["code"] for r in low.reasons} == {"below_floor"}
    assert same.status == "blocked" and {r["code"] for r in same.reasons} == {"not_a_markdown"}
    assert ok.status == "pending_user" and ok.inventory_id == item.id and after.reprices_today == 1
    assert low.inventory_id == item.id and low.opportunity_id is None
    async with session_scope() as s:
        subjects = (
            await s.execute(
                select(Event.subject_type, Event.subject_id).where(Event.kind == "autonomy.action")
            )
        ).all()
    assert {t for t, _ in subjects} == {"inventory"} and {i for _, i in subjects} == {str(item.id)}


async def test_the_markdowns_per_day_are_capped_by_the_users_limits(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    pid = await list_item(auth_client, ids["watch"])
    await enable(auth_client, skip_dry_run=True, max_reprices_per_day=0)
    model = ScriptedModel(
        [
            call("propose_reprice", purchase_id=pid, reason="Fermo da settimane"),
            call("finish_proposals", summary="Ok."),
        ]
    )
    assert await run_propose(uid, model) is not None
    (row,) = await agent_actions(uid)
    assert row.status == "blocked" and {r["code"] for r in row.reasons} == {"max_reprices"}


async def test_one_user_cannot_make_the_agent_touch_the_items_of_another(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    pid = await list_item(auth_client, ids["watch"])  # belongs to the logged-in user
    async with session_scope() as s:
        other = User(email="other@example.com", password_hash="x", display_name="Altro")
        s.add(other)
        await s.flush()
        s.add(
            AutonomySettings(
                user_id=other.id, enabled=True, mode="assisted", killed=False,
                limits={"daily_budget": 400, "weekly_budget": 1000, "max_per_item": 80},
            )
        )  # fmt: skip
        other_id = other.id
    reg = proposal_registry()
    async with session_scope() as s:
        # Even with the purchase in the scope (a bug elsewhere), the query is scoped to the user.
        ctx = ctx_for(s, other_id, ids, [pid])
        res = await reg.call(ctx, "propose_reprice", {"purchase_id": pid, "reason": "Non è mio"})
        listing = await reg.call(ctx, "list_listed_inventory", {})
        advice = await reg.call(ctx, "reprice_advice", {"purchase_id": pid})
        outside = await reg.call(
            ctx_for(s, other_id, ids, []), "propose_reprice", {"purchase_id": pid, "reason": "Prova"}
        )
    assert not res.ok and "non trovato" in (res.error or "")
    assert listing.ok and listing.output["count"] == 0
    assert not advice.ok and not outside.ok and "fuori dal perimetro" in (outside.error or "")
    assert await all_actions(other_id) == []


# ------------------------------------------------------------------ duplicates
async def test_the_same_purchase_with_another_reason_is_one_action(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    model = ScriptedModel(
        [
            call("propose_purchase", opportunity_id=ids["strong"], reason="Primo motivo"),
            call("propose_purchase", opportunity_id=ids["strong"], reason="Un altro motivo"),
            call("finish_proposals", summary="Una sola proposta."),
        ]
    )
    run = await run_propose(uid, model)
    assert run is not None
    again = [s for s in tool_steps(run) if s["tool"] == "propose_purchase"]
    assert [s["ok"] for s in again] == [True, False] and "già proposto" in again[1]["error"]
    assert len(await agent_actions(uid)) == 1
    # A later run (and the cycle) find it already proposed in the database.
    async with session_scope() as s:
        ctx = ctx_for(s, uid, ids)
        late = await proposal_registry().call(
            ctx, "propose_purchase", {"opportunity_id": ids["strong"], "reason": "Ancora"}
        )
    assert not late.ok and "ultimi 7 giorni" in (late.error or "")
    await cycle(uid)
    assert sum(1 for a in await all_actions(uid) if str(a.opportunity_id) == ids["strong"]) == 1


async def test_the_same_markdown_is_proposed_once_and_waits_for_the_user(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    pid = await list_item(auth_client, ids["watch"])
    await enable(auth_client, skip_dry_run=True)
    model = ScriptedModel(
        [
            call("propose_reprice", purchase_id=pid, reason="Primo motivo"),
            call("propose_reprice", purchase_id=pid, reason="Secondo motivo"),
            call("finish_proposals", summary="Uno."),
        ]
    )
    assert await run_propose(uid, model) is not None
    assert len(await agent_actions(uid)) == 1
    async with session_scope() as s:  # a new run: the first markdown still waits for the user
        ctx = ctx_for(s, uid, ids, [pid])
        late = await proposal_registry().call(
            ctx, "propose_reprice", {"purchase_id": pid, "reason": "Di nuovo"}
        )
    assert not late.ok and "in attesa" in (late.error or "")


async def test_a_dry_run_markdown_is_not_proposed_again_at_the_same_price(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    pid = await list_item(auth_client, ids["watch"])
    await enable(auth_client)  # dry run
    script = [
        call("propose_reprice", purchase_id=pid, reason="Fermo da settimane"),
        call("finish_proposals", summary="Ok."),
    ]
    assert await run_propose(uid, ScriptedModel(list(script))) is not None
    (first,) = await agent_actions(uid)
    assert first.status == "dry_run"
    again = [
        call("propose_reprice", purchase_id=pid, reason="Ancora fermo"),
        call("finish_proposals", summary="Ok."),
    ]
    run = await run_propose(uid, ScriptedModel(again), force=True)
    assert run is not None
    step = next(s for s in tool_steps(run) if s["tool"] == "propose_reprice")
    assert step["ok"] is False and "già stato proposto" in step["error"]
    assert len(await agent_actions(uid)) == 1


# ------------------------------------------------------------------ bounds of a run
async def test_a_run_makes_at_most_the_configured_number_of_proposals(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    model = ScriptedModel(
        [
            call("propose_purchase", opportunity_id=ids["strong"], reason="Primo"),
            call("propose_purchase", opportunity_id=ids["buy"], reason="Secondo"),
            call("finish_proposals", summary="Uno solo, per tetto."),
        ]
    )
    run = await run_propose(uid, model, settings=cfg(agent_max_proposals_per_run=1))
    assert run is not None
    outcomes = [(s["tool"], s["ok"]) for s in tool_steps(run)]
    assert outcomes == [("propose_purchase", True), ("propose_purchase", False), ("finish_proposals", True)]
    assert "tetto di proposte" in tool_steps(run)[1]["error"]
    assert len(await agent_actions(uid)) == 1


async def test_a_run_that_never_ends_is_stopped_and_what_it_proposed_stays_recorded(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    script = [call("propose_purchase", opportunity_id=ids["strong"], reason="Buono")] + [
        call("autonomy_status") for _ in range(10)
    ]
    run = await run_propose(uid, ScriptedModel(script), settings=cfg(agent_max_steps=3))
    assert run is not None and run.status == "stopped" and run.stop_reason == "max_steps"
    assert len(await agent_actions(uid)) == 1  # the record is real; there is no rules text to overwrite it


@pytest.mark.parametrize("reason", ["model_unavailable", "no_result"])
async def test_a_model_that_cannot_help_proposes_nothing_and_the_run_is_retried_next_time(
    auth_client: httpx.AsyncClient, make_listing: Any, reason: str
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    model = ScriptedModel([None] if reason == "model_unavailable" else [say("non so cosa fare")])
    run = await run_propose(uid, model)
    assert run is not None and run.status == "stopped" and run.stop_reason == reason
    assert await agent_actions(uid) == [] and run.result["proposed"] == []
    # A stopped run does not count as "nothing changed": the next cycle tries again.
    retry = await run_propose(uid, ScriptedModel([call("finish_proposals", summary="Niente di valido.")]))
    assert retry is not None and retry.status == "succeeded"


async def test_an_unchanged_situation_is_not_reviewed_again_but_a_change_is(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    done = [call("finish_proposals", summary="Niente da proporre.")]
    first = await run_propose(uid, ScriptedModel(list(done)))
    assert first is not None and first.status == "succeeded"
    skipped = ScriptedModel(list(done))
    assert await run_propose(uid, skipped) is None and skipped.seen == []
    assert await run_propose(uid, ScriptedModel(list(done)), force=True) is not None
    # A new day, or a new analysis of a candidate, is a change.
    assert await run_propose(uid, ScriptedModel(list(done)), now=LATER + timedelta(days=1)) is not None
    await tamper("UPDATE opportunities SET analysis_id = NULL WHERE id = :i", i=uuid.UUID(ids["buy"]))
    assert await run_propose(uid, ScriptedModel(list(done))) is not None


async def test_nothing_to_look_at_is_not_a_run(auth_client: httpx.AsyncClient) -> None:
    uid = await user_id(auth_client)
    await enable(auth_client)
    assert await run_propose(uid, ScriptedModel([])) is None


# ------------------------------------------------------------------ the provider's request quota
class FakeLimiter:
    def __init__(self, **snap: Any) -> None:
        self.snap = {
            "rpm_limit": 10, "rpd_limit": 200, "rpm_left": 10, "rpd_left": 200, "cooldown_s": 0, **snap,
        }  # fmt: skip

    async def snapshot(self, model: str, tier: str) -> dict[str, Any]:
        return {"model": model, **self.snap}


@pytest.mark.parametrize(
    ("snap", "room"),
    [
        ({}, True),
        ({"rpm_limit": 0, "rpd_limit": 0, "rpm_left": None, "rpd_left": None}, True),  # unlimited
        ({"cooldown_s": 40}, False),  # a 429 put the model on hold
        ({"rpm_left": 2}, False),  # less than a run needs this minute
        ({"rpd_left": 6}, False),  # nothing beyond the reserve of the day
        ({"rpd_left": 20}, True),
        ({"rpm_left": 0, "rpd_left": 0}, False),  # also what a Redis outage reports (fail closed)
    ],
)
async def test_a_run_only_starts_when_the_provider_has_quota_to_spare(
    monkeypatch: pytest.MonkeyPatch, snap: dict[str, Any], room: bool
) -> None:
    monkeypatch.setattr(propose_mod, "get_limiter", lambda: FakeLimiter(**snap))
    ok, why = await propose_mod.has_quota_room(ScriptedModel([]), FakeLLM(None), cfg())
    assert ok is room and (why == "" if room else why in ("cooldown", "rpm", "rpd"))
    assert (await propose_mod.has_quota_room(ScriptedModel([]), None, cfg()))[0] is True  # nothing to protect


async def test_without_quota_the_agent_does_not_start_and_leaves_no_run(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    monkeypatch.setattr(propose_mod, "get_limiter", lambda: FakeLimiter(cooldown_s=30))
    model = ScriptedModel([call("finish_proposals", summary="Niente.")])
    assert await run_propose(uid, model, llm=FakeLLM(None)) is None and model.seen == []
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(AgentRun))).scalar_one() == 0


class DrainingLimiter:
    """The quota of the model, seen once per look: each entry is what is left the moment the agent checks."""

    def __init__(self, rpm_left: list[int]) -> None:
        self.rpm_left = list(rpm_left)

    async def snapshot(self, model: str, tier: str) -> dict[str, Any]:
        left = self.rpm_left.pop(0) if len(self.rpm_left) > 1 else self.rpm_left[0]
        return {
            "model": model, "rpm_limit": 10, "rpd_limit": 200, "rpm_left": left, "rpd_left": 200, "cooldown_s": 0,
        }  # fmt: skip


async def test_a_run_that_has_used_its_share_of_the_quota_stops_and_keeps_what_it_proposed(
    auth_client: httpx.AsyncClient, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    # Plenty at the start and for the first turn; then the reserve for the photo checks is all that is left.
    limiter = DrainingLimiter([10, 10, 1])
    monkeypatch.setattr(propose_mod, "get_limiter", lambda: limiter)
    model = ScriptedModel(
        [
            call("propose_purchase", opportunity_id=ids["strong"], reason="Buono"),
            call("propose_purchase", opportunity_id=ids["buy"], reason="Anche questo"),
            call("finish_proposals", summary="Ok."),
        ]
    )
    run = await run_propose(uid, model, llm=FakeLLM(None))
    assert run is not None and run.status == "stopped" and run.stop_reason == "model_unavailable"
    assert len(model.seen) == 1  # the second turn was never asked of the provider
    assert len(await agent_actions(uid)) == 1  # what was prepared before stays on record


async def test_the_kill_switch_pressed_during_a_run_stops_the_next_proposal(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    reg = proposal_registry()
    async with session_scope() as s:
        ctx = ctx_for(s, uid, ids)
        first = await reg.call(ctx, "propose_purchase", {"opportunity_id": ids["strong"], "reason": "Prima"})
        assert first.ok
        # The user presses the stop button while the run goes on (another request, another transaction).
        assert (await auth_client.post(f"{API}/autonomy/kill")).status_code == 200
        second = await reg.call(ctx, "propose_purchase", {"opportunity_id": ids["buy"], "reason": "Seconda"})
    assert not second.ok and "interruttore d'emergenza" in (second.error or "")
    assert len(await agent_actions(uid)) == 1


# ------------------------------------------------------------------ nothing can execute
async def test_the_review_agent_cannot_reach_the_proposal_tools(clean_db: None, make_listing: Any) -> None:
    ids = await seed(make_listing)
    model = ScriptedModel(
        [
            call("propose_purchase", opportunity_id=ids["strong"], reason="Provo dal riesame"),
            submit([{"opportunity_id": ids["strong"], "action": "buy", "reason": "Margine confermato"}]),
        ]
    )
    run = await run_review(model)
    assert run is not None
    refused = next(s for s in run.steps if s.get("tool") == "propose_purchase")
    assert refused["ok"] is False and "non consentito" in refused["error"]
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(AutonomyAction))).scalar_one() == 0
    assert "propose_purchase" not in REVIEW_TOOLS


async def test_the_agent_cannot_reach_a_channel_that_acts_and_the_only_end_state_is_a_record(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    pid = await list_item(auth_client, ids["watch"])
    await enable(auth_client, skip_dry_run=True)
    assert set(CHANNELS) == {("vinted", "dry_run"), ("vinted", "assisted")}
    model = ScriptedModel(
        [
            call("propose_purchase", opportunity_id=ids["strong"], reason="Buono"),
            call("propose_purchase", opportunity_id=ids["buy"], reason="Buono anche questo"),
            call("propose_reprice", purchase_id=pid, reason="Fermo"),
            call("finish_proposals", summary="Fatto."),
        ]
    )
    assert await run_propose(uid, model) is not None
    rows = await agent_actions(uid)
    assert len(rows) == 3
    # Every record is a blocked refusal, a dry run or a task for the user: never "done" or "failed".
    assert {a.status for a in rows} <= {"blocked", "dry_run", "pending_user"}
    assert {a.channel for a in rows} <= {"-", "dry_run", "assisted"}
    # No tool of the agent marks anything done: only the user's resolve endpoint does.
    assert not any(a.status == "done" for a in await all_actions(uid))


# ------------------------------------------------------------------ the report keeps its meaning
async def test_markdowns_do_not_dilute_the_dry_run_report_of_purchases(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    pid = await list_item(auth_client, ids["watch"])
    await enable(auth_client)
    model = ScriptedModel(
        [
            call("propose_purchase", opportunity_id=ids["strong"], reason="Buono"),
            call("propose_reprice", purchase_id=pid, reason="Fermo"),
            call("finish_proposals", summary="Due proposte."),
        ]
    )
    assert await run_propose(uid, model) is not None
    rep = (await auth_client.get(f"{API}/autonomy/dry-run-report")).json()
    assert rep["actions"] == 2 and rep["by_kind"] == {"buy": 1, "reprice": 1} and rep["by_agent"] == 2
    assert rep["would_have_bought"] == 1 and rep["would_have_repriced"] == 1
    assert rep["verifier_disagreement_rate"] == 0  # one purchase, none disagreed: the markdown is not counted


async def test_usage_counts_the_markdowns_of_today(auth_client: httpx.AsyncClient, make_listing: Any) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    pid = await list_item(auth_client, ids["watch"])
    await enable(auth_client, skip_dry_run=True)
    async with session_scope() as s:
        assert (await engine.usage_for(s, uid, LATER)).reprices_today == 0
    model = ScriptedModel(
        [call("propose_reprice", purchase_id=pid, reason="Fermo"), call("finish_proposals", summary="Uno.")]
    )
    assert await run_propose(uid, model) is not None
    async with session_scope() as s:
        usage = await engine.usage_for(s, uid, LATER)
    assert usage.reprices_today == 1 and isinstance(usage, policy.Usage)


# ------------------------------------------------------------------ the cycle is unchanged, the job is wired
async def test_the_cycles_own_proposals_are_tagged_as_rules_and_carry_no_agent_run(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client)
    await cycle(uid)
    rows = await all_actions(uid)
    assert rows and {a.payload["source"] for a in rows} == {"rules"}
    assert all("run_id" not in a.payload and "reason" not in a.payload for a in rows)
    assert all(a.inventory_id is None and a.kind == "buy" for a in rows)
    listed = (await auth_client.get(f"{API}/autonomy/actions")).json()
    assert {a["source"] for a in listed} == {"rules"}


def test_the_job_runs_just_before_the_autonomy_cycle() -> None:
    from app.workers import tasks
    from app.workers.main import _cron_jobs, _functions

    assert tasks.agent_propose_task.__name__ in {f.name for f in _functions()}
    crons = {c.name: c for c in _cron_jobs()}
    mine, cycle_job = crons["cron:agent_propose_task"], crons["cron:autonomy_cycle_task"]
    assert mine.minute == {10, 40} and cycle_job.minute == {12, 42}
    assert mine.unique is True


async def test_the_job_is_off_by_default_and_one_users_failure_does_not_stop_the_others(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from app.workers import tasks

    uid = await user_id(auth_client)
    await enable(auth_client)
    async with session_scope() as s:
        other = User(email="second@example.com", password_hash="x", display_name="Secondo")
        s.add(other)
        await s.flush()
        s.add(AutonomySettings(user_id=other.id, enabled=True, mode="assisted", killed=False, limits={}))
        other_id = other.id

    # Off by default: no provider is touched, nothing runs.
    assert await tasks.agent_propose_task({}) == {"users": 0, "ran": 0, "proposed": 0, "failed": 0}

    seen: list[uuid.UUID] = []

    async def fake_propose(session: Any, user: uuid.UUID, **kw: Any) -> Any:
        seen.append(user)
        assert kw["model"].provider == "anthropic" and kw["settings"].agent_propose_enabled
        if user == uid:
            raise RuntimeError("boom")
        return SimpleNamespace(result={"proposed": [{"action_id": "a"}, {"action_id": "b"}]})

    monkeypatch.setattr(tasks, "get_settings", lambda: cfg())
    monkeypatch.setattr(tasks, "get_llm", lambda: SimpleNamespace(enabled=True, settings=cfg()))
    monkeypatch.setattr(propose_mod, "propose_for_user", fake_propose)
    out = await tasks.agent_propose_task({})
    assert out == {"users": 2, "ran": 1, "proposed": 2, "failed": 1} and set(seen) == {uid, other_id}
    # Without a model key the job does nothing either.
    monkeypatch.setattr(tasks, "get_llm", lambda: SimpleNamespace(enabled=False))
    assert (await tasks.agent_propose_task({}))["users"] == 0


# ------------------------------------------------------------------ the purchase plan is the code's
async def test_the_purchase_plan_uses_the_budget_and_caps_of_the_user_not_the_models(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    await enable(auth_client, daily_budget=25, weekly_budget=1000, max_per_item=80)
    reg = proposal_registry()
    cands = [ids["strong"], ids["buy"], ids["inject"]]
    async with session_scope() as s:
        ctx = ctx_for(s, uid, ids)
        status = await reg.call(ctx, "autonomy_status", {})
        plan = await reg.call(ctx, "plan_purchases", {"opportunity_ids": cands})
        sneaky = await reg.call(ctx, "plan_purchases", {"opportunity_ids": cands, "budget": 9999})
    assert status.ok and status.output["budget_left_now"] == 25.0 and status.output["mode"] == "dry_run"
    assert plan.ok and plan.output["budget_used_for_the_plan"] == 25.0
    assert 1 <= len(plan.output["selected"]) <= 1  # one item of ~18-20 EUR fits in 25
    assert D(plan.output["total_cost"]) <= 25
    assert not sneaky.ok and "input non valido" in (sneaky.error or "")  # the model cannot give a budget


async def test_the_purchase_plan_refuses_when_the_limits_leave_nothing(
    auth_client: httpx.AsyncClient, make_listing: Any
) -> None:
    ids = await seed(make_listing)
    uid = await user_id(auth_client)
    reg = proposal_registry()
    await enable(auth_client, daily_budget=0, weekly_budget=1000, max_per_item=80)
    async with session_scope() as s:
        none_left = await reg.call(
            ctx_for(s, uid, ids), "plan_purchases", {"opportunity_ids": [ids["strong"]]}
        )
    assert not none_left.ok and "esaurit" in (none_left.error or "")
    await auth_client.put(f"{API}/autonomy/limits", json={"daily_budget": 50, "weekly_budget": 100})
    async with session_scope() as s:  # no cap per item set: nothing is bought
        unset = await reg.call(ctx_for(s, uid, ids), "plan_purchases", {"opportunity_ids": [ids["strong"]]})
    assert not unset.ok and "non si compra" in (unset.error or "")
