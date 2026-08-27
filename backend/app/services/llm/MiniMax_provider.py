"""MiniMax(MiniMax)LLM provider.

MiniMax's chat completion endpoint is OpenAI-compatible, so this provider
reuses the official `openai` SDK and only swaps `base_url` + `api_key`.
No new dependency.

Default model lineup (overridable via Settings):
  * `MiniMax-M3`     — strong, deep research / scoring
  * `MiniMax-M2`     — mid
  * `MiniMax-M1`     — cheap, screening / summary
  * `MiniMax-Embeddings` — embeddings (separate endpoint, handled in embedding.py)

Contract: every response MUST be a JSON object (else `ValidationError`).
Any transport / auth / 5xx failure is translated to `ExternalServiceError`
so screening / scoring retry logic stays uniform across vendors.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.services.llm.provider import LLMProvider
from app.utils import ExternalServiceError, ValidationError, get_logger

logger = get_logger(__name__)


class MiniMaxLLMProvider(LLMProvider):
    """JSON-mode chat completion via MiniMax's OpenAI-compatible API."""

    name = "MiniMax"

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        api_url: str = "https://api.MiniMax.io/v1",
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValidationError("MiniMaxLLMProvider requires an api_key")
        self.api_key = api_key
        self.default_model = default_model
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        # Lazy import — openai SDK is heavy and not required for tests.
        import openai  # type: ignore[import-not-found]

        self._client = openai.AsyncOpenAI(
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
                f"MiniMax call failed: {exc}",
                provider="MiniMax",
                model=model_name,
            ) from exc

        try:
            content = response.choices[0].message.content or "{}"
            payload = json.loads(content)
        except (IndexError, AttributeError, json.JSONDecodeError) as exc:
            raise ValidationError(
                "MiniMax returned non-JSON response",
                provider="MiniMax",
                raw_excerpt=(content[:200] if "content" in locals() else ""),
            ) from exc

        if not isinstance(payload, dict):
            raise ValidationError(
                "MiniMax response was not a JSON object",
                provider="MiniMax",
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