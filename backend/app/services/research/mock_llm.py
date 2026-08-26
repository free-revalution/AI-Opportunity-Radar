"""Deterministic LLM mock for the research-engine synthesis call.

Mirrors `MockLLMProvider` in shape, but emits the `ResearchReport`
JSON schema. Determinism: same user prompt → same response, so test
assertions stay stable.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from app.services.llm.provider import LLMProvider
from app.services.research.prompts import RESEARCH_REPORT_SCHEMA


def _sha_seed(text: str) -> int:
    """Stable seed derived from `text` — used for jitter."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _clean_title(user: str) -> str:
    """Pull a short title out of the synthesis user prompt."""
    for line in user.splitlines():
        if line.strip().startswith("title:"):
            return line.split(":", 1)[1].strip()
    return "the opportunity"


class MockResearchLLMProvider(LLMProvider):
    """Returns a complete, well-formed ResearchReport dict."""

    name = "mock-research"

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        response_schema: Optional[dict[str, Any]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> dict[str, Any]:
        if response_schema and response_schema is not RESEARCH_REPORT_SCHEMA:
            # Be tolerant of callers passing a slightly different shape.
            pass
        title = _clean_title(user) or "the opportunity"
        seed = _sha_seed(user)
        # Derive a confidence in [0.55, 0.95] so tests are stable but varied.
        confidence = 0.55 + (seed % 41) / 100.0
        return {
            "executive_summary": (
                f"{title} addresses a real gap in the market. Early signals "
                "from the discussion show strong interest and willingness to pay."
            ),
            "market_analysis": (
                "Public sources describe a fast-growing segment with multiple "
                "adjacent verticals. No exact TAM number is cited in the "
                "supplied excerpts — additional sources required."
            ),
            "competition_analysis": (
                "Several incumbents exist at the high end (Gong, Chorus). "
                "Mid-market and SMB segments remain under-served."
            ),
            "china_analysis": (
                "Local competitors are emerging in Tier-1 cities but the gap "
                "between Chinese and global offerings is shrinking."
            ),
            "monetization_analysis": (
                "Seat-based pricing at $49–$199/seat/month appears dominant; "
                "usage-based tiers are emerging for power users."
            ),
            "mvp_analysis": (
                "An 8-week MVP can ship on top of an LLM API plus a thin CRM "
                "connector. Cold-start is the primary risk."
            ),
            "risk_analysis": (
                "Switching cost is the main barrier to entry; data privacy "
                "is a secondary risk. No specific revenue figure is "
                "supported by the supplied sources."
            ),
            "recommendation": _recommendation_for(seed),
            "confidence": round(confidence, 3),
            "sources": _derive_sources(user, seed),
        }

    async def complete_json_batch(
        self,
        *,
        system: str,
        users: list[str],
        response_schema: Optional[dict[str, Any]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        return [await self.complete_json(system=system, user=u) for u in users]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_VALID_RECS = (
    "strongly_recommend",
    "recommend",
    "watch",
    "not_recommended",
    "insufficient_data",
)


def _recommendation_for(seed: int) -> str:
    return _VALID_RECS[seed % len(_VALID_RECS)]


_URL_RE = re.compile(r"URL:\s*(\S+)", re.IGNORECASE)


def _derive_sources(user: str, seed: int) -> list[dict[str, Any]]:
    """Reuse the URLs mentioned in the prompt as the cited source list.

    Falls back to a few deterministic placeholders when no URLs are
    present (so unit tests can still assert non-empty sources).
    """
    urls = _URL_RE.findall(user or "")
    if not urls:
        urls = [
            f"https://example.com/derived-{seed % 7}",
            f"https://example.com/derived-{(seed + 1) % 7}",
        ]
    out: list[dict[str, Any]] = []
    for u in urls[:5]:
        out.append({"url": u, "title": "", "via_provider": "mock"})
    return out


def _safe_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True)


__all__ = ["MockResearchLLMProvider"]
