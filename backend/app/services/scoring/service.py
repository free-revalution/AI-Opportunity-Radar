"""Scoring service — Phase 6.

Responsibilities:

  * Re-derive the weighted `total_score` from the persisted sub-scores
    (the formula in `scoring.py` is pure-Python — running it here is
    idempotent and cheap, so a periodic re-score is safe).
  * Optionally blend in the signal aggregates (engagement, velocity,
    relevance) so that new RawItems linked to the same opportunity
    shift the score.
  * Move the opportunity through its status machine:
        screened           → scored
        scored             → research_eligible  (when score ≥ 70)
        scored             → scored             (when score <  70)
  * When the opportunity crosses the threshold, enqueue a `ResearchJob`
    row (Phase 7 picks it up; the row is a stub for now).

The service is intentionally side-effect free per opportunity: a single
LLM / network failure does not poison the batch.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Opportunity, ResearchJob
from app.repositories import (
    OpportunityRepository,
    SignalRepository,
)
from app.services.scoring.scoring import (
    RESEARCH_TRIGGER_THRESHOLD,
    SUMMARY_ONLY_THRESHOLD,
    ScoreInput,
    calculate_total_score,
    recommendation_for,
)
from app.utils import get_logger

logger = get_logger(__name__)


# Cap on how much the signal aggregates may shift a sub-score.
# This prevents a viral Reddit thread from completely inverting the
# LLM's business assessment.
SIGNAL_BLEND_WEIGHT = 0.15  # 15 % from signals, 85 % from LLM
ENGAGEMENT_TO_SCORE_DIVISOR = 100.0  # 1000 stars ≈ +10 trend nudge
VELOCITY_FLOOR_BOOST = 5.0  # even modest signal velocity nudges trend


@dataclass(slots=True)
class ScoringReport:
    """Outcome of a single `run_once()` call."""

    opportunities_attempted: int = 0
    opportunities_scored: int = 0
    opportunities_marked_eligible: int = 0
    research_jobs_created: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


@dataclass(slots=True)
class ScoringOutcome:
    """Per-opportunity result."""

    opportunity_id: int
    total_score: float
    recommendation: str
    status: str
    research_job_id: Optional[int]
    changed: bool


class ScoringService:
    """Phase 6 orchestrator — re-derives scores, gates research, tracks status."""

    SCORED_STATUS = "scored"
    RESEARCH_ELIGIBLE_STATUS = "research_eligible"

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        trigger_threshold: float | None = None,
        limit: int = 200,
        blend_signals: bool = True,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.trigger_threshold = (
            trigger_threshold
            if trigger_threshold is not None
            else RESEARCH_TRIGGER_THRESHOLD
        )
        self.limit = limit
        self.blend_signals = blend_signals

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def run_once(self) -> ScoringReport:
        """Re-score every screened / scored / research_eligible opportunity."""
        report = ScoringReport()
        opp_repo = OpportunityRepository(self.session)
        candidates = await opp_repo.list_scored_candidates(limit=self.limit)
        report.opportunities_attempted = len(candidates)
        if not candidates:
            logger.info("scoring_nothing_to_score")
            return report

        for opp in candidates:
            try:
                outcome = await self.score_one(opp)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"opp {opp.id}: {exc}")
                logger.exception("scoring_failed", opportunity_id=opp.id)
                continue

            if outcome.changed:
                report.opportunities_scored += 1
                if outcome.status == self.RESEARCH_ELIGIBLE_STATUS:
                    report.opportunities_marked_eligible += 1
                if outcome.research_job_id is not None:
                    report.research_jobs_created += 1
            else:
                report.unchanged += 1

        logger.info("scoring_run_complete", **report.as_dict())
        return report

    async def score_one(
        self, opp: Opportunity | int
    ) -> ScoringOutcome:
        """Score one opportunity explicitly (used by `score/{id}` endpoints)."""
        opp_obj = await self._resolve(opp)
        if opp_obj is None:
            raise LookupError(f"opportunity not found: {opp}")

        before_score = opp_obj.total_score
        before_status = opp_obj.status

        # 1. Re-derive total_score from sub-scores (with optional signal blend).
        score_input = await self._build_score_input(opp_obj)

        # 2. Persist the (possibly blended) sub-scores back onto the row so
        #    callers can read them via the public API without recomputing.
        opp_obj.trend_score = score_input.trend
        opp_obj.demand_score = score_input.demand
        opp_obj.monetization_score = score_input.monetization
        opp_obj.competition_gap_score = score_input.competition_gap
        opp_obj.china_gap_score = score_input.china_gap
        opp_obj.execution_score = score_input.execution

        # 3. Roll the weighted formula → total_score.
        opp_obj.total_score = calculate_total_score(score_input)
        opp_obj.status = self.SCORED_STATUS

        # 4. Recommendation label — exposed via /opportunities API too.
        recommendation = recommendation_for(opp_obj.total_score)

        # 5. Gate to research when the threshold is crossed.
        research_job_id: Optional[int] = None
        if opp_obj.total_score >= self.trigger_threshold:
            opp_obj.status = self.RESEARCH_ELIGIBLE_STATUS
            research_job_id = await self._ensure_research_job(opp_obj)

        await self.session.flush()
        changed = (
            opp_obj.total_score != before_score
            or opp_obj.status != before_status
        )
        if changed:
            await self.session.commit()

        return ScoringOutcome(
            opportunity_id=opp_obj.id,
            total_score=opp_obj.total_score,
            recommendation=recommendation,
            status=opp_obj.status,
            research_job_id=research_job_id,
            changed=changed,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _resolve(
        self, opp: Opportunity | int
    ) -> Optional[Opportunity]:
        if isinstance(opp, Opportunity):
            return opp
        opp_repo = OpportunityRepository(self.session)
        return await opp_repo.get_by_id(int(opp))

    async def _build_score_input(self, opp: Opportunity) -> ScoreInput:
        """Reconstruct ScoreInput, blending in signal aggregates if enabled."""
        trend = float(opp.trend_score)
        demand = float(opp.demand_score)
        monetisation = float(opp.monetization_score)
        competition = float(opp.competition_gap_score)
        china = float(opp.china_gap_score)
        execution = float(opp.execution_score)

        if self.blend_signals:
            signal_repo = SignalRepository(self.session)
            agg = await signal_repo.aggregate_for_opportunity(opp.id)
            if not agg.is_empty:
                # Trend nudges with avg_velocity; demand with engagement;
                # monetisation with relevance + log-ish engagement.
                trend = _blend(
                    trend, _velocity_to_score(agg.avg_velocity)
                )
                demand = _blend(
                    demand, _engagement_to_score(agg.avg_engagement)
                )
                monetisation = _blend(
                    monetisation,
                    _engagement_to_score(agg.avg_engagement)
                    * 0.6
                    + 100.0 * agg.avg_relevance,
                )

        return ScoreInput(
            trend=trend,
            demand=demand,
            monetization=monetisation,
            competition_gap=competition,
            china_gap=china,
            execution=execution,
        )

    async def _ensure_research_job(self, opp: Opportunity) -> Optional[int]:
        """Create a pending ResearchJob — no-op if one is already running."""
        opp_repo = OpportunityRepository(self.session)
        if await opp_repo.has_pending_research(opp.id):
            logger.info(
                "research_job_already_pending",
                opportunity_id=opp.id,
            )
            return None
        job = ResearchJob(
            opportunity_id=opp.id,
            status="pending",
            provider=None,  # picked by the Phase 7 worker
        )
        self.session.add(job)
        await self.session.flush()
        logger.info(
            "research_job_queued",
            opportunity_id=opp.id,
            job_id=job.id,
            total_score=opp.total_score,
        )
        return job.id


# ----------------------------------------------------------------------
# Pure helpers (kept at module bottom so they're easy to unit-test).
# ----------------------------------------------------------------------
def _blend(llm_score: float, signal_score: float) -> float:
    """Weighted blend of the LLM score and a signal-derived score."""
    blended = (
        (1.0 - SIGNAL_BLEND_WEIGHT) * llm_score
        + SIGNAL_BLEND_WEIGHT * signal_score
    )
    # Clip to [0, 100] — same range as the underlying scoring formula.
    return max(0.0, min(100.0, blended))


def _velocity_to_score(avg_velocity: float) -> float:
    """Map an average signal velocity in [0, 100] to a trend nudge."""
    return max(0.0, min(100.0, avg_velocity + VELOCITY_FLOOR_BOOST))


def _engagement_to_score(avg_engagement: float) -> float:
    """Compress raw engagement totals into a 0-100 score."""
    if avg_engagement <= 0:
        return 0.0
    raw = avg_engagement / ENGAGEMENT_TO_SCORE_DIVISOR
    # Log-shaped: diminishing returns past 1000.
    return max(0.0, min(100.0, 30.0 * math.log1p(raw)))


__all__ = [
    "RESEARCH_TRIGGER_THRESHOLD",
    "SUMMARY_ONLY_THRESHOLD",
    "SIGNAL_BLEND_WEIGHT",
    "ScoringOutcome",
    "ScoringReport",
    "ScoringService",
    "_blend",
    "_engagement_to_score",
    "_velocity_to_score",
]
