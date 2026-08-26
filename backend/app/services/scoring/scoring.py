"""Opportunity scoring service.

Implements the weighted formula from README §12:

    Opportunity Score =
        Trend Velocity      × 0.20
      + Demand              × 0.20
      + Monetization        × 0.20
      + Competition Gap     × 0.15
      + China Gap           × 0.15
      + Execution Feasibility × 0.10

All sub-scores are normalised to 0-100. The output is a single float in
[0, 100] rounded to two decimal places.

This module is deliberately framework-free (no FastAPI / SQLAlchemy
imports) so it can be unit-tested without any infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass

# Sub-score weights — must sum to 1.0.
WEIGHTS: dict[str, float] = {
    "trend": 0.20,
    "demand": 0.20,
    "monetization": 0.20,
    "competition_gap": 0.15,
    "china_gap": 0.15,
    "execution": 0.10,
}


@dataclass(frozen=True, slots=True)
class ScoreInput:
    """Sub-scores for an opportunity. Each value must be in [0, 100]."""

    trend: float
    demand: float
    monetization: float
    competition_gap: float
    china_gap: float
    execution: float


def _clip(value: float) -> float:
    """Clamp a sub-score to [0, 100]. Negative / over-100 inputs are tolerated."""
    if value < 0:
        return 0.0
    if value > 100:
        return 100.0
    return value


def calculate_total_score(scores: ScoreInput) -> float:
    """Return the weighted total score in [0, 100]."""
    parts = {
        "trend": _clip(scores.trend),
        "demand": _clip(scores.demand),
        "monetization": _clip(scores.monetization),
        "competition_gap": _clip(scores.competition_gap),
        "china_gap": _clip(scores.china_gap),
        "execution": _clip(scores.execution),
    }
    total = sum(parts[key] * WEIGHTS[key] for key in WEIGHTS)
    return round(total, 2)


# ---------------------------------------------------------------------------
# Recommendation thresholds
# ---------------------------------------------------------------------------
# Below this total score we do not even bother saving a deep-research prompt.
RESEARCH_TRIGGER_THRESHOLD = 70.0
# Below this we only persist a summary.
SUMMARY_ONLY_THRESHOLD = 50.0


def recommendation_for(total_score: float) -> str:
    """Map a total score to a coarse recommendation label."""
    if total_score >= 85:
        return "strongly_recommend"
    if total_score >= 70:
        return "recommend"
    if total_score >= 50:
        return "watch"
    return "not_recommended"


def should_trigger_deep_research(total_score: float) -> bool:
    """Whether the scoring engine is confident enough to spawn a research job."""
    return total_score >= RESEARCH_TRIGGER_THRESHOLD