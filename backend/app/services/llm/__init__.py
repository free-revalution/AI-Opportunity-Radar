"""LLM provider package."""

from app.services.llm.mock_provider import MockLLMProvider
from app.services.llm.openai_provider import OpenAILLMProvider, build_openai_provider
from app.services.llm.provider import LLMProvider

__all__ = [
    "LLMProvider",
    "MockLLMProvider",
    "OpenAILLMProvider",
    "build_openai_provider",
]


def build_llm_provider(settings, *, prefer: str | None = None):
    """Return the LLM provider appropriate for the runtime.

    Resolution order:

      1. If `MOCK_EXTERNAL_SERVICES=true` (or no provider can be built)
         → `MockLLMProvider`.
      2. Otherwise, prefer the provider named in `prefer` (or
         `settings.llm_default_provider`). The first provider with an
         API key wins.
      3. Fall back to the mock.
    """
    if getattr(settings, "mock_external_services", False):
        return MockLLMProvider()

    chosen = (prefer or settings.llm_default_provider or "").lower()

    providers: list[tuple[str, callable]] = []
    if chosen in {"", "openai"} and settings.openai_api_key:
        providers.append(("openai", lambda: build_openai_provider(settings)))
    if chosen in {"anthropic"} and settings.anthropic_api_key:
        # Hook left for Phase 7 — Anthropic provider not yet built.
        providers.append(("anthropic", lambda: None))
    if chosen in {"gemini"} and settings.gemini_api_key:
        providers.append(("gemini", lambda: None))

    for _name, factory in providers:
        instance = factory()
        if instance is not None:
            return instance

    return MockLLMProvider()
