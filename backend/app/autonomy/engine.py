"""One autonomy cycle for one user: switches, anomalies, candidates, verification, policy, channel, audit."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.events import log_event
from app.autonomy import anomalies, policy, verifier
from app.autonomy.channel import ChannelRefused, UnsupportedPlatform, get_channel
from app.autonomy.limits import Limits, parse_limits
from app.core.logging import get_logger
from app.db.models import (
    AutonomyAction,
    AutonomySettings,
    InventoryItem,
    Listing,
    Opportunity,
    PredictionOutcome,
    Purchase,
    Sale,
)
from app.decision.engine import DecisionVerdict, apply_review_ceiling
from app.intelligence.exposure import Holding
from app.opportunities.queries import price_band
from app.selling.stages import IN_STOCK

log = get_logger(__name__)
CANDIDATES = 10
COUNTED = ("dry_run", "pending_user", "done")  # actions that use up budget, messages and slots


@dataclass
class CycleResult:
    state: str  # ran | disabled | killed | suspended
    proposed: int = 0
    blocked: int = 0
    executed: int = 0  # dry runs and tasks for the user, per the mode
    failed: int = 0
    suspended_for: list[str] = field(default_factory=list)
    actions: list[uuid.UUID] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "proposed": self.proposed,
            "blocked": self.blocked,
            "recorded": self.executed,
            "failed": self.failed,
            "suspended_for": self.suspended_for,
        }


async def audit(
    session: AsyncSession,
    user_id: uuid.UUID,
    kind: str,
    *,
    actor: str = "system",
    subject_type: str | None = None,
    subject_id: object | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append to the audit log, always tagged with the user (the log itself cannot be changed)."""
    await log_event(
        session, kind, actor=actor, subject_type=subject_type, subject_id=subject_id,
        payload={**(payload or {}), "user_id": str(user_id)},
    )  # fmt: skip


# ------------------------------------------------------------------ settings and switches
async def get_settings_row(session: AsyncSession, user_id: uuid.UUID) -> AutonomySettings:
    row = await session.get(AutonomySettings, user_id)
    if row is None:
        row = AutonomySettings(user_id=user_id, enabled=False, mode="dry_run", killed=False, limits={})
        session.add(row)
        await session.flush()
    return row


async def enable(
    session: AsyncSession,
    user_id: uuid.UUID,
    limits: dict[str, Any],
    *,
    skip_dry_run: bool = False,
    now: datetime | None = None,
) -> AutonomySettings:
    now = now or datetime.now(UTC)
    parsed = parse_limits(limits)  # raises on invalid limits before anything changes
    row = await get_settings_row(session, user_id)
    row.limits = parsed.as_dict()
    row.enabled = True
    row.killed = False
    row.suspended_reason = None
    row.suspended_at = None
    row.dry_run_until = None if skip_dry_run else now + timedelta(days=parsed.dry_run_days)
    row.mode = "assisted" if skip_dry_run or parsed.dry_run_days == 0 else "dry_run"
    await audit(
        session, user_id, "autonomy.enabled", actor="user", subject_type="user", subject_id=user_id,
        payload={"limits": row.limits, "dry_run_until": row.dry_run_until.isoformat() if row.dry_run_until else None},
    )  # fmt: skip
    return row


async def kill(
    session: AsyncSession, user_id: uuid.UUID, reason: str = "interruttore d'emergenza"
) -> AutonomySettings:
    row = await get_settings_row(session, user_id)
    row.killed = True
    await audit(
        session,
        user_id,
        "autonomy.killed",
        actor="user",
        subject_type="user",
        subject_id=user_id,
        payload={"reason": reason},
    )
    return row


async def resume(session: AsyncSession, user_id: uuid.UUID) -> AutonomySettings:
    row = await get_settings_row(session, user_id)
    row.killed = False
    row.suspended_reason = None
    row.suspended_at = None
    await audit(session, user_id, "autonomy.resumed", actor="user", subject_type="user", subject_id=user_id)
    return row


