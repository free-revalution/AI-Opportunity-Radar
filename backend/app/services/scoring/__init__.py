"""Scoring service package."""

from app.services.scoring.scoring import (
    RESEARCH_TRIGGER_THRESHOLD,
    SUMMARY_ONLY_THRESHOLD,
    ScoreInput,
    WEIGHTS,
    calculate_total_score,
    recommendation_for,
    should_trigger_deep_research,
)

__all__ = [
    "RESEARCH_TRIGGER_THRESHOLD",
    "SUMMARY_ONLY_THRESHOLD",
    "ScoreInput",
    "WEIGHTS",
    "calculate_total_score",
    "recommendation_for",
    "should_trigger_deep_research",
]