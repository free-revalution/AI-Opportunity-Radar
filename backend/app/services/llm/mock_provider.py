"""Deterministic LLM mock for tests + offline development.

The mock derives a stable, reproducible response from the prompt text
itself — no random, no API calls. This makes CI runs deterministic
while still exercising the screening pipeline end-to-end.

Strategy:
  * `is_business_relevant` is True when the text contains any of a
    curated set of "business-y" keywords (ai, saas, sales, market,
    launch, growth, …).
  * Sub-scores are derived from word counts and a small hash of the
    title — different inputs produce different scores, but the same
    input always produces the same scores.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from app.services.llm.provider import LLMProvider

_BUSINESS_KEYWORDS = frozenset(
    {
        "ai",
        "saas",
        "automation",
        "launch",
        "growth",
        "sales",
        "market",
        "marketplace",
        "platform",
        "tool",
        "agent",
        "api",
        "startup",
        "founder",
        "indie",
        "customer",
        "users",
        "revenue",
        "monetize",
        "monetization",
        "monetisation",
        "pricing",
        "subscription",
        "b2b",
        "b2c",
        "dashboard",
        "analytics",
        "product",
        "service",
        "enterprise",
        "scale",
        "scaleup",
        "scaling",
        "growth",
    }
)

_RELEVANT_CATEGORIES = (
    "AI SaaS",
    "AI Agent",
    "Developer Tool",
    "Productivity",
    "E-commerce",
    "Marketing",
    "Sales",
    "Content",
    "Education",
    "Healthcare",
    "FinTech",
    "Media",
    "Community",
)


class MockLLMProvider(LLMProvider):
    """Heuristic, deterministic JSON-mode mock."""

    name = "mock"

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
        return _derive(user)


def _derive(user: str) -> dict[str, Any]:
    text = user.lower()
    words = re.findall(r"[a-z][a-z0-9]+", text)
    word_count = len(words)
    matches = [w for w in words if w in _BUSINESS_KEYWORDS]
    keyword_density = (len(matches) / max(word_count, 1)) if words else 0.0

    is_business_relevant = keyword_density >= 0.02 or len(matches) >= 2

    # Stable per-input spread: hash the user text and map into [40, 95].
    seed = int(hashlib.sha256(user.encode("utf-8")).hexdigest()[:8], 16)
    spread = seed % 55  # 0..54
    base = 40 + spread

    trend = _clip(base + int(20 * keyword_density))
    demand = _clip(base + int(15 * (len(matches) / 5.0)))
    monetization = _clip(base - 5 + int(10 * keyword_density))
    competition_gap = _clip(base + ((seed >> 8) % 20))
    china_gap = _clip(base + ((seed >> 16) % 25))
    execution = _clip(base + ((seed >> 24) % 15))

    if not is_business_relevant:
        # Penalise so opportunities scoring <= 50 fall into "watch".
        trend = min(trend, 35)
        demand = min(demand, 35)
        monetization = min(monetization, 30)

    category = _RELEVANT_CATEGORIES[seed % len(_RELEVANT_CATEGORIES)]
    title_match = re.search(r"title:\s*(.+)", user, re.IGNORECASE)
    title_hint = title_match.group(1).strip().rstrip(".")[:80] if title_match else "the topic"

    return {
        "is_business_relevant": is_business_relevant,
        "category": category if is_business_relevant else "Other",
        "problem": (
            f"Operators struggle with {title_hint.lower()}."
            if is_business_relevant
            else "Not enough signal to define a business problem."
        ),
        "potential_business": (
            f"A focused product that addresses {title_hint.lower()} with AI."
            if is_business_relevant
            else "No clear monetisable angle."
        ),
        "trend_strength": trend,
        "demand_strength": demand,
        "monetization_potential": monetization,
        "competition_gap": competition_gap,
        "china_gap": china_gap,
        "execution_feasibility": execution,
        "keywords": list(dict.fromkeys(matches))[:8],
        "confidence": round(min(0.95, 0.4 + keyword_density * 5), 3),
    }


def _clip(value: float) -> int:
    return int(max(0, min(100, value)))


__all__ = ["MockLLMProvider"]
