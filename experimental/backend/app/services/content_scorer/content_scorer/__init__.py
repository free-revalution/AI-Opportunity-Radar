"""Content quality scoring (Phase 10).

LLM-as-judge over a single generated piece — returns 5 sub-scores + a
weighted total + a rationale. Used by:

* `POST /api/internal/content/{id}/quality` — operator-facing score
* `ContentGeneratorService.run_for_opportunity(..., auto_regenerate=True)`
  — auto-retry low-scoring pieces (off by default)
"""

from app.services.content_scorer.scorer import (
    DEFAULT_DIMENSION_FLOOR,
    DEFAULT_THRESHOLD,
    ContentQualityScore,
    ContentQualityScorer,
)

__all__ = [
    "ContentQualityScorer",
    "ContentQualityScore",
    "DEFAULT_THRESHOLD",
    "DEFAULT_DIMENSION_FLOOR",
]