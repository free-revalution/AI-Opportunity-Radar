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

Phase 9 (DRY cleanup) — module-level helpers
============================================

Before Phase 9, every Markdown-shaped generator carried its own
copy of:

  * `extract_text_from_llm(raw)` — pull a string out of whatever
    dict/string the provider returns.
  * `extract_title_from_body(body, max_chars)` — grab the first
    non-empty line, strip leading `#`s, optionally truncate with
    an ellipsis.
  * `append_block_if_missing(body, marker, block)` — CTA / footer
    enforcement pattern: if `marker` not already in `body`, append
    `block` at the tail. Used by `wechat_article` (three CTA
    placeholders) and `xiaohongshu_post` (`{{CTA_URL}}`).
  * `ensure_section_placeholders(body, *, image_pattern)` — make
    sure each H2 section has at least one `{{IMAGE_N}}` so the
    distribution layer doesn't have to manually insert images.

These all live at module scope here so the per-generator subclasses
just import and call them — no duplicated logic, no copy-pasted
heuristics drifting between channels.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

from app.services.llm.provider import LLMProvider
from app.utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers — promoted from per-generator duplicates in Phase 9.
# ---------------------------------------------------------------------------
# Standard attribute list fed into the LLM `user` turn across every Markdown
# generator. JSON-format generators (Xianyu) keep their own tighter list
# because the LLM is asked for structured fields, not free-form prose.
OPPORTUNITY_CONTEXT_FIELDS: tuple[str, ...] = (
    "title",
    "summary",
    "target_user",
    "target_customer",
    "market_size",
    "monetization_model",
    "mvp_days",
    "difficulty",
    "china_gap",
    "total_score",
)

RESEARCH_REPORT_FIELDS: tuple[str, ...] = (
    "executive_summary",
    "market_analysis",
    "competition_analysis",
    "china_analysis",
    "monetization_analysis",
    "mvp_analysis",
    "risk_analysis",
    "recommendation",
)


