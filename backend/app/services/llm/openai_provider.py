"""OpenAI-backed LLM provider.

Uses the official `openai` package, JSON-mode (`response_format`) for
structured outputs, and translates every failure mode into the
project's unified exception hierarchy so the screening / scoring
services can retry uniformly.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.config import Settings
from app.services.llm.provider import LLMProvider
from app.utils import ExternalServiceError, ValidationError, get_logger

logger = get_logger(__name__)


class OpenAILLMProvider(LLMProvider):
    """JSON-mode chat completion via the OpenAI HTTP API."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValidationError("OpenAILLMProvider requires an api_key")
        self.api_key = api_key
        self.default_model = default_model
        self.timeout = timeout
        # Lazy import — openai SDK is heavy and not required for tests.
        import openai  # type: ignore[import-not-found]

        self._client = openai.AsyncOpenAI(api_key=api_key, timeout=timeout)

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any] | None = None,
        model: Optional[str] = None,
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
        messages = [
            {"role": "system", "content": system + schema_hint},
            {"role": "user", "content": user},
        ]
        try:
            response = await self._client.chat.completions.create(
                model=model_name,
                messages=messages,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001 — translate to AppError
            raise ExternalServiceError(
                f"openai call failed: {exc}",
                provider="openai",
                model=model_name,
            ) from exc

        try:
            content = response.choices[0].message.content or "{}"
            payload = json.loads(content)
        except (IndexError, AttributeError, json.JSONDecodeError) as exc:
            raise ValidationError(
                "openai returned non-JSON response",
                provider="openai",
                raw_excerpt=(content[:200] if "content" in locals() else ""),
            ) from exc

        if not isinstance(payload, dict):
            raise ValidationError(
                "openai response was not a JSON object",
                provider="openai",
            )
        return payload


def build_openai_provider(settings: Settings) -> OpenAILLMProvider:
    """Convenience builder — reads config defaults."""
    return OpenAILLMProvider(
        api_key=settings.openai_api_key,
        default_model=settings.openai_model_cheap,
    )


__all__ = ["OpenAILLMProvider", "build_openai_provider"]
