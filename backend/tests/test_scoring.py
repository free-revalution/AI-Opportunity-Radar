"""Tests for the deterministic opportunity scoring formula."""

from __future__ import annotations

import math

from app.services.scoring import (
    RESEARCH_TRIGGER_THRESHOLD,
    ScoreInput,
    WEIGHTS,
    calculate_total_score,
    recommendation_for,
    should_trigger_deep_research,
)


def _approx(a: float, b: float, *, places: int = 4) -> bool:
    return math.isclose(a, b, abs_tol=10 ** -places)


def test_weights_sum_to_one() -> None:
    assert _approx(sum(WEIGHTS.values()), 1.0)


def test_perfect_score_is_one_hundred() -> None:
    total = calculate_total_score(
        ScoreInput(
            trend=100, demand=100, monetization=100,
            competition_gap=100, china_gap=100, execution=100,
        )
    )
    assert total == 100.0


def test_zero_score_is_zero() -> None:
    total = calculate_total_score(
        ScoreInput(0, 0, 0, 0, 0, 0)
    )
    assert total == 0.0


def test_known_mix_matches_weighted_sum() -> None:
    """A hand-computed example — guards against accidental weight changes."""
    scores = ScoreInput(
        trend=90,         # 0.20 * 90 = 18.0
        demand=80,        # 0.20 * 80 = 16.0
        monetization=70,  # 0.20 * 70 = 14.0
        competition_gap=60,  # 0.15 * 60 =  9.0
        china_gap=50,     # 0.15 * 50 =  7.5
        execution=40,     # 0.10 * 40 =  4.0
    )
    expected = 18.0 + 16.0 + 14.0 + 9.0 + 7.5 + 4.0  # = 68.5
    assert _approx(calculate_total_score(scores), expected)


def test_overflow_values_are_clipped_to_one_hundred() -> None:
    total = calculate_total_score(
        ScoreInput(500, 500, 500, 500, 500, 500)
    )
    assert total == 100.0


def test_negative_values_are_clipped_to_zero() -> None:
    total = calculate_total_score(
        ScoreInput(-10, -10, -10, -10, -10, -10)
    )
    assert total == 0.0


def test_recommendation_thresholds() -> None:
    assert recommendation_for(95) == "strongly_recommend"
    assert recommendation_for(85) == "strongly_recommend"
    assert recommendation_for(80) == "recommend"
    assert recommendation_for(70) == "recommend"
    assert recommendation_for(60) == "watch"
    assert recommendation_for(40) == "not_recommended"


def test_deep_research_trigger_respects_threshold() -> None:
    assert should_trigger_deep_research(RESEARCH_TRIGGER_THRESHOLD) is True
    assert should_trigger_deep_research(RESEARCH_TRIGGER_THRESHOLD - 1) is False
    assert should_trigger_deep_research(100.0) is True