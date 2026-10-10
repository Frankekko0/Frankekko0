"""Model-written negotiation messages for one user and one opportunity: the cache, the limits and the flow.

``GET /negotiation`` stays deterministic (templates) and never calls the model; it only puts back a draft that was
written earlier for the same figures (``attach``). ``POST /negotiation/draft`` is the one place a model is asked
(``draft``), once per click, behind the feature flag, the cache, the injection check and the daily cap and cooldown.

A draft is stored per (user, opportunity) in ``system_state`` because the figures depend on the user's own economics,
together with a fingerprint of what it was written for. A model that cannot answer, or whose answer fails the guard,
never overwrites a stored draft and never replaces a message already written by the model with a template: the
templates only fill what the model has not (or not yet) written. Nothing here sends anything anywhere.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.events import log_event
from app.agent.guardrails import injection_suspected
from app.core.config import Settings, get_settings
from app.market.state import get_state, set_state
from app.negotiation import guard, writer
from app.negotiation.assistant import NegotiationPlan, ai_block
from app.negotiation.quota import NegotiationQuota


def state_key(user_id: uuid.UUID, opportunity_id: uuid.UUID) -> str:
    """``system_state.key`` is 80 characters: a digest of the pair, never the ids themselves."""
    return "neg:" + hashlib.sha256(f"{user_id}|{opportunity_id}".encode()).hexdigest()[:32]


def _base_ai(llm: Any, settings: Settings, tone: str | None = None) -> dict[str, Any]:
    flag, usable = settings.negotiation_ai_enabled, bool(getattr(llm, "enabled", False))
    return ai_block(
        enabled=flag and usable,
        fallback=None if flag and usable else ("disabled" if not flag else "no_model"),
        provider=settings.ai_provider if flag else None,
        model=settings.ai_model_cheap if flag else None,
        prompt_version=writer.PROMPT_VERSION,
        tone=tone,
    )


def _apply_cached(
    plan: NegotiationPlan, facts: guard.NegotiationFacts, cached: dict[str, Any] | None, fp: str
) -> int:
    """Put the stored model drafts into the plan when they were written for these exact figures; returns how many."""
    if not cached or cached.get("fingerprint") != fp:
        return 0
    meta = cached.get("meta") or {}
    n = 0
    for kind, text in (cached.get("messages") or {}).items():
        if (
            kind in plan.messages
            and isinstance(text, str)
            and (meta.get(kind) or {}).get("source") == "model"
            and guard.amounts_are_the_codes(text, facts)
        ):
            plan.messages[kind] = text
            plan.message_meta[kind] = {"source": "model", "violations": [], "tone": meta[kind].get("tone")}
            n += 1
    if n:
        plan.ai.update(
            used=True,
            cached=True,
            tone=cached.get("tone"),
            provider=cached.get("provider"),
            model=cached.get("model"),
            generated_at=cached.get("generated_at"),
        )
    return n


async def attach(
    db: AsyncSession,
    user_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    plan: NegotiationPlan,
    facts: guard.NegotiationFacts,
    title: str | None,
    llm: Any,
    settings: Settings | None = None,
) -> None:
    """For the read-only GET: say whether the model can be asked, and show a stored draft that is still valid.
    No model call, no write."""
    s = settings or get_settings()
    plan.ai = _base_ai(llm, s)
    if s.negotiation_ai_enabled:
        fp = writer.fingerprint(facts, model=s.ai_model_cheap, title=title)
        _apply_cached(plan, facts, await get_state(db, state_key(user_id, opportunity_id)), fp)


async def draft(
    db: AsyncSession,
    user_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    plan: NegotiationPlan,
    facts: guard.NegotiationFacts,
    title: str | None,
    llm: Any,
    *,
    tone: str,
    regenerate: bool = False,
    settings: Settings | None = None,
    quota: NegotiationQuota | None = None,
) -> None:
    """Ask the model to write the messages of ``plan`` (in place). The caller commits.

    Whatever happens the plan keeps its templates where the model wrote nothing usable, and ``plan.ai.fallback``
    says why (``disabled``, ``no_model``, ``injection``, ``cooldown``, ``daily_cap``, ``rate_limited``,
    ``unavailable``, ``no_answer`` or ``guardrail``)."""
    s = settings or get_settings()
    plan.ai = _base_ai(llm, s, tone)
    if not s.negotiation_ai_enabled:
        return
    key = state_key(user_id, opportunity_id)
    fp = writer.fingerprint(facts, model=s.ai_model_cheap, title=title)
    cached = await get_state(db, key)
    had = _apply_cached(plan, facts, cached, fp)
    if not getattr(llm, "enabled", False):
        return
    if had and not regenerate and cached and cached.get("tone") == tone:
        plan.ai["fallback"] = None
        return  # the same request again: the stored draft answers, no model call

    if injection_suspected(title or ""):
        plan.ai.update(fallback="injection", injection_suspected=True)
        await log_event(
            db,
            "negotiation.injection_suspected",
            actor="system",
            subject_type="opportunity",
            subject_id=opportunity_id,
            payload={"user_id": str(user_id)},
        )
        return

    gate = quota or NegotiationQuota(s)
    grant = await gate.reserve(user_id, opportunity_id)
    if not grant.allowed:
        plan.ai.update(
            fallback="rate_limited" if grant.reason == "redis" else grant.reason,
            retry_after=int(grant.retry_after) or None,
        )
        return

    result = await writer.write_messages(llm, plan, facts, title, ref=str(opportunity_id))
    if result.fallback in ("rate_limited", "unavailable"):
        # the model was not (successfully) called: give the click back
        await gate.release(user_id, opportunity_id)
        retry = int(result.retry_after) if result.retry_after else None
        plan.ai.update(fallback=result.fallback, retry_after=retry)
        return
    now = datetime.now(UTC).isoformat()
    if result.accepted:
        for kind, m in result.meta.items():
            # a draft stored earlier stays over a template
            stored = plan.message_meta.get(kind, {}).get("source") == "model"
            if m["source"] == "model":
                plan.messages[kind] = result.messages[kind]
                plan.message_meta[kind] = {"source": "model", "violations": [], "tone": tone}
            elif not stored:
                plan.message_meta[kind] = {"source": "template", "violations": m["violations"]}
        plan.ai.update(used=True, cached=False, tone=tone, generated_at=now)
        plan.ai.update(provider=s.ai_provider, model=s.ai_model_cheap)
        await set_state(
            db,
            key,
            {
                "fingerprint": fp,
                "tone": tone,
                "provider": s.ai_provider,
                "model": s.ai_model_cheap,
                "prompt_version": writer.PROMPT_VERSION,
                "generated_at": now,
                "messages": {
                    k: plan.messages[k] for k, m in plan.message_meta.items() if m["source"] == "model"
                },
                "meta": {k: m for k, m in plan.message_meta.items() if m["source"] == "model"},
            },
        )
        plan.ai["fallback"] = None  # a message the guard dropped says so in its own ``violations``
    else:
        for kind, m in result.meta.items():
            if plan.message_meta.get(kind, {}).get("source") != "model":
                plan.message_meta[kind] = {"source": "template", "violations": m["violations"]}
        plan.ai["fallback"] = result.fallback
    await log_event(
        db,
        "negotiation.drafted",
        actor="user",
        subject_type="opportunity",
        subject_id=opportunity_id,
        payload={
            "user_id": str(user_id),
            "provider": s.ai_provider,
            "model": s.ai_model_cheap,
            "prompt_version": writer.PROMPT_VERSION,
            "tone": tone,
            "accepted": sorted(k for k, m in result.meta.items() if m["source"] == "model"),
            "rejected": result.rejected,
            "fallback": result.fallback,
        },
    )
