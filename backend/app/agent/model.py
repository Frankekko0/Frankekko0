"""The model side of the agent loop: the real one (Anthropic) and a scripted one for tests and dry runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from app.ai.llm import LLMClient, ModelTurn, Tier


class AgentModel(Protocol):
    provider: str

    @property
    def model_name(self) -> str | None: ...

    async def turn(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], ref: str | None
    ) -> ModelTurn | None:
        """The next answer, or ``None`` when no call can be made (budget, breaker, provider down)."""
        ...


class AnthropicAgentModel:
    provider = "anthropic"

    def __init__(self, llm: LLMClient, tier: Tier = "cheap") -> None:
        self.llm = llm
        self.tier = tier
        # The label of a run says who answered: this class serves the Gemini client too.
        self.provider = str(getattr(getattr(llm, "settings", None), "ai_provider", "anthropic"))

    @property
    def model_name(self) -> str | None:
        return self.llm.model_for(self.tier)

    async def turn(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], ref: str | None
    ) -> ModelTurn | None:
        return await self.llm.converse(
            system=system, messages=messages, tools=tools, purpose="agent", tier=self.tier, ref=ref
        )


class ScriptedModel:
    """Plays a fixed script of answers. Each item is a ``ModelTurn`` or a function of the messages
    so far returning one (or ``None`` to simulate a refused call). Records what it was asked."""

    provider = "scripted"
    model_name: str | None = "scripted"

    def __init__(
        self, script: list[ModelTurn | Callable[[list[dict[str, Any]]], ModelTurn | None] | None]
    ) -> None:
        self.script = list(script)
        self.seen: list[list[dict[str, Any]]] = []
        self.systems: list[str] = []

    async def turn(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], ref: str | None
    ) -> ModelTurn | None:
        self.seen.append([dict(m) for m in messages])
        self.systems.append(system)
        if not self.script:
            return None
        step = self.script.pop(0)
        return step(messages) if callable(step) else step