async def disable(session: AsyncSession, user_id: uuid.UUID) -> AutonomySettings:
    row = await get_settings_row(session, user_id)
    row.enabled = False
    await audit(session, user_id, "autonomy.disabled", actor="user", subject_type="user", subject_id=user_id)
    return row


def switches_of(row: AutonomySettings, now: datetime) -> policy.Switches:
    return policy.Switches(row.enabled, row.killed, row.suspended_at is not None, row.dry_run_until, now)


# ------------------------------------------------------------------ what has been used
async def usage_for(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> policy.Usage:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=now.weekday())

    async def spent(since: datetime) -> Decimal:
        total = (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(AutonomyAction.payload["cost"].astext.cast(Opportunity.listing_price.type)),
                        0,
                    )
                ).where(
                    AutonomyAction.user_id == user_id,
                    AutonomyAction.kind == "buy",
                    AutonomyAction.status.in_(COUNTED),
                    AutonomyAction.created_at >= since,
                )
            )
        ).scalar_one()
        return Decimal(str(total))

    messages = (
        await session.execute(
            select(func.count())
            .select_from(AutonomyAction)
            .where(
                AutonomyAction.user_id == user_id,
                AutonomyAction.kind == "message",
                AutonomyAction.status.in_(COUNTED),
                AutonomyAction.created_at >= day_start,
            )
        )
    ).scalar_one()
    reprices = (
        await session.execute(
            select(func.count())
            .select_from(AutonomyAction)
            .where(
                AutonomyAction.user_id == user_id,
                AutonomyAction.kind == "reprice",
                AutonomyAction.status.in_(COUNTED),
                AutonomyAction.created_at >= day_start,
            )
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(Purchase, InventoryItem)
            .join(InventoryItem, InventoryItem.purchase_id == Purchase.id)
            .where(Purchase.user_id == user_id, InventoryItem.stage.in_(IN_STOCK))
        )
    ).all()
    pending_buys = (
        await session.execute(
            select(func.count())
            .select_from(AutonomyAction)
            .where(
                AutonomyAction.user_id == user_id,
                AutonomyAction.kind == "buy",
                AutonomyAction.status == "pending_user",
            )
        )
    ).scalar_one()
    holdings = tuple(
        Holding(
            float(p.total_cost),
            (p.brand_name or "").lower() or None,
            (p.category_name or "").lower() or None,
            p.size,
            price_band(float(p.purchase_price)),
        )
        for p, _ in rows
    )
    return policy.Usage(
        await spent(day_start),
        await spent(week_start),
        len(rows) + pending_buys,
        messages,
        holdings,
        reprices,
    )


