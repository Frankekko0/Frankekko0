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


# ------------------------------------------------------------------ the photo analysis on Gemini
_SUPPORTED_KEYS = {"type", "properties", "required", "items", "enum", "nullable", "description", "format"}
_TYPES = {"object", "array", "string", "number", "integer", "boolean"}


def _walk(node: Any, path: str = "$") -> list[str]:
    """Everything in a translated schema that Gemini's ``responseSchema`` (an OpenAPI subset) would reject."""
    problems: list[str] = []
    unknown = set(node) - _SUPPORTED_KEYS
    if unknown:
        problems.append(f"{path}: keywords {sorted(unknown)}")
    kind = node.get("type")
    if not isinstance(kind, str) or kind not in _TYPES:  # a list of types, or none, is not accepted
        problems.append(f"{path}: type {kind!r}")
    if "enum" in node and (
        kind != "string" or not node["enum"] or not all(isinstance(e, str) for e in node["enum"])
    ):
        problems.append(f"{path}: enum {node['enum']!r} on {kind}")
    if kind == "object":
        props = node.get("properties")
        if not isinstance(props, dict) or not props:
            problems.append(f"{path}: object without properties")
        else:
            problems += [f"{path}.required {r}" for r in node.get("required", []) if r not in props]
            for name, sub in props.items():  # a property may be called "type" or "enum": names, not keywords
                problems += _walk(sub, f"{path}.{name}")
    if kind == "array":
        problems += (
            _walk(node["items"], f"{path}[]")
            if isinstance(node.get("items"), dict)
            else [f"{path}: no items"]
        )
    return problems


def test_the_whole_vision_schema_translates_to_the_gemini_subset() -> None:
    from app.vision.analyzer import VISION_SCHEMA

    out = to_gemini_schema(VISION_SCHEMA)
    assert _walk(out) == []
    # The things the translator has to rewrite are all there to be rewritten, and came out as nullable fields.
    reason = out["properties"]["photo_checks"]["items"]["properties"]["reason"]
    assert reason == {
        "type": "string",
        "enum": ["sfocata", "tagliata", "troppo lontana", "troppo scura"],
        "nullable": True,
    }
    assert out["properties"]["brand"]["nullable"] is True and out["properties"]["defects"]["items"]["properties"]["box"] == {
        "type": "array", "nullable": True, "items": {"type": "number"}
    }  # fmt: skip
    # A property called "type" (the label's kind) is a property, not a keyword to be rewritten.
    assert out["properties"]["labels"]["items"]["properties"]["type"]["type"] == "string"
    assert "additionalProperties" not in str(out) and "None" not in str(out)


def test_the_walker_catches_what_gemini_rejects() -> None:
    bad = {
        "type": "object",
        "properties": {"a": {"type": ["string", "null"], "enum": ["x", None]}, "b": {"anyOf": []}},
    }
    assert len(_walk(bad)) >= 3


def test_an_inline_gif_is_sent_as_jpeg_and_a_png_as_it_is() -> None:
    import base64

    from tests.photos import jpeg

    gif, png = jpeg(1, (300, 200), "GIF"), jpeg(2, (300, 200), "PNG")
    parts = to_contents(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/gif",
                            "data": base64.b64encode(gif).decode(),
                        },
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(png).decode(),
                        },
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/gif",
                            "data": base64.b64encode(b"junk").decode(),
                        },
                    },
                ],
            }
        ]
    )[0]["parts"]
    assert [p["inlineData"]["mimeType"] for p in parts] == [
        "image/jpeg",
        "image/png",
    ]  # the broken one is dropped
    assert base64.b64decode(parts[0]["inlineData"]["data"])[:2] == b"\xff\xd8"
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == png


def test_gemini_declares_what_it_takes_inline() -> None:
    from app.ai.images import GEMINI_IMAGES

    assert GeminiClient.image_limits is GEMINI_IMAGES
    assert "image/gif" not in GEMINI_IMAGES.accepted and {"image/jpeg", "image/png", "image/webp"} <= set(
        GEMINI_IMAGES.accepted
    )
    assert (
        GEMINI_IMAGES.budget is not None and GEMINI_IMAGES.budget < 20 * 1024 * 1024
    )  # room under the 20 MB cap


REAL_CLIENT = httpx.AsyncClient  # before any test replaces it


async def _body_sent(
    monkeypatch: pytest.MonkeyPatch, model: str, purpose: str, reply: dict[str, Any] | None = None
) -> Any:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(
            200,
            json=reply
            or {
                "candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            },
        )

    monkeypatch.setattr(
        gemini.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setattr(gemini, "get_limiter", lambda: _Open())
    c = GeminiClient(Settings(ai_provider="gemini", ai_api_key="k", ai_model=model), budget=_Budget())  # type: ignore[arg-type]
    await c.structured(system="s", content=[], schema={"type": "object"}, purpose=purpose)
    return seen[0]["generationConfig"]


class _Open:
    async def acquire(self, model: str, tier: str) -> Any:
        return type("A", (), {"allowed": True, "retry_after": 0, "reason": ""})()

    async def penalize(self, model: str, seconds: float) -> None:
        return None


async def test_the_photo_analysis_caps_gemini_thinking_so_a_long_gallery_is_not_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = await _body_sent(monkeypatch, "gemini-2.5-flash", "vision")
    assert cfg["thinkingConfig"] == {"thinkingBudget": gemini.THINKING_BUDGET["vision"]}
    assert cfg["responseMimeType"] == "application/json" and "responseSchema" in cfg
    # Other calls keep the model's own thinking; a model that does not think takes no such field at all.
    assert "thinkingConfig" not in await _body_sent(monkeypatch, "gemini-2.5-flash", "analysis")
    assert "thinkingConfig" not in await _body_sent(monkeypatch, "gemini-2.0-flash", "vision")


async def test_a_blocked_prompt_is_a_refusal_not_a_bad_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai.llm import AiDeferred

    reply = {
        "promptFeedback": {"blockReason": "PROHIBITED_CONTENT"},
        "usageMetadata": {"promptTokenCount": 9},
    }
    monkeypatch.setattr(
        gemini.httpx,
        "AsyncClient",
        lambda **kw: REAL_CLIENT(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=reply)), **kw
        ),
    )
    monkeypatch.setattr(gemini, "get_limiter", lambda: _Open())
    c = GeminiClient(Settings(ai_provider="gemini", ai_api_key="k"), budget=_Budget())  # type: ignore[arg-type]
    assert await c.structured(system="s", content=[], schema={"type": "object"}) is None
    with pytest.raises(AiDeferred) as e:
        await c.structured(system="s", content=[], schema={"type": "object"}, raise_on_defer=True)
    assert e.value.reason == "refused"


def test_a_tool_without_inputs_declares_no_parameters() -> None:
    d = gemini._declaration(
        {"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}
    )
    assert "parameters" not in d
    d2 = gemini._declaration(
        {"name": "t", "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}}}
    )
    assert d2["parameters"]["properties"]["a"] == {"type": "string"}


def test_the_half_open_trial_slot_is_given_back_when_the_call_is_refused() -> None:
    from app.ai.breaker import CircuitBreaker

    t = [0.0]
    b = CircuitBreaker(failures=1, cooldown=60, clock=lambda: t[0])
    b.failure()
    t[0] = 61
    assert b.allow() and not b.allow()  # one trial only
    b.release()
    assert b.allow()
