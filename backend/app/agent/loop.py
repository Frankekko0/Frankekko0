"""The agent loop: the model asks for tools, the code runs them, until a result is submitted.

The loop is plain code around a model. It never lets the model run unbounded (step limit, cost
limit, the shared AI budget), never raises on a bad tool call (the error goes back to the model),
and keeps a trace of every step so a decision can be audited and replayed. It does not touch the
database itself: persistence is the caller's job (``app.agent.review``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.agent.model import AgentModel
from app.agent.tools import ToolContext, ToolRegistry

TRACE_TEXT = 400
TRACE_OUTPUT = 600


@dataclass
class AgentOutcome:
    status: str  # succeeded | stopped
    stop_reason: str | None
    result: dict[str, Any] | None
    steps: list[dict[str, Any]] = field(default_factory=list)
    cost: Decimal = Decimal("0")


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "…"


async def run_agent(
    *,
    system: str,
    task: str,
    model: AgentModel,
    registry: ToolRegistry,
    ctx: ToolContext,
    allowed_tools: set[str] | None = None,
    max_steps: int = 8,
    max_cost: Decimal = Decimal("0.10"),
) -> AgentOutcome:
    tools = registry.definitions(allowed_tools)
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    steps: list[dict[str, Any]] = []
    cost = Decimal("0")
    ref = str(ctx.run_id)

    def stopped(reason: str) -> AgentOutcome:
        return AgentOutcome("stopped", reason, None, steps, cost)

    for n in range(1, max_steps + 1):
        if cost >= max_cost:
            return stopped("run_cost_limit")
        turn = await model.turn(system=system, messages=messages, tools=tools, ref=ref)
        if turn is None:
            return stopped("model_unavailable")
        cost += turn.cost_usd
        steps.append(
            {
                "n": n,
                "kind": "model",
                "model": turn.model or model.model_name,
                "stop": turn.stop_reason,
                "text": _cut(turn.text, TRACE_TEXT),
                "tokens": [turn.input_tokens, turn.output_tokens],
                "cost_usd": str(turn.cost_usd),
            }
        )
        if turn.stop_reason == "refusal":
            return stopped("model_refused")
        if turn.stop_reason == "max_tokens":
            return stopped("model_truncated")
        messages.append({"role": "assistant", "content": turn.blocks})
        if not turn.tool_calls:
            return stopped("no_result")

        results: list[dict[str, Any]] = []
        for call in turn.tool_calls:
            error: str | None
            if allowed_tools is not None and call.name not in allowed_tools:
                outcome_ok, body, error, ms, cached = (
                    False,
                    "",
                    f"strumento non consentito: {call.name}",
                    0,
                    False,
                )
            else:
                res = await registry.call(ctx, call.name, call.input)
                outcome_ok, body, error, ms, cached = (
                    res.ok,
                    res.for_model(),
                    res.error,
                    res.duration_ms,
                    res.cached,
                )
            if not outcome_ok and not body:
                body = json.dumps({"error": error}, ensure_ascii=False)
            steps.append(
                {
                    "n": n,
                    "kind": "tool",
                    "tool": call.name,
                    "input": _cut(json.dumps(call.input, ensure_ascii=False, default=str), TRACE_OUTPUT),
                    "ok": outcome_ok,
                    "output": _cut(body, TRACE_OUTPUT),
                    "error": error,
                    "ms": ms,
                    "cached": cached,
                }
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": body,
                    **({} if outcome_ok else {"is_error": True}),
                }
            )
        messages.append({"role": "user", "content": results})
        if ctx.submitted is not None:
            return AgentOutcome("succeeded", None, ctx.submitted, steps, cost)
    return stopped("max_steps")