async def recent_stats(
    session: AsyncSession, user_id: uuid.UUID, now: datetime, limits: Limits
) -> anomalies.RecentStats:
    since = now - timedelta(days=limits.window_days)
    total, failed = (
        await session.execute(
            select(func.count(), func.count().filter(AutonomyAction.status == "failed")).where(
                AutonomyAction.user_id == user_id,
                AutonomyAction.created_at >= since,
                AutonomyAction.status != "blocked",
            )
        )
    ).one()
    profits = (
        (
            await session.execute(
                select(Sale.profit).where(Sale.user_id == user_id, Sale.sale_date >= since.date())
            )
        )
        .scalars()
        .all()
    )
    loss = sum((-p for p in profits if p < 0), Decimal(0))
    errs = (
        (
            await session.execute(
                select(PredictionOutcome.price_error_pct).where(
                    PredictionOutcome.user_id == user_id,
                    PredictionOutcome.created_at >= since,
                    PredictionOutcome.price_error_pct.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    mae = float(sum(abs(float(e)) for e in errs if e is not None) / len(errs)) if errs else None
    return anomalies.RecentStats(int(total), int(failed), loss, mae, len(profits))


# ------------------------------------------------------------------ the cycle
def facts_of(o: Opportunity, li: Listing, verified: bool | None, premortem_done: bool) -> policy.OppFacts:
    return policy.OppFacts(
        verdict=o.decision_verdict or "INSUFFICIENT_EVIDENCE",
        flip=o.flip_score,
        confidence=o.confidence_score,
        risk=o.risk_score,
        brand=(li.brand.slug if li.brand else None),
        category=(li.category.slug if li.category else None),
        size=li.size_normalized,
        price_band=price_band(float(o.listing_price)),
        premortem_done=premortem_done,
        verifier_agrees=verified,
        available=li.status == "active" and o.is_active,
    )


async def record(
    session: AsyncSession, user_id: uuid.UUID, kind: str, status: str, *, opp: Opportunity | None, payload: dict[str, Any],
    reasons: list[dict[str, str]], channel: str, verifier_out: dict[str, Any] | None,
    inventory_id: uuid.UUID | None = None,
) -> AutonomyAction:  # fmt: skip
    a = AutonomyAction(
        id=uuid.uuid4(), user_id=user_id, kind=kind, opportunity_id=opp.id if opp else None, inventory_id=inventory_id,
        payload=payload, status=status, reasons=reasons, channel=channel, verifier=verifier_out,
        resolved_at=datetime.now(UTC) if status in ("blocked", "failed", "dry_run") else None,
    )  # fmt: skip
    session.add(a)
    await session.flush()
    subject = (
        ("opportunity", opp.id) if opp else (("inventory", inventory_id) if inventory_id else (None, None))
    )
    await audit(
        session, user_id, "autonomy.action", actor="agent", subject_type=subject[0], subject_id=subject[1],
        payload={
            "action_id": str(a.id), "kind": kind, "status": status, "reasons": reasons, "channel": channel,
            "source": payload.get("source", "rules"), **({"run_id": payload["run_id"]} if "run_id" in payload else {}),
        },
    )  # fmt: skip
    return a


async def already_proposed(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> set[uuid.UUID]:
    """The opportunities this user was already offered a purchase of in the last week (and did not turn down)."""
    ids = (
        await session.execute(
            select(AutonomyAction.opportunity_id).where(
                AutonomyAction.user_id == user_id, AutonomyAction.kind == "buy",
                AutonomyAction.status.in_(("pending_user", "dry_run", "done")), AutonomyAction.created_at >= now - timedelta(days=7),
            )
        )
    ).scalars().all()  # fmt: skip
    return {i for i in ids if i is not None}


async def _through_channel(
    kind: policy.Kind, mode: str, payload: dict[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    """Hand a decided action to the one channel its mode allows: ``(status, channel name, detail)``."""
    channel_name = "dry_run" if mode == "dry_run" else "assisted"
    try:
        out = await get_channel("vinted", channel_name).execute(kind, payload)
        return out.status, channel_name, out.detail
    except ChannelRefused as exc:
        return "failed", channel_name, {"refused": str(exc)}
    except UnsupportedPlatform as exc:
        return "failed", channel_name, {"unsupported": str(exc)}


def _tagged(
    payload: dict[str, Any], source: str, run_id: uuid.UUID | None, reason: str | None
) -> dict[str, Any]:
    """Who proposed this: the rules of the cycle or the agent (with its run and its reason, as inert text)."""
    payload["source"] = source
    if run_id is not None:
        payload["run_id"] = str(run_id)
    if reason:
        payload["reason"] = reason
    return payload


async def propose_buy(
    session: AsyncSession, user_id: uuid.UUID, opp: Opportunity, li: Listing, *, limits: Limits,
    usage: policy.Usage, sw: policy.Switches, now: datetime, llm: Any | None, source: str = "rules",
    run_id: uuid.UUID | None = None, reason: str | None = None,
) -> tuple[AutonomyAction, policy.Usage]:  # fmt: skip
    """One purchase through the verifier, the policy and the channel, recorded either way.

    The cycle and the agent both come through here, so a proposal meets the same checks whoever made it. Nothing
    is bought: the result is a blocked record, a dry run, or a task for the user. Returns the record and the usage
    updated for what it took (the caller passes that on to the next proposal)."""
    v = verifier.verify(opp, li, now)
    if llm is not None:
        v.second_opinion = await verifier.second_opinion(llm, opp, li)
        if v.second_opinion and not v.second_opinion["agrees"]:
            v.issues.append(
                verifier.Issue(
                    "second_opinion",
                    "Il secondo parere del modello non concorda: " + "; ".join(v.second_opinion["issues"]),
                )
            )
    intel = (opp.decision or {}).get("intelligence") or {}
    pre = intel.get("premortem") or {}
    premortem_done = not pre.get("required") or bool(pre.get("modes"))
    proposed = policy.Proposed(
        policy.Kind.BUY, opp.total_acquisition_cost, facts_of(opp, li, v.agrees, premortem_done)
    )
    verdict = policy.evaluate(proposed, limits, usage, sw)
    payload = _tagged(
        {
            "cost": str(opp.total_acquisition_cost),
            "price": str(opp.listing_price),
            "title": li.title[:120],
            "url": li.url,
        },
        source, run_id, reason,
    )  # fmt: skip
    if not v.agrees and opp.decision:
        # The verifier does not agree: the decision itself comes down to WATCHLIST, with the reason on record.
        why = "; ".join(i.label for i in v.issues if i.blocking)
        opp.decision = apply_review_ceiling(
            opp.decision, DecisionVerdict.WATCHLIST, f"Il verificatore non conferma: {why}", code="verifier"
        )
        opp.decision_verdict = opp.decision["verdict"]
        opp.recommended_action = opp.decision["action"]
    if not verdict.allowed:
        reasons = [{"code": x.code, "label": x.label} for x in verdict.violations]
        blocked = await record(
            session, user_id, "buy", "blocked", opp=opp, payload=payload, reasons=reasons, channel="-",
            verifier_out=v.as_dict(),
        )  # fmt: skip
        return blocked, usage
    status, channel_name, detail = await _through_channel(policy.Kind.BUY, verdict.mode, payload)
    act = await record(
        session, user_id, "buy", status, opp=opp, payload={**payload, **detail}, reasons=[], channel=channel_name,
        verifier_out=v.as_dict(),
    )  # fmt: skip
    if status != "failed":
        facts = facts_of(opp, li, True, True)
        cost = opp.total_acquisition_cost
        usage = replace(
            usage, spent_today=usage.spent_today + cost, spent_week=usage.spent_week + cost,
            owned_items=usage.owned_items + 1,
            holdings=(*usage.holdings, Holding(float(cost), facts.brand, facts.category, li.size_normalized, price_band(float(opp.listing_price)))),
        )  # fmt: skip
    return act, usage


async def propose_reprice(
    session: AsyncSession, user_id: uuid.UUID, purchase: Purchase, item: InventoryItem, *, price: Decimal,
    floor: Decimal, current: Decimal, limits: Limits, usage: policy.Usage, sw: policy.Switches, now: datetime,
    source: str = "rules", run_id: uuid.UUID | None = None, reason: str | None = None,
) -> tuple[AutonomyAction, policy.Usage]:  # fmt: skip
    """A markdown of a listed item through the policy and the channel. The price comes from the selling plan, never
    from a model; it must be below the current asking price and not below the floor. The user changes the price on
    Vinted (or, in a dry run, nobody does): FlipFinder never touches the listing."""
    proposed = policy.Proposed(policy.Kind.REPRICE, price=price, floor=floor, current=current)
    verdict = policy.evaluate(proposed, limits, usage, sw)
    payload = _tagged(
        {
            "title": purchase.title[:120],
            "url": item.listing_url,
            "price": str(price),
            "from_price": str(current),
            "floor": str(floor),
            "purchase_id": str(purchase.id),
        },
        source, run_id, reason,
    )  # fmt: skip
    if not verdict.allowed:
        reasons = [{"code": x.code, "label": x.label} for x in verdict.violations]
        blocked = await record(
            session, user_id, "reprice", "blocked", opp=None, payload=payload, reasons=reasons, channel="-",
            verifier_out=None, inventory_id=item.id,
        )  # fmt: skip
        return blocked, usage
    status, channel_name, detail = await _through_channel(policy.Kind.REPRICE, verdict.mode, payload)
    act = await record(
        session, user_id, "reprice", status, opp=None, payload={**payload, **detail}, reasons=[],
        channel=channel_name, verifier_out=None, inventory_id=item.id,
    )  # fmt: skip
    if status != "failed":
        usage = replace(usage, reprices_today=usage.reprices_today + 1)
    return act, usage


async def run_cycle(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None, llm: Any | None = None
) -> CycleResult:
    now = now or datetime.now(UTC)
    row = await get_settings_row(session, user_id)
    if row.killed:
        return CycleResult("killed")
    if row.suspended_at is not None:
        return CycleResult("suspended", suspended_for=[row.suspended_reason or ""])
    if not row.enabled:
        return CycleResult("disabled")
    limits = parse_limits(row.limits)

    found = anomalies.detect(await recent_stats(session, user_id, now, limits), limits)
    if found:
        row.suspended_at = now
        row.suspended_reason = "; ".join(a.label for a in found)
        await audit(
            session, user_id, "autonomy.suspended", actor="system", subject_type="user", subject_id=user_id,
            payload={"anomalies": [{"code": a.code, "label": a.label} for a in found]},
        )  # fmt: skip
        return CycleResult("suspended", suspended_for=[a.label for a in found])

    result = CycleResult("ran")
    usage = await usage_for(session, user_id, now)
    sw = switches_of(row, now)
    already = await already_proposed(session, user_id, now)
    cands = (
        await session.execute(
            select(Opportunity, Listing)
            .join(Listing, Listing.id == Opportunity.listing_id)
            .where(Opportunity.is_active.is_(True), Opportunity.decision_verdict.in_(("STRONG_BUY", "BUY")))
            .order_by(
                Opportunity.decision_verdict.desc(), Opportunity.risk_adjusted_profit.desc().nulls_last()
            )
            .limit(CANDIDATES * 3)
        )
    ).all()
    taken = 0
    for opp, li in cands:
        if taken >= CANDIDATES:
            break
        if opp.id in already:
            continue
        taken += 1
        result.proposed += 1
        act, usage = await propose_buy(
            session, user_id, opp, li, limits=limits, usage=usage, sw=sw, now=now, llm=llm
        )
        result.actions.append(act.id)
        if act.status == "blocked":
            result.blocked += 1
        elif act.status == "failed":
            result.failed += 1
        else:
            result.executed += 1
    return result


# ------------------------------------------------------------------ reports
async def dry_run_report(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """How reliable the decisions look: what was proposed, what was blocked and why, what happened to the
    listings that would have been bought (still available, or gone)."""
    rows = (
        (await session.execute(select(AutonomyAction).where(AutonomyAction.user_id == user_id)))
        .scalars()
        .all()
    )
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for a in rows:
        by_status[a.status] = by_status.get(a.status, 0) + 1
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
        for r in a.reasons or []:
            reasons[r["code"]] = reasons.get(r["code"], 0) + 1
    would_buy = [a for a in rows if a.status == "dry_run" and a.opportunity_id]
    still = gone = 0
    for a in would_buy:
        opp = await session.get(Opportunity, a.opportunity_id)
        li = await session.get(Listing, opp.listing_id) if opp else None
        if li is not None and li.status == "active":
            still += 1
        else:
            gone += 1
    # Only purchases go through the verifier: markdowns must not dilute its disagreement rate.
    buys = [a for a in rows if a.kind == "buy"]
    verifier_disagreed = sum(1 for a in buys if a.verifier and a.verifier.get("agrees") is False)
    n = len(rows)
    return {
        "actions": n,
        "by_status": by_status,
        "by_kind": by_kind,
        "by_agent": sum(1 for a in rows if (a.payload or {}).get("source") == "agent"),
        "would_have_repriced": sum(1 for a in rows if a.kind == "reprice" and a.status == "dry_run"),
        "blocked_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "would_have_bought": len(would_buy),
        "still_available": still,
        "gone_since": gone,
        "verifier_disagreed": verifier_disagreed,
        "verifier_disagreement_rate": round(verifier_disagreed / len(buys), 3) if buys else None,
        "note": "Nessuna azione registrata: il collaudo non ha ancora dati."
        if not n
        else "Il collaudo mostra cosa avrebbe fatto, non quanto avrebbe guadagnato: servono le vendite reali.",
    }
