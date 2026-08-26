"""LLM provider abstraction.

A small, async interface so every screen / score / research call goes
through a single boundary. Concrete providers live next to this file:

    * `mock_provider`   — deterministic, offline, used in tests + when
                          `MOCK_EXTERNAL_SERVICES=true`
    * `openai_provider` — wraps the official `openai` SDK in JSON mode
                          (lazy-imported to keep tests fast)

Production callers should rely on `build_llm_provider(settings)` so the
swap is configuration-driven, not code-driven.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, Optional


class LLMProvider(ABC):
    """Async LLM boundary.

    `complete_json` is the only method screens / scorers / research
    need. Providers MUST:

      * raise `app.utils.ExternalServiceError` on transport / auth / 5xx
        failures (so callers can retry uniformly);
      * raise `app.utils.ValidationError` when the model returns
        something that does not match `response_schema`;
      * return a plain dict[str, Any] when the call succeeds.
    """

    name: str = "abstract"

    @abstractmethod
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
        """Send a single chat request, return the parsed JSON object."""

    # Optional convenience — overridable for batched providers.
    async def complete_json_batch(
        self,
        *,
        system: str,
        users: Sequence[str],
        response_schema: dict[str, Any] | None = None,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> list[dict[str, Any]]:
        """Default sequential implementation."""
        return [
            await self.complete_json(
                system=system,
                user=u,
                response_schema=response_schema,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            for u in users
        ]


__all__ = ["LLMProvider"]
