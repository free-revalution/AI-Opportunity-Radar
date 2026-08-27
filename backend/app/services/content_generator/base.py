"""ContentGenerator ABC + registry.

A `ContentGenerator` is a single-purpose class that turns one
`Opportunity` (+ its deep-research report) into a piece of sales copy
that's ready to copy-paste into an existing platform. We keep one
class per channel so each generator can hold channel-specific
prompting rules and metadata (price ranges, hashtag conventions,
title-length limits) without conditionals leaking across channels.

The registry pattern means new channels are added by simply
implementing the ABC and calling `register(generator)` from a module
that gets imported at startup. No central switch statement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

from app.services.llm.provider import LLMProvider
from app.utils import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class GeneratedContent:
    """One piece of finished sales copy, ready for distribution.

    `format` is the wire shape — `markdown` for human-readable reports
    and posts, `json` for structured listings (Xianyu). `content` is
    either a Markdown string or a dict; downstream consumers should
    switch on `format` (we never serialise dicts through JSON
    ourselves — the registry returns the raw object).

    `metadata` carries channel-specific knobs (e.g. `{"price_cny":
    49, "hashtags": ["#AI创业", "#海外项目"]}`) so the distribution
    layer can format things per-platform without re-parsing the
    content string.
    """

    opportunity_id: int
    generator: str            # "daily_report" | "xianyu_product" | …
    channel: str              # "feishu" | "xianyu" | "xiaohongshu" | …
    title: str
    format: str               # "markdown" | "json"
    content: str | dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ContentGenerator(ABC):
    """Abstract base for one channel's content writer.

    Subclasses MUST:
      * set `name` (matches the registry key + DB column)
      * set `channel` (matches the notification channel name)
      * set `format` ("markdown" or "json")
      * implement `generate(opportunity, report, llm)` — the only
        method the orchestrator calls.

    The split between `system_prompt` / `user_prompt` /
    `response_schema` is intentional: it keeps prompt-engineering
    changes in the subclass without touching orchestration code.
    """

    # Class-level identifiers — subclasses override these.
    name: ClassVar[str] = ""
    channel: ClassVar[str] = ""
    format: ClassVar[str] = "markdown"  # "markdown" | "json"
    description: ClassVar[str] = ""

    @abstractmethod
    async def generate(
        self,
        *,
        opportunity: Any,           # Opportunity (avoid circular import)
        report: Any | None,         # ResearchReport or None
        llm: LLMProvider,
    ) -> GeneratedContent:
        """Render the sales copy for one opportunity."""

    # ----- helpers subclasses can lean on ----------------------------
    def system_prompt(self) -> str:
        """Channel-level instructions (tone, format rules, audience)."""
        return ""

    def user_prompt(self, *, opportunity: Any, report: Any | None) -> str:
        """Per-opportunity context — fed as the LLM 'user' turn."""
        return ""

    def response_schema(self) -> dict[str, Any] | None:
        """JSON schema when `format == "json"`. Markdown generators
        return None and use prompt-based structuring instead.
        """
        return None

    def metadata_from_opportunity(self, opportunity: Any) -> dict[str, Any]:
        """Pick the fields a downstream channel needs without parsing
        the rendered content. Default = nothing.
        """
        return {}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class ContentRegistry:
    """In-process registry. One instance per app, keyed by generator
    name. The first call to `get_registry()` lazily creates a global
    singleton; subsequent calls return the same instance so
    generators registered at import-time survive.
    """

    def __init__(self) -> None:
        self._generators: dict[str, ContentGenerator] = {}

    def register(self, generator: ContentGenerator) -> None:
        if not generator.name:
            raise ValueError(
                f"{type(generator).__name__}.name must be set before register()"
            )
        if generator.name in self._generators:
            logger.warning(
                "content_generator_overwrite",
                name=generator.name,
                previous=self._generators[generator.name].__class__.__name__,
                new=generator.__class__.__name__,
            )
        self._generators[generator.name] = generator

    def get(self, name: str) -> ContentGenerator:
        try:
            return self._generators[name]
        except KeyError as exc:
            raise KeyError(
                f"no content generator registered under {name!r}; "
                f"available: {sorted(self._generators)}"
            ) from exc

    def list_all(self) -> list[ContentGenerator]:
        return list(self._generators.values())

    def names(self) -> list[str]:
        return sorted(self._generators)

    def __contains__(self, name: str) -> bool:
        return name in self._generators


_global_registry: ContentRegistry | None = None


def get_registry() -> ContentRegistry:
    """Process-wide singleton. Lazy so test code can clear it."""
    global _global_registry
    if _global_registry is None:
        _global_registry = ContentRegistry()
    return _global_registry


def register(generator: ContentGenerator) -> ContentGenerator:
    """Convenience: `register(SomeGenerator())` returns the instance."""
    get_registry().register(generator)
    return generator


__all__ = [
    "ContentGenerator",
    "ContentRegistry",
    "GeneratedContent",
    "get_registry",
    "register",
]