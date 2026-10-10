"""Autonomy: limits, kill switch, dry run, what the system decided and why."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.autonomy import engine
from app.autonomy.limits import Limits, LimitsError, parse_limits
from app.core.errors import AppError, NotFoundError
from app.db.models import AutonomyAction, Event, InventoryItem
from app.schemas.common import Schema
from app.selling import service as selling

router = APIRouter(prefix="/autonomy", tags=["autonomy"])


class EnableIn(Schema):
    limits: dict[str, Any] = Field(default_factory=dict)
    skip_dry_run: bool = Field(
        default=False, description="Start without the dry-run period (the user's choice)"
    )


def _state(row: Any, now: datetime) -> dict[str, Any]:
    dry = row.dry_run_until is not None and now < row.dry_run_until
    return {
        "enabled": row.enabled,
        "killed": row.killed,
        "suspended": row.suspended_at is not None,
        "suspended_reason": row.suspended_reason,
        "dry_run": row.enabled and dry,
        "dry_run_until": row.dry_run_until.isoformat() if row.dry_run_until else None,
        "mode": "off" if not row.enabled else ("dry_run" if dry else "assisted"),
        "limits": row.limits or Limits().as_dict(),
        "channels": {
            "dry_run": "decide e registra, non esegue",
            "assisted": "prepara l'azione per te: tu la fai su Vinted (FlipFinder non agisce su Vinted)",
        },
    }


@router.get("", response_model=dict[str, Any])
async def status(user: CurrentUser, db: DB) -> dict[str, Any]:
    row = await engine.get_settings_row(db, user.id)
    await db.commit()
    return _state(row, datetime.now(UTC))


@router.put("/enable", response_model=dict[str, Any])
async def enable(body: EnableIn, user: CurrentUser, db: DB) -> dict[str, Any]:
    try:
        row = await engine.enable(db, user.id, body.limits, skip_dry_run=body.skip_dry_run)
    except LimitsError as exc:
        raise AppError(str(exc), code="invalid_limits") from exc
    await db.commit()
    return _state(row, datetime.now(UTC))


@router.put("/limits", response_model=dict[str, Any])
async def set_limits(body: dict[str, Any], user: CurrentUser, db: DB) -> dict[str, Any]:
    try:
        parsed = parse_limits(body)
    except LimitsError as exc:
        raise AppError(str(exc), code="invalid_limits") from exc
    row = await engine.get_settings_row(db, user.id)
    row.limits = parsed.as_dict()
    await db.commit()
    return _state(row, datetime.now(UTC))


@router.post("/disable", response_model=dict[str, Any])
async def disable(user: CurrentUser, db: DB) -> dict[str, Any]:
    row = await engine.disable(db, user.id)
    await db.commit()
    return _state(row, datetime.now(UTC))


@router.post("/kill", response_model=dict[str, Any])
async def kill(user: CurrentUser, db: DB) -> dict[str, Any]:
    """The emergency stop: nothing is proposed or executed until the user resumes."""
    row = await engine.kill(db, user.id)
    await db.commit()
    return _state(row, datetime.now(UTC))


@router.post("/resume", response_model=dict[str, Any])
async def resume(user: CurrentUser, db: DB) -> dict[str, Any]:
    row = await engine.resume(db, user.id)
    await db.commit()
    return _state(row, datetime.now(UTC))


@router.post("/run", response_model=dict[str, Any])
async def run_now(user: CurrentUser, db: DB) -> dict[str, Any]:
    """Run one cycle now (the worker runs it every half hour)."""
    out = await engine.run_cycle(db, user.id)
    await db.commit()
    return out.as_dict()


@router.get("/actions", response_model=list[dict[str, Any]])
async def actions(
    user: CurrentUser, db: DB, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    q = (
        select(AutonomyAction)
        .where(AutonomyAction.user_id == user.id)
        .order_by(AutonomyAction.created_at.desc())
        .limit(min(limit, 200))
    )
    if status:
        q = q.where(AutonomyAction.status == status)
    rows = (await db.execute(q)).scalars().all()
    return [
        {
            "id": str(a.id), "kind": a.kind, "status": a.status, "channel": a.channel, "reasons": a.reasons,
            "opportunity_id": str(a.opportunity_id) if a.opportunity_id else None, "payload": a.payload,
            "inventory_id": str(a.inventory_id) if a.inventory_id else None,
            "source": (a.payload or {}).get("source", "rules"),  # rules (the cycle) | agent
            "verifier": a.verifier, "created_at": a.created_at.isoformat(),
            "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        }
        for a in rows
    ]  # fmt: skip


async def _book_markdown(db: DB, user_id: uuid.UUID, a: AutonomyAction) -> None:
    """The user lowered the price on Vinted: the books follow, so the next advice starts from the new price and
    the same markdown is not proposed again. Only ever downwards, and only for the user's own listed item."""
    item = await db.get(InventoryItem, a.inventory_id) if a.inventory_id else None
    if item is None or item.user_id != user_id or item.stage != "listed" or item.listed_price is None:
        return
    try:
        price = Decimal(str((a.payload or {}).get("price")))
    except InvalidOperation:
        return
    if 0 < price < item.listed_price:
        selling.add_price_event(
            item, price, "ribasso proposto dall'agente, fatto dall'utente", datetime.now(UTC)
        )


class ResolveIn(Schema):
    outcome: str = Field(pattern="^(done|rejected)$")


@router.post("/actions/{action_id}/resolve", response_model=dict[str, Any])
async def resolve(action_id: uuid.UUID, body: ResolveIn, user: CurrentUser, db: DB) -> dict[str, Any]:
    """The user says whether they carried out a task the system prepared (or turned it down)."""
    a = await db.get(AutonomyAction, action_id)
    if a is None or a.user_id != user.id:
        raise NotFoundError("Azione non trovata.")
    if a.status != "pending_user":
        raise AppError("L'azione non è in attesa di te.", code="not_pending")
    a.status = body.outcome
    a.resolved_at = datetime.now(UTC)
    if body.outcome == "done" and a.kind == "reprice":
        await _book_markdown(db, user.id, a)
    await engine.audit(
        db, user.id, "autonomy.resolved", actor="user", subject_type="action", subject_id=a.id,
        payload={"outcome": body.outcome},
    )  # fmt: skip
    await db.commit()
    return {"id": str(a.id), "status": a.status}


@router.get("/audit", response_model=list[dict[str, Any]])
async def audit(user: CurrentUser, db: DB, limit: int = 100) -> list[dict[str, Any]]:
    """The append-only log of what the autonomy did (the database refuses to change or delete it)."""
    rows = (
        (
            await db.execute(
                select(Event)
                .where(Event.kind.like("autonomy.%"), Event.payload["user_id"].astext == str(user.id))
                .order_by(Event.id.desc())
                .limit(min(limit, 500))
            )
        )
        .scalars()
        .all()
    )
    return [
        {"at": e.at.isoformat(), "kind": e.kind, "actor": e.actor, "subject": f"{e.subject_type}:{e.subject_id}", "payload": e.payload}
        for e in rows
    ]  # fmt: skip


@router.get("/dry-run-report", response_model=dict[str, Any])
async def dry_run_report(user: CurrentUser, db: DB) -> dict[str, Any]:
    return await engine.dry_run_report(db, user.id)
