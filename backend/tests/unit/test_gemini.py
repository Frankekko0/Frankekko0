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