def extract_text_from_llm(raw: Any) -> str:
    """Provider-agnostic text extraction.

    Some LLM providers wrap the response in `{"text": "..."}` or
    `{"markdown": "..."}`, some nest under `content` / `body`, and
    some return a raw string. We accept all four shapes and fall
    back to JSON-fenced dump of the dict if nothing matches — that
    keeps downstream code from blowing up on unexpected shapes
    while still surfacing the model output for debugging.

    Returns
    -------
    str
        A non-empty string guaranteed to be safe to put in a Markdown
        cell. Never returns None.
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("text", "markdown", "content", "body"):
            v = raw.get(key)
            if isinstance(v, str) and v.strip():
                return v
        # Last resort — dump the whole dict as a JSON fenced block so
        # the operator sees the model output rather than a parse error.
        return "```json\n" + json.dumps(raw, ensure_ascii=False, indent=2) + "\n```"
    return str(raw)


def extract_title_from_body(
    body: str,
    *,
    max_chars: Optional[int] = None,
    ellipsis: str = "…",
) -> tuple[str, bool]:
    """Pull the title from the first non-empty line, stripping leading
    `#` characters (markdown heading syntax).

    Parameters
    ----------
    body
        The full markdown body the LLM produced.
    max_chars
        When set, truncate titles longer than this with `ellipsis`
        appended. When None (the default), return the line verbatim.
    ellipsis
        The truncation marker. Defaults to the typographic ellipsis
        `…` so the result stays Chinese-platform-friendly.

    Returns
    -------
    tuple[str, bool]
        ``(title, was_truncated)``. If the body has no non-empty
        lines, returns ``("", False)``.
    """
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            if max_chars is not None and len(stripped) > max_chars:
                return stripped[: max_chars - len(ellipsis)] + ellipsis, True
            return stripped, False
    return "", False


def append_block_if_missing(body: str, marker: str, block: str) -> str:
    """Append `block` to `body` if `marker` is not already present.

    Used to enforce CTA / footer placeholders — we never trust the
    LLM to remember them, so we add them server-side. If the LLM
    already wrote the marker (e.g. echoed `{{WECHAT_QR}}` verbatim),
    we don't duplicate.

    Always strips trailing whitespace before appending and inserts
    exactly one blank line so the block reads cleanly.
    """
    if marker in body:
        return body
    return body.rstrip() + "\n\n" + block.lstrip()


def ensure_section_placeholders(
    body: str,
    *,
    image_pattern: str = r"!\[\s*配图-\d+\s*\]\(\{\{IMAGE_\d+\}\}\)",
    section_pattern: str = r"^##\s",
) -> str:
    """Make sure each H2 section has at least one ``{{IMAGE_N}}``
    placeholder so the distribution layer doesn't have to manually
    insert images.

    Heuristic: if image-placeholder count is already
    ``>= max(1, num_sections - 1)``, return the body unchanged.
    Otherwise insert one ``![配图-N]({{IMAGE_N}})`` after each H2
    until we're caught up.

    Designed to be safe to call on short articles (single section,
    zero placeholders is allowed). Never raises.
    """
    image_re = re.compile(image_pattern)
    section_re = re.compile(section_pattern, re.MULTILINE)

    sections = section_re.findall(body)
    existing = image_re.findall(body)
    if len(existing) >= max(1, len(sections) - 1):
        return body

    lines = body.splitlines()
    out: list[str] = []
    idx = 1
    inserted = len(existing)
    for line in lines:
        out.append(line)
        if line.startswith("## ") and inserted < len(sections):
            out.append(f"![配图-{idx}]({{{{IMAGE_{idx}}}}})")
            idx += 1
            inserted += 1
    return "\n".join(out)


def base_metadata(opportunity: Any) -> dict[str, Any]:
    """The metadata fields every operator-visible channel card
    wants: score / category / market. Generators extend this with
    channel-specific knobs (price, hashtags, char_count)."""
    return {
        "score": float(getattr(opportunity, "total_score", 0.0) or 0.0),
        "category": getattr(opportunity, "category", None),
        "market": getattr(opportunity, "market", None),
    }


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

    # Class-level prompt-field hints — Markdown subclasses use the full
    # `OPPORTUNITY_CONTEXT_FIELDS`. JSON-shape subclasses (Xianyu) can
    # override with a tighter list because the LLM is asked for
    # structured fields, not free-form prose.
    opportunity_context_fields: ClassVar[tuple[str, ...]] = OPPORTUNITY_CONTEXT_FIELDS
    research_report_fields: ClassVar[tuple[str, ...]] = RESEARCH_REPORT_FIELDS

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
        """Per-opportunity context — fed as the LLM 'user' turn.

        Default impl walks `opportunity_context_fields` +
        `research_report_fields` and emits each non-empty value as
        ``"- <field>: <value>"``. Subclasses can still override if
        they want a different layout (e.g. daily_report's
        "## 简短摘要" style)."""
        parts: list[str] = [f"# 机会标题:{opportunity.title}"]
        for attr in self.opportunity_context_fields:
            v = getattr(opportunity, attr, None)
            if v is not None and v != "":
                parts.append(f"- {attr}: {v}")

        if report is not None:
            parts.append("\n# 深度研究素材")
            for k in self.research_report_fields:
                v = getattr(report, k, None)
                if v:
                    parts.append(f"- {k}: {v}")
        return "\n".join(parts).strip()

    def response_schema(self) -> dict[str, Any] | None:
        """JSON schema when `format == "json"`. Markdown generators
        return None and use prompt-based structuring instead.
        """
        return None

    def metadata_from_opportunity(self, opportunity: Any) -> dict[str, Any]:
        """Pick the fields a downstream channel needs without parsing
        the rendered content. Default = the common
        score/category/market block from `base_metadata`."""
        return base_metadata(opportunity)


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
    "OPPORTUNITY_CONTEXT_FIELDS",
    "RESEARCH_REPORT_FIELDS",
    "append_block_if_missing",
    "base_metadata",
    "ensure_section_placeholders",
    "extract_text_from_llm",
    "extract_title_from_body",
    "get_registry",
    "register",
]