"""Tests for ScreeningResult parsing — strict LLM-output validation."""

from __future__ import annotations

import pytest

from app.services.screening import parse_screening_response
from app.utils import ValidationError


def _valid_payload(**overrides) -> dict:
    payload = {
        "is_business_relevant": True,
        "category": "AI SaaS",
        "problem": "SDRs waste time on call summaries.",
        "potential_business": "AI coach for SDRs.",
        "trend_strength": 80,
        "demand_strength": 70,
        "monetization_potential": 75,
        "competition_gap": 60,
        "china_gap": 65,
        "execution_feasibility": 70,
        "keywords": ["ai", "sales", "saas"],
        "confidence": 0.85,
    }
    payload.update(overrides)
    return payload


def test_parse_minimal_valid_response():
    result = parse_screening_response(_valid_payload())
    assert result.is_business_relevant is True
    assert result.trend_strength == 80
    assert result.confidence == pytest.approx(0.85)


def test_parse_clips_out_of_range_scores():
    result = parse_screening_response(_valid_payload(trend_strength=150, demand_strength=-10))
    assert result.trend_strength == 100
    assert result.demand_strength == 0


def test_parse_coerces_float_to_int():
    result = parse_screening_response(_valid_payload(trend_strength=82.7))
    assert result.trend_strength == 83


def test_parse_coerces_string_int():
    result = parse_screening_response(_valid_payload(trend_strength="45"))
    assert result.trend_strength == 45


def test_parse_rejects_unparseable_score():
    with pytest.raises(ValidationError):
        parse_screening_response(_valid_payload(trend_strength="not-a-number"))


def test_parse_rejects_non_dict_payload():
    with pytest.raises(ValidationError):
        parse_screening_response([1, 2, 3])  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        parse_screening_response("string")  # type: ignore[arg-type]


def test_parse_rejects_missing_required_field():
    payload = _valid_payload()
    payload.pop("is_business_relevant")
    with pytest.raises(ValidationError):
        parse_screening_response(payload)


def test_irrelevant_caps_subscores_at_35():
    payload = _valid_payload(
        is_business_relevant=False,
        trend_strength=80,
        demand_strength=80,
        monetization_potential=80,
    )
    result = parse_screening_response(payload)
    assert result.is_business_relevant is False
    assert result.trend_strength <= 35
    assert result.demand_strength <= 35
    assert result.monetization_potential <= 30


def test_keywords_deduplicated_and_lowercased():
    payload = _valid_payload(keywords=["AI", "sales", "AI", "SaaS", "  sales  "])
    result = parse_screening_response(payload)
    assert result.keywords == ["ai", "sales", "saas"]


def test_keywords_string_fallback():
    payload = _valid_payload(keywords="ai, sales, saas")
    result = parse_screening_response(payload)
    assert result.keywords == ["ai", "sales", "saas"]


def test_keywords_empty_when_none():
    payload = _valid_payload(keywords=None)
    result = parse_screening_response(payload)
    assert result.keywords == []


def test_confidence_clamped_to_0_1():
    payload = _valid_payload(confidence=1.5)
    result = parse_screening_response(payload)
    assert result.confidence == 1.0
    payload = _valid_payload(confidence=-0.5)
    result = parse_screening_response(payload)
    assert result.confidence == 0.0


def test_confidence_defaults_to_05_when_invalid():
    payload = _valid_payload(confidence="nope")
    result = parse_screening_response(payload)
    assert result.confidence == 0.5


def test_string_fields_truncated_to_2000():
    long = "x" * 5000
    result = parse_screening_response(
        _valid_payload(problem=long, potential_business=long)
    )
    assert len(result.problem) == 2000
    assert len(result.potential_business) == 2000


def test_bool_is_not_accepted_as_int():
    """`is_business_relevant` is bool; sub-scores must not be bools."""
    payload = _valid_payload(trend_strength=True)
    with pytest.raises(ValidationError):
        parse_screening_response(payload)
