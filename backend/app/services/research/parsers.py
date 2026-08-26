"""Strict parser for LLM research-report responses.

The research LLM call returns JSON matching `RESEARCH_REPORT_SCHEMA`. The
parser is intentionally defensive — we never raise on a missing or weird
field; we coerce, clamp, and record the canonical enum so downstream code
can rely on well-typed values.

Hard rules:
  * Every text section is plain UTF-8 string, trimmed, max 8000 chars.
  * `recommendation` MUST be one of the five canonical enums; anything
    else falls back to `"insufficient_data"`.
  * `confidence` is clipped to [0.0, 1.0].
  * `sources` is deduped by URL and capped at 20 entries.
"""

from __future__ import annotations

from typing import Any

from app.services.research.prompts import RESEARCH_REPORT_SCHEMA


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_RECOMMENDATIONS: tuple[str, ...] = (
    "strongly_recommend",
    "recommend",
    "watch",
    "not_recommended",
    "insufficient_data",
)

TEXT_FIELDS: tuple[str, ...] = (
    "executive_summary",
    "market_analysis",
    "competition_analysis",
    "china_analysis",
    "monetization_analysis",
    "mvp_analysis",
    "risk_analysis",
)

MAX_TEXT_LEN = 8_000
MAX_SOURCES = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _as_text(value: Any, *, default: str = "") -> str:
    """Coerce `value` to a bounded UTF-8 string."""
    if value is None:
        return default
    if not isinstance(value, str):
        value = str(value)
    cleaned = value.strip()
    if len(cleaned) > MAX_TEXT_LEN:
        cleaned = cleaned[:MAX_TEXT_LEN]
    return cleaned


def _as_recommendation(value: Any) -> str:
    """Normalize to a canonical enum value or fall back to default."""
    if isinstance(value, str):
        candidate = value.strip().lower().replace("-", "_").replace(" ", "_")
        if candidate in VALID_RECOMMENDATIONS:
            return candidate
        # Light fuzzy matching — accept common variants.
        aliases = {
            "strong": "strongly_recommend",
            "strong_yes": "strongly_recommend",
            "yes": "recommend",
            "maybe": "watch",
            "no": "not_recommended",
            "unknown": "insufficient_data",
            "": "insufficient_data",
        }
        if candidate in aliases:
            return aliases[candidate]
    return "insufficient_data"


def _as_confidence(value: Any) -> float:
    """Clip confidence to [0.0, 1.0]."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
        return 0.0
    return max(0.0, min(1.0, f))


def _as_sources(value: Any) -> list[dict[str, Any]]:
    """Normalize sources list — dedup by URL, cap length, drop empties."""
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        url = url.strip()
        if url in seen:
            continue
        seen.add(url)
        cleaned.append(
            {
                "url": url[:1024],
                "title": _as_text(entry.get("title"), default=""),
                "via_provider": _as_text(entry.get("via_provider"), default=""),
            }
        )
        if len(cleaned) >= MAX_SOURCES:
            break
    return cleaned


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse_research_report(payload: Any) -> dict[str, Any]:
    """Coerce raw JSON (or dict) into a canonical ResearchReport dict.

    The returned dict matches the field names on `ResearchReport` so it
    can be unpacked directly via `ResearchReport(**parsed)`.
    """
    if not isinstance(payload, dict):
        payload = {}

    result: dict[str, Any] = {
        "executive_summary": _as_text(payload.get("executive_summary"), default=""),
        "market_analysis": _as_text(payload.get("market_analysis"), default=""),
        "competition_analysis": _as_text(payload.get("competition_analysis"), default=""),
        "china_analysis": _as_text(payload.get("china_analysis"), default=""),
        "monetization_analysis": _as_text(payload.get("monetization_analysis"), default=""),
        "mvp_analysis": _as_text(payload.get("mvp_analysis"), default=""),
        "risk_analysis": _as_text(payload.get("risk_analysis"), default=""),
        "recommendation": _as_recommendation(payload.get("recommendation")),
        "confidence": _as_confidence(payload.get("confidence")),
        "sources_json": {"items": _as_sources(payload.get("sources"))},
    }
    return result


def validate_research_report(parsed: dict[str, Any]) -> list[str]:
    """Return a list of human-readable validation warnings.

    Empty list means the report looks usable. Used by the service to
    decide whether to mark the job `completed` or `failed`.
    """
    warnings: list[str] = []
    if not parsed["executive_summary"]:
        warnings.append("executive_summary is empty")
    if parsed["recommendation"] == "insufficient_data" and parsed["confidence"] > 0.5:
        warnings.append("recommendation=insufficient_data but confidence>0.5")
    if not parsed["sources_json"]["items"]:
        warnings.append("no sources cited")
    return warnings


__all__ = [
    "parse_research_report",
    "validate_research_report",
    "RESEARCH_REPORT_SCHEMA",
    "VALID_RECOMMENDATIONS",
]
