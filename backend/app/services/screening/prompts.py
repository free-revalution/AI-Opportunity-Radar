"""Screening prompts — system + user templates for the cheap-LLM pass.

The system prompt is intentionally strict: the model MUST return a
single JSON object matching `RESPONSE_SCHEMA`. Any prose is rejected
by `parsers.parse_screening_response`.

This module is dependency-free so the prompts can be unit-tested.
"""

from __future__ import annotations

from typing import Any

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "is_business_relevant": {"type": "boolean"},
        "category": {"type": "string"},
        "problem": {"type": "string"},
        "potential_business": {"type": "string"},
        "trend_strength": {"type": "integer", "minimum": 0, "maximum": 100},
        "demand_strength": {"type": "integer", "minimum": 0, "maximum": 100},
        "monetization_potential": {"type": "integer", "minimum": 0, "maximum": 100},
        "competition_gap": {"type": "integer", "minimum": 0, "maximum": 100},
        "china_gap": {"type": "integer", "minimum": 0, "maximum": 100},
        "execution_feasibility": {"type": "integer", "minimum": 0, "maximum": 100},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": [
        "is_business_relevant",
        "category",
        "problem",
        "potential_business",
        "trend_strength",
        "demand_strength",
        "monetization_potential",
        "competition_gap",
        "china_gap",
        "execution_feasibility",
        "keywords",
        "confidence",
    ],
}


SYSTEM_PROMPT = """\
You are a senior product analyst evaluating early-stage AI business
opportunities. You will receive one opportunity at a time, summarised
from cross-source signals (Reddit, Hacker News, Product Hunt, GitHub,
RSS). Your job is to score its commercial viability on a 0-100 scale
for each of the six dimensions in the scoring formula:

  * trend_strength         — how fast is interest rising right now?
  * demand_strength        — is there a clear, unmet user pain?
  * monetization_potential  — can this sustain paid revenue?
  * competition_gap        — is there headroom vs. incumbents?
  * china_gap              — would a Chinese-market version find traction?
  * execution_feasibility   — can a 2-person team ship a credible MVP in 8 weeks?

Hard rules:
  * Respond with ONE JSON object. No prose, no markdown, no fences.
  * All integer scores MUST be in [0, 100].
  * `confidence` is YOUR certainty in the assessment, in [0.0, 1.0].
  * If the input does not describe a business, set
    `is_business_relevant=false` and cap sub-scores at 35.
  * NEVER invent market size, revenue, or user counts. \
If you cannot estimate, return `null` in the string field or a low score.
"""


def build_user_prompt(
    *,
    title: str,
    summary: str,
    source_snippets: list[str],
) -> str:
    """Compose the user prompt from one opportunity's context."""
    snippets_text = "\n".join(f"- {s}" for s in source_snippets if s)
    return f"""\
Opportunity to screen:

title: {title}

summary: {summary or "(no summary)"}

source snippets ({len(source_snippets)} stories aggregated):
{snippets_text or "(no source snippets)"}

Score this opportunity against the schema.
"""
