"""The learning report on real records: false negatives, drift, probability calibration and the baseline.

Everything is computed from what the database holds. Where there is not enough to measure, the section says
so (``measurable: false``) and gives the reason: there is no estimate in its place. Each evaluation is also
written to the experiment registry, so a hypothesis that was tried and rejected is not run again.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Brand,
    Experiment,
    InventoryItem,
    Listing,
    Opportunity,
    PredictionOutcome,
    Purchase,
)
from app.intelligence import baseline, counterfactual, drift, probcal

WINDOW_DAYS = 7.0
RECENT_DAYS = 14
BASELINE_DAYS = 90


async def false_negatives(session: AsyncSession, min_profit: float, since: datetime) -> dict[str, Any]:
    bought = select(Purchase.opportunity_id).where(Purchase.opportunity_id.is_not(None))
    rows = (
        await session.execute(
            select(Opportunity, Listing)
            .join(Listing, Listing.id == Opportunity.listing_id)
            .where(
                Opportunity.decision_verdict.in_(counterfactual.REJECTED),
                Opportunity.id.not_in(bought),
                Opportunity.analyzed_at >= since,
            )
            .limit(2000)
        )
    ).all()
    items = []
    for o, li in rows:
        sold_after = None
        if li.status == "sold" and li.sold_at is not None:
            sold_after = max(0.0, (li.sold_at - o.analyzed_at).total_seconds() / 86400)
        vetoes = tuple(v["code"] for v in (o.decision or {}).get("vetoes", []) if v.get("binding"))
        items.append(
            counterfactual.Discarded(
                str(o.id), o.decision_verdict or "", float(o.listing_price),
                float(o.expected_profit) if o.expected_profit is not None else None, sold_after,
                float(li.last_active_price or li.price) if sold_after is not None else None, vetoes,
            )
        )  # fmt: skip
    rep = counterfactual.evaluate(items, min_profit, WINDOW_DAYS)
    return {**rep.as_dict(), "window_days": WINDOW_DAYS, "measurable": rep.sold_quickly > 0,
            "definition": "scartato (non comprato) che si è venduto entro la finestra a un prezzo non superiore a quello analizzato, con profitto atteso già sopra il minimo"}  # fmt: skip


async def drift_report(session: AsyncSession, now: datetime) -> dict[str, Any]:
    recent_from, base_from = now - timedelta(days=RECENT_DAYS), now - timedelta(days=BASELINE_DAYS)
    recent = (
        await session.execute(
            select(Listing.price, Listing.brand_id).where(Listing.first_seen_at >= recent_from).limit(5000)
        )
    ).all()
    base = (
        await session.execute(
            select(Listing.price, Listing.brand_id)
            .where(Listing.first_seen_at < recent_from, Listing.first_seen_at >= base_from)
            .limit(5000)
        )
    ).all()
    names = dict((await session.execute(select(Brand.id, Brand.name))).all())

    def mix(rows: Any) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, b in rows:
            k = names.get(b, "—")
            out[k] = out.get(k, 0) + 1
        return out

    alarms = [
        a for a in (
            drift.detect("price", [float(r[0]) for r in base], [float(r[0]) for r in recent], "I prezzi degli annunci"),
            drift.detect_mix("brand_mix", mix(base), mix(recent), "La composizione per marca"),
        ) if a is not None
    ]  # fmt: skip
    return {
        "measurable": len(base) >= drift.MIN_SAMPLE and len(recent) >= drift.MIN_SAMPLE,
        "baseline_n": len(base),
        "recent_n": len(recent),
        "alarms": [a.__dict__ for a in alarms],
        "action": "Un allarme propone una ricalibrazione controllata (sfidante in ombra): nessun parametro cambia da solo."
        if alarms
        else "Nessuna deriva rilevata."
        if len(base) >= drift.MIN_SAMPLE and len(recent) >= drift.MIN_SAMPLE
        else "Pochi annunci per giudicare una deriva.",
    }


async def calibration_report(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    """P(sold within 30 days) forecast at purchase against what happened (sold items, plus items bought more than
    30 days ago and still unsold: counted as 'not sold', so the evaluation is not flattered)."""
    pairs: list[tuple[float, int]] = []
    done = (
        (
            await session.execute(
                select(PredictionOutcome)
                .where(PredictionOutcome.user_id == user_id, PredictionOutcome.predicted_p_sale.is_not(None))
                .order_by(PredictionOutcome.created_at)
            )
        )
        .scalars()
        .all()
    )
    for o in done:
        pairs.append((float(o.predicted_p_sale or 0), 1 if o.actual_days <= 30 else 0))
    open_items = (
        await session.execute(
            select(Purchase, Opportunity)
            .join(Opportunity, Opportunity.id == Purchase.opportunity_id)
            .join(InventoryItem, InventoryItem.purchase_id == Purchase.id)
            .where(
                Purchase.user_id == user_id,
                Opportunity.sale_probability.is_not(None),
                InventoryItem.stage.in_(("to_list", "listed", "unsold")),
                Purchase.purchase_date <= (now - timedelta(days=30)).date(),
            )
        )
    ).all()
    for _, opp in open_items:
        pairs.append((float(opp.sale_probability or 0), 0))
    rep, _model = probcal.evaluate_and_calibrate([p for p, _ in pairs], [y for _, y in pairs])
    return {**rep.__dict__, "measurable": rep.status != "not_enough_data", "pairs": len(pairs)}


async def baseline_report(
    session: AsyncSession, user_id: uuid.UUID, min_profit: float, min_roi: float
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(PredictionOutcome, Purchase, Opportunity)
            .join(Purchase, Purchase.id == PredictionOutcome.purchase_id)
            .outerjoin(Opportunity, Opportunity.id == PredictionOutcome.opportunity_id)
            .where(PredictionOutcome.user_id == user_id)
        )
    ).all()
    cands = []
    for out, p, o in rows:
        seg = f"{p.brand_name or '—'} · {p.category_name or '—'}"
        net = (
            float(o.expected_sale_price) * 0.95
            if o is not None and o.expected_sale_price is not None
            else None
        )
        ai = (out.verdict_at_buy or "") in ("STRONG_BUY", "BUY")
        cands.append(
            baseline.Candidate(
                str(out.id),
                seg,
                float(p.total_cost),
                (float(o.market_median) * 0.95 if o is not None and o.market_median is not None else net),
                ai,
                float(out.actual_profit),
                float(max(1, out.actual_days)),
            )
        )
    res = baseline.compare_by_segment(cands, min_profit, min_roi)
    s = baseline.summary(res)
    return {**s, "segments_detail": [r.__dict__ for r in res], "measurable": s["measurable"] > 0,
            "caveat": "Solo gli articoli comprati hanno un esito reale: il confronto ha un bias di selezione, dichiarato."}  # fmt: skip


def fingerprint(name: str, config: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps([name, config], sort_keys=True, default=str).encode()).hexdigest()


async def register(
    session: AsyncSession,
    user_id: uuid.UUID | None,
    name: str,
    hypothesis: str,
    kind: str,
    config: dict[str, Any],
    status: str,
    result: dict[str, Any],
) -> tuple[Experiment, bool]:
    """Write an evaluation to the registry. Returns (row, already_tried_and_rejected)."""
    fp = fingerprint(name, config)
    prior = (
        await session.execute(
            select(Experiment)
            .where(
                Experiment.fingerprint == fp, Experiment.user_id == user_id, Experiment.status == "rejected"
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    row = Experiment(
        id=uuid.uuid4(), user_id=user_id, name=name, fingerprint=fp, hypothesis=hypothesis, kind=kind, status=status,
        config=config, result=result, ended_at=datetime.now(UTC),
    )  # fmt: skip
    session.add(row)
    await session.flush()
    return row, prior is not None


async def build(
    session: AsyncSession, user_id: uuid.UUID, min_profit: float, min_roi: float, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    report = {
        "generated_at": now.isoformat(),
        "counterfactual": await false_negatives(session, min_profit, now - timedelta(days=BASELINE_DAYS)),
        "drift": await drift_report(session, now),
        "calibration": await calibration_report(session, user_id, now),
        "baseline": await baseline_report(session, user_id, min_profit, min_roi),
    }
    n_out = (
        await session.execute(
            select(func.count()).select_from(PredictionOutcome).where(PredictionOutcome.user_id == user_id)
        )
    ).scalar_one()
    report["closed_sales"] = int(n_out)  # type: ignore[assignment]
    report["note"] = (
        "Nessuna affermazione di superiorità dell'AI sul baseline senza esiti reali per segmento: ciò che non è misurabile è indicato."
    )
    return report


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


async def run_and_register(
    session: AsyncSession, user_id: uuid.UUID, min_profit: float, min_roi: float, now: datetime | None = None
) -> dict[str, Any]:
    """Build the report and record its evaluations; a rejected hypothesis with the same configuration is flagged."""
    report = _plain(await build(session, user_id, min_profit, min_roi, now))
    repeats: list[str] = []
    for name, hypothesis, key, status_when in (
        (
            "ai_vs_baseline",
            "L'AI batte il baseline per profitto per euro al giorno",
            "baseline",
            lambda r: (
                "promoted"
                if r["measurable"] and not r["degraded"]
                else ("rejected" if r["degraded"] else "inconclusive")
            ),
        ),
        (
            "probability_calibration",
            "Le probabilità di vendita sono calibrate",
            "calibration",
            lambda r: "promoted" if r["status"] in ("calibrated_ok", "recalibrated") else "inconclusive",
        ),
        (
            "false_negative_rate",
            "Le soglie non scartano buoni affari",
            "counterfactual",
            lambda r: (
                "inconclusive"
                if not r["measurable"]
                else ("rejected" if (r["rate"] or 0) > 0.3 else "promoted")
            ),
        ),
    ):
        _, again = await register(
            session,
            user_id,
            name,
            hypothesis,
            "evaluation",
            {"closed_sales": report["closed_sales"]},
            status_when(report[key]),
            report[key],
        )
        if again:
            repeats.append(name)
    report["already_rejected_with_same_data"] = repeats
    return report
