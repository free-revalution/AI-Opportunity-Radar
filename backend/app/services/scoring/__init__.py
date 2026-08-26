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
from app.services.scoring.service import (
    SIGNAL_BLEND_WEIGHT,
    ScoringOutcome,
    ScoringReport,
    ScoringService,
    _blend,
    _engagement_to_score,
    _velocity_to_score,
)

__all__ = [
    "RESEARCH_TRIGGER_THRESHOLD",
    "SIGNAL_BLEND_WEIGHT",
    "SUMMARY_ONLY_THRESHOLD",
    "ScoreInput",
    "ScoringOutcome",
    "ScoringReport",
    "ScoringService",
    "WEIGHTS",
    "_blend",
    "_engagement_to_score",
    "_velocity_to_score",
    "calculate_total_score",
    "recommendation_for",
    "should_trigger_deep_research",
]
