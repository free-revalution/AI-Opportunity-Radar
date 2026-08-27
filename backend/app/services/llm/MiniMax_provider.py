"""MiniMax(MiniMax) LLM provider.

MiniMax exposes an Anthropic-compatible Messages API at
`https://api.minimaxi.com/anthropic` (note the trailing `/anthropic` path
prefix). We reuse the official `anthropic` SDK and only swap `base_url` +
`api_key`. No protocol translation needed.

Default model lineup (overridable via Settings — `MiniMax_model_cheap`,
`MiniMax_model_mid`, `MiniMax_model_strong`):

  * `MiniMax-M3`             — strong, deep research / scoring (newest, 2026-06)
  * `MiniMax-M2.7`           — mid
  * `MiniMax-M2.7-highspeed` — cheap, screening / summary

Note: there is NO `MiniMax-M1` on this endpoint — `M2.7-highspeed` is the
cheapest available tier. See `/v1/models` on the live API for the full list.

The Anthropic Messages API has no native JSON-mode flag (unlike OpenAI),
so we lean on the system prompt to instruct JSON-only output and parse
the response client-side. Any transport / auth / 4xx / 5xx failure is
translated to `ExternalServiceError` so the screening / scoring retry
logic stays uniform across vendors.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.services.llm.provider import LLMProvider
from app.utils import ExternalServiceError, ValidationError, get_logger

logger = get_logger(__name__)


class MiniMaxLLMProvider(LLMProvider):
    """JSON-mode chat completion via MiniMax's Anthropic-compatible API."""

    name = "MiniMax"

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        api_url: str = "https://api.minimaxi.com/anthropic",
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValidationError("MiniMaxLLMProvider requires an api_key")
        self.api_key = api_key
        self.default_model = default_model
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        # Lazy import — anthropic SDK is heavy and not required for tests.
        import anthropic  # type: ignore[import-not-found]

        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=self.api_url,
            timeout=timeout,
        )

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        model_name = model or self.default_model
        schema_hint = ""
        if response_schema:
            schema_hint = (
                "\n\nReturn ONLY a JSON object that matches this schema:\n"
                + json.dumps(response_schema, indent=2)
            )
        # Anthropic has no JSON mode — the system prompt carries the JSON-only
        # contract and we parse the response ourselves. We add a closing brace
        # hint to nudge models that try to wrap the JSON in prose.
        full_system = (
            f"{system}{schema_hint}\n\n"
            "IMPORTANT: Respond with a single JSON object and NOTHING else. "
            "Do not wrap the JSON in markdown fences or prose."
        )
        try:
            # MiniMax's Anthropic-compatible endpoint rejects `temperature` as
            # an unknown keyword (as of 2026-08). The Anthropic SDK passes
            # kwargs verbatim so we omit it; the upstream default behaviour
            # is close enough for screening / scoring.
            response = await self._client.messages.create(
                model=model_name,
                system=full_system,
                messages=[{"role": "user", "content": user}],
                max_tokens=max_tokens,
            )
        except Exception as exc:  # noqa: BLE001 — translate to AppError
            raise ExternalServiceError(
                f"MiniMax call failed: {exc}",
                provider="MiniMax",
                model=model_name,
            ) from exc

        # Anthropic response shape: response.content is a list of blocks;
        # text blocks carry .text. Concatenate text blocks in case the model
        # split the JSON across blocks.
        try:
            text_parts = [
                block.text
                for block in (response.content or [])
                if getattr(block, "type", None) == "text"
            ]
            content = "\n".join(text_parts).strip()
        except AttributeError as exc:
            raise ValidationError(
                "MiniMax response missing 'content'",
                provider="MiniMax",
                raw_excerpt=str(response)[:200],
            ) from exc

        if not content:
            raise ValidationError(
                "MiniMax returned empty content",
                provider="MiniMax",
                model=model_name,
            )

        # Some models wrap JSON in ```json ... ``` fences — strip them.
        if content.startswith("```"):
            content = content.strip("`")
            # Drop the optional leading "json" language tag.
            if content.lower().startswith("json"):
                content = content[4:].lstrip()
            # Trim trailing fence if present.
            if content.endswith("```"):
                content = content[:-3].rstrip()

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                "MiniMax returned non-JSON response",
                provider="MiniMax",
                model=model_name,
                raw_excerpt=content[:200],
            ) from exc

        if not isinstance(payload, dict):
            raise ValidationError(
                "MiniMax response was not a JSON object",
                provider="MiniMax",
                model=model_name,
            )
        return payload


def build_MiniMax_provider(settings: Settings) -> MiniMaxLLMProvider:
    """Convenience builder — reads config defaults."""
    return MiniMaxLLMProvider(
        api_key=settings.MiniMax_api_key,
        default_model=settings.MiniMax_model_strong,
        api_url=settings.MiniMax_api_url,
    )


__all__ = ["MiniMaxLLMProvider", "build_MiniMax_provider"]