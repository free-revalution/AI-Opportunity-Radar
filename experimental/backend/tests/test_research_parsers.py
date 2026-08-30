"""Tests for the strict research-report parser."""

from __future__ import annotations

import pytest

from app.services.research.parsers import (
    VALID_RECOMMENDATIONS,
    parse_research_report,
    validate_research_report,
)


def _valid_report(**overrides) -> dict:
    payload = {
        "executive_summary": "AI sales coaches are growing fast.",
        "market_analysis": "The sales-tech market is large and expanding.",
        "competition_analysis": "Gong and Chorus dominate enterprise.",
        "china_analysis": "Local competitors emerging in Shenzhen.",
        "monetization_analysis": "Seat-based pricing at $99/seat/month.",
        "mvp_analysis": "Build on top of OpenAI + a CRM connector.",
        "risk_analysis": "Switching cost is the main risk.",
        "recommendation": "recommend",
        "confidence": 0.78,
        "sources": [
            {"url": "https://a.com/1", "title": "A", "via_provider": "mock"},
            {"url": "https://b.com/2", "title": "B", "via_provider": "mock"},
        ],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------
def test_parse_valid_full_report():
    parsed = parse_research_report(_valid_report())
    assert parsed["executive_summary"].startswith("AI sales coaches")
    assert parsed["recommendation"] == "recommend"
    assert parsed["confidence"] == pytest.approx(0.78)
    assert len(parsed["sources_json"]["items"]) == 2
    assert parsed["sources_json"]["items"][0]["url"] == "https://a.com/1"


def test_parse_empty_payload_returns_defaults():
    parsed = parse_research_report({})
    assert parsed["executive_summary"] == ""
    assert parsed["recommendation"] == "insufficient_data"
    assert parsed["confidence"] == 0.0
    assert parsed["sources_json"]["items"] == []


def test_parse_non_dict_payload_returns_defaults():
    parsed = parse_research_report([1, 2, 3])  # type: ignore[arg-type]
    assert parsed["recommendation"] == "insufficient_data"
    assert parsed["sources_json"]["items"] == []


# ---------------------------------------------------------------------------
# Recommendation normalisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw", list(VALID_RECOMMENDATIONS))
def test_parse_accepts_all_canonical_recommendations(raw):
    parsed = parse_research_report(_valid_report(recommendation=raw))
    assert parsed["recommendation"] == raw


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Strongly Recommend", "strongly_recommend"),
        ("yes", "recommend"),
        ("MAYBE", "watch"),
        ("no", "not_recommended"),
        ("unknown", "insufficient_data"),
        ("strong-yes", "strongly_recommend"),
        ("", "insufficient_data"),
    ],
)
def test_parse_normalises_common_variants(raw, expected):
    parsed = parse_research_report(_valid_report(recommendation=raw))
    assert parsed["recommendation"] == expected


def test_parse_unknown_recommendation_falls_back_to_insufficient():
    parsed = parse_research_report(_valid_report(recommendation="yolo"))
    assert parsed["recommendation"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Confidence coercion
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.9, 0.9),
        (1.5, 1.0),
        (-0.5, 0.0),
        ("0.4", 0.4),
        ("abc", 0.0),
        (None, 0.0),
    ],
)
def test_parse_confidence_clipping_and_coercion(raw, expected):
    parsed = parse_research_report(_valid_report(confidence=raw))
    assert parsed["confidence"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
def test_parse_sources_dedupes_by_url():
    payload = _valid_report(
        sources=[
            {"url": "https://a.com/1"},
            {"url": "https://a.com/1"},  # duplicate
            {"url": "https://b.com/2"},
        ]
    )
    parsed = parse_research_report(payload)
    urls = [s["url"] for s in parsed["sources_json"]["items"]]
    assert urls == ["https://a.com/1", "https://b.com/2"]


def test_parse_sources_drops_invalid_entries():
    payload = _valid_report(
        sources=[
            {"url": ""},               # empty url
            {"url": 123},              # wrong type
            {"title": "no url"},       # missing url
            {"url": "https://ok.com"}, # valid
            "not a dict",              # wrong type
        ]
    )
    parsed = parse_research_report(payload)
    urls = [s["url"] for s in parsed["sources_json"]["items"]]
    assert urls == ["https://ok.com"]


def test_parse_sources_caps_at_20():
    payload = _valid_report(
        sources=[{"url": f"https://x.com/{i}"} for i in range(30)]
    )
    parsed = parse_research_report(payload)
    assert len(parsed["sources_json"]["items"]) == 20


# ---------------------------------------------------------------------------
# Text fields
# ---------------------------------------------------------------------------
def test_parse_truncates_long_text():
    huge = "x" * 20_000
    parsed = parse_research_report(_valid_report(executive_summary=huge))
    assert len(parsed["executive_summary"]) == 8_000


def test_parse_coerces_non_string_text():
    parsed = parse_research_report(_valid_report(executive_summary=42))
    assert parsed["executive_summary"] == "42"


# ---------------------------------------------------------------------------
# validate_research_report
# ---------------------------------------------------------------------------
def test_validate_clean_report_returns_no_warnings():
    parsed = parse_research_report(_valid_report())
    warnings = validate_research_report(parsed)
    assert warnings == []


def test_validate_flags_missing_executive_summary():
    parsed = parse_research_report(_valid_report(executive_summary=""))
    warnings = validate_research_report(parsed)
    assert any("executive_summary" in w for w in warnings)


def test_validate_flags_high_confidence_with_insufficient_data():
    parsed = parse_research_report(
        _valid_report(recommendation="insufficient_data", confidence=0.8)
    )
    warnings = validate_research_report(parsed)
    assert any("insufficient_data" in w for w in warnings)


def test_validate_flags_no_sources():
    parsed = parse_research_report(_valid_report(sources=[]))
    warnings = validate_research_report(parsed)
    assert any("no sources" in w for w in warnings)
