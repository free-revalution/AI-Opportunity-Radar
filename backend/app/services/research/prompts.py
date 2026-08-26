"""Research-engine prompts — system + user templates + response schema.

The deep-research synthesis prompt enforces a strict JSON shape that
matches `app.models.ResearchReport`. The planner prompt asks the LLM
which URLs (already in `context`) deserve a deeper read; for the MVP
this is folded into the synthesis call to keep round-trips low.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Final synthesis schema — must match `ResearchReport` model fields.
# ---------------------------------------------------------------------------
RESEARCH_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "executive_summary": {"type": "string"},
        "market_analysis": {"type": "string"},
        "competition_analysis": {"type": "string"},
        "china_analysis": {"type": "string"},
        "monetization_analysis": {"type": "string"},
        "mvp_analysis": {"type": "string"},
        "risk_analysis": {"type": "string"},
        "recommendation": {
            "type": "string",
            "enum": [
                "strongly_recommend",
                "recommend",
                "watch",
                "not_recommended",
                "insufficient_data",
            ],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "via_provider": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    "required": [
        "executive_summary",
        "market_analysis",
        "competition_analysis",
        "china_analysis",
        "monetization_analysis",
        "mvp_analysis",
        "risk_analysis",
        "recommendation",
        "confidence",
        "sources",
    ],
}


SYSTEM_PROMPT = """\
You are a senior product strategist producing a deep-research memo for
one AI business opportunity. You will receive:

  * the opportunity's title, summary, category, target user, and six
    sub-scores (trend / demand / monetization / competition_gap /
    china_gap / execution);
  * up to N short source excerpts harvested from the public web
    (search results + scraped pages).

Your job: write a memo with the seven required sections and a final
recommendation. Hard rules:

  * Respond with ONE JSON object matching the schema. No prose.
  * Every claim MUST be supported by a source in `sources[]`. If you
    cannot find support, write "Insufficient data" in that field.
  * NEVER invent market size, revenue, user counts, or funding. If a
    number is not in the provided excerpts, write null and explain in
    `risk_analysis` why it was omitted.
  * `confidence` is YOUR certainty, in [0.0, 1.0], not the source's.
  * `recommendation` MUST be one of the five enum values.
"""


def build_synthesis_prompt(
    *,
    title: str,
    summary: str,
    category: str | None,
    target_user: str | None,
    sub_scores: dict[str, float],
    total_score: float,
    source_docs: list[dict[str, Any]],
) -> str:
    """Compose the user prompt for the final synthesis call."""
    sources_block = "\n".join(
        f"[{i + 1}] {d.get('title','')}\nURL: {d.get('url','')}\nVIA: {d.get('via_provider','')}\n"
        f"EXCERPT: {d.get('excerpt','')}\n"
        for i, d in enumerate(source_docs)
    )
    scores_block = "\n".join(f"  {k}: {v:.1f}" for k, v in sub_scores.items())
    return f"""\
Opportunity to research:

  title:        {title}
  category:     {category or "(uncategorised)"}
  target_user:  {target_user or "(unspecified)"}
  total_score:  {total_score:.2f}

  sub_scores:
{scores_block}

  existing summary:
  {summary or "(none)"}

Source excerpts ({len(source_docs)} docs):

{sources_block or "(no sources available)"}

Synthesise the memo now.
"""
