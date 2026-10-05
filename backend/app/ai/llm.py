"""Thin wrapper around the Anthropic SDK for structured (JSON-schema) responses.

* Model, effort and timeout come from settings (``AI_MODEL``, ``AI_EFFORT``); the API key from
  ``AI_API_KEY`` and is never logged.
* Responses are constrained with ``output_config.format`` (JSON schema), so they parse reliably.
* Server-side refusal fallbacks are enabled (``fallbacks="default"``): if a safety classifier
  declines, the API re-runs the request on Anthropic's recommended fallback model.
* ``stop_reason`` is always checked before reading content; any failure returns ``None`` and the
  caller falls back to the deterministic rule-based implementation.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        key = self.settings.ai_api_key.get_secret_value() if self.settings.ai_api_key else None
        self._client = (
            anthropic.AsyncAnthropic(api_key=key, timeout=self.settings.ai_timeout_seconds, max_retries=2)
            if key
            else None
        )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def model(self) -> str:
        return self.settings.ai_model

    async def structured(
        self,
        *,
        system: str,
        content: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int = 16000,
        purpose: str = "analysis",
    ) -> dict[str, Any] | None:
        if self._client is None:
            return None
        try:
            response = await self._client.beta.messages.create(
                model=self.settings.ai_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],  # type: ignore[typeddict-item]
                output_config={
                    "effort": self.settings.ai_effort,
                    "format": {"type": "json_schema", "schema": schema},
                },  # type: ignore[typeddict-item]
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.RateLimitError:
            log.warning("llm.rate_limited", purpose=purpose)
            return None
        except anthropic.APIStatusError as exc:
            log.warning("llm.api_error", purpose=purpose, status=exc.status_code)
            return None
        except anthropic.APIConnectionError:
            log.warning("llm.connection_error", purpose=purpose)
            return None

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
        log.info(
            "llm.completed",
            purpose=purpose,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return data if isinstance(data, dict) else None


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
