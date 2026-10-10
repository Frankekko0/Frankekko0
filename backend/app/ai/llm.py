"""Thin wrapper around the Anthropic SDK: structured (JSON-schema) answers and tool-use turns.

* Models, effort and timeout come from settings (``AI_MODEL``, ``AI_MODEL_CHEAP``, ``AI_EFFORT``);
  the API key from ``AI_API_KEY`` and is never logged. Calls name a ``tier`` (``cheap`` for volume,
  ``strong`` where the stakes are) and the model follows from it.
* Every call passes through the **budget** (a daily and a monthly cap, see ``app.ai.budget``) and a
  **circuit breaker** (a provider that keeps failing is left alone for a while). Refused or failed
  calls return ``None`` and the caller falls back to its deterministic rules.
* Structured answers are constrained with ``output_config.format`` (JSON schema), so they parse.
* Server-side refusal fallbacks are enabled (``fallbacks="default"``).
* ``stop_reason`` is always checked before reading content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import anthropic

from app.ai.breaker import CircuitBreaker
from app.ai.budget import AiBudget, get_budget
from app.core.config import Settings, get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
Tier = Literal["cheap", "strong"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ModelTurn:
    """One answer of the model in a tool-use conversation."""

    stop_reason: str  # end_turn | tool_use | max_tokens | refusal | other
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The assistant content as plain dicts, to send back unchanged with the tool results.
    blocks: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    cost_usd: Decimal = Decimal("0")


class LLMClient:
    def __init__(
        self,
        settings: Settings | None = None,
        budget: AiBudget | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        key = self.settings.ai_api_key.get_secret_value() if self.settings.ai_api_key else None
        self._client = (
            anthropic.AsyncAnthropic(api_key=key, timeout=self.settings.ai_timeout_seconds, max_retries=2)
            if key
            else None
        )
        self._budget = budget
        self.breaker = breaker or CircuitBreaker(
            self.settings.ai_breaker_failures, self.settings.ai_breaker_cooldown_seconds
        )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def model(self) -> str:
        return self.settings.ai_model

    def model_for(self, tier: Tier) -> str:
        return self.settings.ai_model_cheap if tier == "cheap" else self.settings.ai_model

    @property
    def budget(self) -> AiBudget:
        return self._budget or get_budget()

    # ------------------------------------------------------------------ shared plumbing
    async def _create(
        self, *, tier: Tier, purpose: str, ref: str | None, **request: Any
    ) -> tuple[Any, Decimal] | None:
        """One call through the breaker and the budget; ``(response, cost)`` or ``None``."""
        if self._client is None:
            return None
        if not self.breaker.allow():
            log.warning("llm.breaker_open", purpose=purpose)
            return None
        check = await self.budget.check(purpose)
        if not check.allowed:
            log.warning("llm.budget_stop", purpose=purpose, reason=check.reason)
            return None
        try:
            response = await self._client.beta.messages.create(
                model=self.model_for(tier),
                betas=[FALLBACK_BETA],
                fallbacks="default",
                **request,
            )
        except anthropic.RateLimitError:
            log.warning("llm.rate_limited", purpose=purpose)
            self.breaker.failure()
            return None
        except anthropic.APIStatusError as exc:
            log.warning("llm.api_error", purpose=purpose, status=exc.status_code)
            self.breaker.failure()
            return None
        except anthropic.APIConnectionError:
            log.warning("llm.connection_error", purpose=purpose)
            self.breaker.failure()
            return None
        self.breaker.success()
        usage = response.usage
        try:
            cost = await self.budget.record(
                purpose=purpose,
                model=response.model,
                tier=tier,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                ref=ref,
            )
        except Exception as exc:  # the answer was paid for: keep it, but say the count is off
            log.error("llm.usage_not_recorded", purpose=purpose, error=type(exc).__name__)
            cost = Decimal("0")
        log.info(
            "llm.completed",
            purpose=purpose,
            model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=str(cost),
        )
        return response, cost

    def _effort(self, tier: Tier) -> str:
        return "low" if tier == "cheap" else self.settings.ai_effort

    # ------------------------------------------------------------------ structured answers
    async def structured(
        self,
        *,
        system: str,
        content: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int = 16000,
        purpose: str = "analysis",
        tier: Tier = "strong",
        ref: str | None = None,
    ) -> dict[str, Any] | None:
        done = await self._create(
            tier=tier,
            purpose=purpose,
            ref=ref,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self._effort(tier), "format": {"type": "json_schema", "schema": schema}},
        )
        if done is None:
            return None
        response, _cost = done
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            log.info("llm.refused", purpose=purpose, category=category)
            return None
        if response.stop_reason == "max_tokens":
            log.warning("llm.truncated", purpose=purpose)
            return None
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("llm.invalid_json", purpose=purpose)
            return None
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------------ tool-use turns
    async def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
        purpose: str = "agent",
        tier: Tier = "cheap",
        ref: str | None = None,
    ) -> ModelTurn | None:
        """One turn of a tool-use conversation; ``None`` when the call is refused or fails."""
        done = await self._create(
            tier=tier,
            purpose=purpose,
            ref=ref,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            output_config={"effort": self._effort(tier)},
        )
        if done is None:
            return None
        response, cost = done
        blocks: list[dict[str, Any]] = []
        calls: list[ToolCall] = []
        texts: list[str] = []
        for b in response.content:
            if b.type == "text":
                blocks.append({"type": "text", "text": b.text})
                texts.append(b.text)
            elif b.type == "tool_use":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                calls.append(ToolCall(b.id, b.name, dict(b.input)))
        stop = (
            response.stop_reason
            if response.stop_reason in ("end_turn", "tool_use", "max_tokens", "refusal")
            else "other"
        )
        return ModelTurn(
            stop_reason=stop,
            text="\n".join(texts),
            tool_calls=calls,
            blocks=blocks,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
            cost_usd=cost,
        )


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        if get_settings().ai_provider == "gemini":
            from app.ai.gemini import GeminiClient

            _client = GeminiClient()
        else:
            _client = LLMClient()
    return _client
