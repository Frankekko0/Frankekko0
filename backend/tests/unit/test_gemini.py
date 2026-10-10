"""Gemini provider: translation of schemas and turns, and a full call against a fake HTTP server."""

import json
from typing import Any

import httpx
import pytest

from app.ai import gemini
from app.ai.gemini import GeminiClient, to_contents, to_gemini_schema
from app.core.config import Settings


def test_schema_is_translated_to_the_gemini_subset() -> None:
    out = to_gemini_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "price": {"type": ["number", "null"]},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["price"],
        }
    )
    assert "additionalProperties" not in out
    assert out["properties"]["price"] == {"type": "number", "nullable": True}
    assert out["required"] == ["price"]


def test_tool_turns_are_translated() -> None:
    contents = to_contents(
        [
            {"role": "user", "content": "ciao"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "c1", "name": "f", "input": {"a": 1}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "c1", "content": '{"ok": true}'}],
            },
        ]
    )
    assert contents[1]["role"] == "model" and contents[1]["parts"][0]["functionCall"]["name"] == "f"
    assert contents[2]["parts"][0]["functionResponse"] == {"name": "f", "response": {"ok": True}}


class _Budget:
    async def check(self, purpose: str) -> Any:
        return type("C", (), {"allowed": True, "reason": None})()

    async def record(self, **kw: Any) -> Any:
        from decimal import Decimal

        return Decimal("0")


async def test_structured_and_converse_against_a_fake_server(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        body = json.loads(req.content)
        if "tools" in body:
            part = {"functionCall": {"name": "lookup", "args": {"q": "x"}}}
        else:
            part = {"text": '{"verdict": "BUY"}'}
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [part]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            },
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        gemini.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    s = Settings(ai_provider="gemini", ai_api_key="k-test", ai_model="gemini-x", ai_model_cheap="gemini-y")
    c = GeminiClient(s, budget=_Budget())  # type: ignore[arg-type]
    assert c.enabled is True
    out = await c.structured(system="s", content=[{"type": "text", "text": "t"}], schema={"type": "object"})
    assert out == {"verdict": "BUY"}
    turn = await c.converse(
        system="s",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "lookup", "input_schema": {"type": "object"}}],
    )
    assert turn is not None and turn.stop_reason == "tool_use" and turn.tool_calls[0].name == "lookup"
    assert seen[0].headers["x-goog-api-key"] == "k-test" and "gemini-x:generateContent" in str(seen[0].url)
    assert "gemini-y:generateContent" in str(seen[1].url)


async def test_429_defers_without_tripping_the_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai import limiter as lim
    from app.ai.llm import AiDeferred

    pen: list[float] = []

    class L:
        async def acquire(self, model: str, tier: str) -> Any:
            return type("A", (), {"allowed": True, "retry_after": 0, "reason": ""})()

        async def penalize(self, model: str, seconds: float) -> None:
            pen.append(seconds)

    monkeypatch.setattr(gemini, "get_limiter", lambda: L())
    monkeypatch.setattr(lim, "get_limiter", lambda: L())

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"details": [{"retryDelay": "12s"}]}})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        gemini.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    c = GeminiClient(Settings(ai_provider="gemini", ai_api_key="k"), budget=_Budget())  # type: ignore[arg-type]
    assert await c.structured(system="s", content=[], schema={"type": "object"}) is None
    with pytest.raises(AiDeferred) as e:
        await c.structured(system="s", content=[], schema={"type": "object"}, raise_on_defer=True)
    assert e.value.reason == "rate_limited" and e.value.retry_after == 12.0
    assert pen == [12.0, 12.0] and c.breaker.allow()  # quota is not an outage


def test_enum_with_null_becomes_nullable() -> None:
    assert to_gemini_schema({"type": "string", "enum": ["a", None]}) == {
        "type": "string",
        "enum": ["a"],
        "nullable": True,
    }
