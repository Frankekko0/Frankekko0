"""Google Gemini as the model provider (``AI_PROVIDER=gemini``), same interface as ``LLMClient``.

Plain REST (``generateContent``) with ``httpx``: no extra dependency. The Anthropic-shaped inputs the rest of
the app uses (text/image blocks, tool definitions, tool_use/tool_result turns) are translated, and the answer
is translated back, so callers do not change. Budget and breaker are shared with ``LLMClient``. On the free
tier Google may use what you send to improve its products and the limits are low and may change: keep
``AI_VISION_ENABLED=false`` if photos must not leave your server, and set ``AI_PRICE_*`` to 0 for the free tier.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx

from app.ai.limiter import get_limiter
from app.ai.llm import AiDeferred, LLMClient, ModelTurn, Tier, ToolCall, log, retry_after_seconds

BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_DROP = {"additionalProperties", "$schema", "title", "default", "examples"}


def to_gemini_schema(node: Any) -> Any:
    """JSON Schema to the OpenAPI subset Gemini accepts: no additionalProperties, ``["number","null"]`` becomes
    ``nullable``."""
    if isinstance(node, list):
        return [to_gemini_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in _DROP and not (k == "title" and isinstance(v, dict)):
            continue
        if k == "type" and isinstance(v, list):
            kinds = [t for t in v if t != "null"]
            out["type"] = kinds[0] if kinds else "string"
            if "null" in v:
                out["nullable"] = True
        elif k == "enum" and isinstance(v, list):
            out[k] = [str(e) for e in v if e is not None]  # Gemini rejects null inside an enum
            if any(e is None for e in v):
                out["nullable"] = True
        elif k == "properties" and isinstance(v, dict):
            out[k] = {name: to_gemini_schema(sub) for name, sub in v.items()}
        else:
            out[k] = to_gemini_schema(v)
    return out


def _parts(content: Any, names: dict[str, str]) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]
    parts: list[dict[str, Any]] = []
    for b in content:
        t = b.get("type")
        if t == "text":
            parts.append({"text": b["text"]})
        elif t == "image":
            src = b["source"]
            parts.append({"inlineData": {"mimeType": src["media_type"], "data": src["data"]}})
        elif t == "tool_use":
            names[b["id"]] = b["name"]
            parts.append({"functionCall": {"name": b["name"], "args": b.get("input", {})}})
        elif t == "tool_result":
            body = b.get("content")
            if isinstance(body, list):
                body = "".join(x.get("text", "") for x in body if isinstance(x, dict))
            try:
                payload = json.loads(body) if isinstance(body, str) else body
            except json.JSONDecodeError:
                payload = body
            if not isinstance(payload, dict):
                payload = {"result": payload}
            parts.append(
                {"functionResponse": {"name": names.get(b["tool_use_id"], "tool"), "response": payload}}
            )
    return parts


def to_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names: dict[str, str] = {}
    return [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": _parts(m["content"], names)}
        for m in messages
    ]


class GeminiClient(LLMClient):
    @property
    def enabled(self) -> bool:
        return self.settings.ai_api_key is not None

    async def _generate(
        self, *, tier: Tier, purpose: str, ref: str | None, body: dict[str, Any], raise_on_defer: bool = False
    ) -> tuple[dict[str, Any], Decimal] | None:
        def refuse(reason: str, retry_after: float = 0.0) -> None:
            if raise_on_defer:
                raise AiDeferred(reason, retry_after)

        key = self.settings.ai_api_key.get_secret_value() if self.settings.ai_api_key else None
        if not key:
            return None
        if not self.breaker.allow():
            log.warning("llm.breaker_open", purpose=purpose)
            refuse("breaker_open", self.breaker.cooldown)
            return None
        check = await self.budget.check(purpose)
        if not check.allowed:
            log.warning("llm.budget_stop", purpose=purpose, reason=check.reason)
            refuse("budget", 3600.0)
            return None
        model = self.model_for(tier)
        adm = await get_limiter().acquire(model, tier)
        if not adm.allowed:
            log.info("ai.deferred", purpose=purpose, reason=adm.reason, retry_after=adm.retry_after)
            refuse(adm.reason, adm.retry_after)
            return None
        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as http:
                r = await http.post(
                    f"{BASE}/{model}:generateContent", json=body, headers={"x-goog-api-key": key}
                )
            if r.status_code == 429:
                delay = r.headers.get("retry-after")
                if not delay:
                    for d in (r.json().get("error", {}).get("details") or []) if r.content else []:
                        delay = d.get("retryDelay") or delay
                wait = retry_after_seconds(delay)
                log.warning("llm.rate_limited", purpose=purpose, retry_after=wait)
                await get_limiter().penalize(model, wait)  # quota, not an outage: the breaker stays closed
                refuse("rate_limited", wait)
                return None
            if r.status_code >= 500:
                log.warning("llm.api_error", purpose=purpose, status=r.status_code)
                self.breaker.failure()
                refuse("api_error", 60.0)
                return None
            if (
                r.status_code >= 400
            ):  # our request is wrong (400/403/404): retrying will not help, the breaker is not tripped
                log.warning("llm.request_rejected", purpose=purpose, status=r.status_code)
                refuse("rejected", 3600.0)
                return None
            data = r.json()
        except (httpx.HTTPError, ValueError):
            log.warning("llm.connection_error", purpose=purpose)
            self.breaker.failure()
            refuse("connection", 60.0)
            return None
        self.breaker.success()
        u = data.get("usageMetadata", {})
        tin, tout = int(u.get("promptTokenCount", 0)), int(u.get("candidatesTokenCount", 0))
        try:
            cost = await self.budget.record(
                purpose=purpose, model=model, tier=tier, input_tokens=tin, output_tokens=tout, ref=ref
            )
        except Exception as exc:
            log.error("llm.usage_not_recorded", purpose=purpose, error=type(exc).__name__)
            cost = Decimal("0")
        data["_tokens"] = (tin, tout, model)
        return data, cost

    @staticmethod
    def _candidate(data: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        cand = (data.get("candidates") or [{}])[0]
        return (cand.get("content") or {}).get("parts") or [], cand.get("finishReason", "")

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
        raise_on_defer: bool = False,
    ) -> dict[str, Any] | None:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": to_contents([{"role": "user", "content": content}]),
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
                "responseSchema": to_gemini_schema(schema),
            },
        }
        done = await self._generate(
            tier=tier, purpose=purpose, ref=ref, body=body, raise_on_defer=raise_on_defer
        )
        if done is None:
            return None

        def unusable(reason: str) -> None:
            if raise_on_defer:
                raise AiDeferred(reason, 300.0)

        parts, finish = self._candidate(done[0])
        if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            log.info("llm.refused", purpose=purpose, category=finish)
            unusable("refused")
            return None
        if finish == "MAX_TOKENS":
            log.warning("llm.truncated", purpose=purpose)
            unusable("truncated")
            return None
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("llm.invalid_json", purpose=purpose)
            unusable("bad_answer")
            return None
        if not isinstance(data, dict):
            unusable("bad_answer")
            return None
        return data

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
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": to_contents(messages),
            "generationConfig": {"maxOutputTokens": max_tokens},
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": to_gemini_schema(t.get("input_schema", {"type": "object"})),
                        }
                        for t in tools
                    ]
                }
            ],
        }
        done = await self._generate(tier=tier, purpose=purpose, ref=ref, body=body)
        if done is None:
            return None
        data, cost = done
        parts, finish = self._candidate(data)
        blocks: list[dict[str, Any]] = []
        calls: list[ToolCall] = []
        texts: list[str] = []
        for i, p in enumerate(parts):
            if "text" in p and not p.get("thought"):
                blocks.append({"type": "text", "text": p["text"]})
                texts.append(p["text"])
            elif "functionCall" in p:
                fc = p["functionCall"]
                cid = f"call_{len(messages)}_{i}"
                args = dict(fc.get("args") or {})
                blocks.append({"type": "tool_use", "id": cid, "name": fc["name"], "input": args})
                calls.append(ToolCall(cid, fc["name"], args))
        stop = (
            "tool_use"
            if calls
            else "max_tokens"
            if finish == "MAX_TOKENS"
            else "refusal"
            if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST")
            else "end_turn"
            if finish in ("STOP", "")
            else "other"
        )
        tin, tout, model = data["_tokens"]
        return ModelTurn(stop, "\n".join(texts), calls, blocks, tin, tout, model, cost)
