"""End-to-end tests for the Phase 6 ScoringService."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Opportunity, OpportunitySource, RawItem, ResearchJob, Signal, Source
from app.repositories import RawItemRepository
from app.services.clustering import Clusterer, ClusteringService, HashingEmbedder
from app.services.llm import MockLLMProvider
from app.services.scoring import (
    RESEARCH_TRIGGER_THRESHOLD,
    ScoringService,
    calculate_total_score,
)
from app.services.scoring.scoring import ScoreInput
from app.services.screening import ScreeningService

pytestmark = pytest.mark.asyncio


async def _seed_source(session, name: str = "test-src") -> Source:
    s = Source(name=name, type="api", url=f"https://example.com/{name}", enabled=True)
    session.add(s)
    await session.flush()
    return s


async def _seed_screened_opportunity(
    session,
    *,
    title: str,
    sub_scores: dict[str, float],
    items: list[dict],
    manually_screened: bool = True,
) -> Opportunity:
    """Create a clustered + screened opportunity with given sub-scores.

    Multi-call safe: returns the most-recently-created opportunity
    (highest id) so the helper works inside a session that already
    contains other opportunities.
    """
    source = await _seed_source(session)
    raw_repo = RawItemRepository(session)
    for it in items:
        await raw_repo.upsert(
            source_id=source.id,
            external_id=it["external_id"],
            url=it["url"],
            title=it["title"],
            content=it.get("content"),
            metadata_json=it.get("metadata_json", {}),
        )
    await session.flush()

    clusterer = ClusteringService(
        session,
        embedder=HashingEmbedder(dim=512, ngram=3),
        clusterer=Clusterer(threshold=0.0),
    )
    await clusterer.run_once()
    await session.flush()

    # Return the most-recently-created opportunity (highest id).
    opp = (
        await session.execute(
            select(Opportunity).order_by(Opportunity.id.desc()).limit(1)
        )
    ).scalars().first()
    assert opp is not None

    opp.title = title
    opp.trend_score = sub_scores["trend"]
    opp.demand_score = sub_scores["demand"]
    opp.monetization_score = sub_scores["monetization"]
    opp.competition_gap_score = sub_scores["competition_gap"]
    opp.china_gap_score = sub_scores["china_gap"]
    opp.execution_score = sub_scores["execution"]
    opp.total_score = calculate_total_score(
        ScoreInput(
            trend=sub_scores["trend"],
            demand=sub_scores["demand"],
            monetization=sub_scores["monetization"],
            competition_gap=sub_scores["competition_gap"],
            china_gap=sub_scores["china_gap"],
            execution=sub_scores["execution"],
        )
    )
    opp.status = "screened" if manually_screened else "detected"

    # Add a Signal row per linked RawItem so the blend has data to read.
    links = (
        await session.execute(
            select(OpportunitySource).where(
                OpportunitySource.opportunity_id == opp.id
            )
        )
    ).scalars().all()
    for link in links:
        await SignalRepository_create_signal(
            session,
            raw_item_id=link.raw_item_id,
            velocity=sub_scores["trend"],
            engagement=100.0,
            relevance=1.0,
        )
    await session.flush()
    return opp


async def SignalRepository_create_signal(
    session, *, raw_item_id: int, velocity: float, engagement: float, relevance: float
) -> None:
    sig = Signal(
        raw_item_id=raw_item_id,
        signal_type="screening",
        velocity_score=velocity,
        engagement_score=engagement,
        relevance_score=relevance,
    )
    session.add(sig)
    await session.flush()


_HIGH = {"trend": 90, "demand": 85, "monetization": 80, "competition_gap": 75, "china_gap": 80, "execution": 80}
_LOW = {"trend": 30, "demand": 25, "monetization": 20, "competition_gap": 30, "china_gap": 30, "execution": 30}


async def test_score_one_marks_research_eligible_when_above_threshold(sqlite_session):
    opp = await _seed_screened_opportunity(
        sqlite_session,
        title="High potential",
        sub_scores=_HIGH,
        items=[
            {"external_id": "1", "url": "https://hn.com/1", "title": "AI Sales Coach"},
        ],
    )

    service = ScoringService(sqlite_session, blend_signals=False)
    outcome = await service.score_one(opp)

    assert outcome.total_score >= RESEARCH_TRIGGER_THRESHOLD
    assert outcome.status == "research_eligible"
    assert outcome.research_job_id is not None
    assert outcome.changed is True


async def test_score_one_keeps_status_scored_below_threshold(sqlite_session):
    opp = await _seed_screened_opportunity(
        sqlite_session,
        title="Low potential",
        sub_scores=_LOW,
        items=[
            {"external_id": "1", "url": "https://hn.com/1", "title": "Tiny idea"},
        ],
    )

    service = ScoringService(sqlite_session, blend_signals=False)
    outcome = await service.score_one(opp)

    assert outcome.total_score < RESEARCH_TRIGGER_THRESHOLD
    assert outcome.status == "scored"
    assert outcome.research_job_id is None


async def test_score_one_does_not_create_duplicate_research_job(sqlite_session):
    opp = await _seed_screened_opportunity(
        sqlite_session,
        title="High potential",
        sub_scores=_HIGH,
        items=[
            {"external_id": "1", "url": "https://hn.com/1", "title": "AI Sales Coach"},
        ],
    )
    opp_id = opp.id

    service = ScoringService(sqlite_session, blend_signals=False)
    first = await service.score_one(opp_id)
    second = await service.score_one(opp_id)
    assert first.research_job_id is not None
    assert second.research_job_id is None  # already pending → no new job

    jobs = (await sqlite_session.execute(select(ResearchJob))).scalars().all()
    assert len(jobs) == 1


async def test_run_once_processes_all_candidates(sqlite_session):
    for idx, scores in enumerate([_HIGH, _LOW, _HIGH]):
        await _seed_screened_opportunity(
            sqlite_session,
            title=f"Opportunity {idx}",
            sub_scores=scores,
            items=[
                {
                    "external_id": f"e{idx}",
                    "url": f"https://example.com/{idx}",
                    "title": f"Story {idx}",
                    "content": "AI SaaS for sales teams.",
                }
            ],
        )

    service = ScoringService(sqlite_session, blend_signals=False)
    report = await service.run_once()

    assert report.opportunities_attempted == 3
    # 2 high → research_eligible; 1 low → scored.
    assert report.opportunities_marked_eligible == 2
    assert report.research_jobs_created == 2


async def test_run_once_ignores_detected_opportunities(sqlite_session):
    """Opportunities that haven't been screened yet are out of scope."""
    opp = await _seed_screened_opportunity(
        sqlite_session,
        title="Not screened yet",
        sub_scores=_HIGH,
        items=[{"external_id": "1", "url": "https://x.com/1", "title": "AI thing"}],
        manually_screened=False,  # status='detected'
    )

    service = ScoringService(sqlite_session, blend_signals=False)
    report = await service.run_once()
    assert report.opportunities_attempted == 0


async def test_run_once_is_idempotent_on_subscores(sqlite_session):
    """Running the sweeper twice with no signal changes yields unchanged=True."""
    opp = await _seed_screened_opportunity(
        sqlite_session,
        title="Stable",
        sub_scores=_HIGH,
        items=[{"external_id": "1", "url": "https://x.com/1", "title": "AI thing"}],
    )
    opp_id = opp.id

    service = ScoringService(sqlite_session, blend_signals=False)
    first = await service.run_once()
    second = await service.run_once()

    # The first run moved screened → research_eligible (changed).
    # The second run sees the same total_score, no change.
    assert first.opportunities_scored == 1
    assert second.unchanged == 1
    assert second.opportunities_scored == 0


async def test_score_one_blends_signal_velocity(sqlite_session):
    """Engagement-heavy signals should nudge trend slightly."""
    opp = await _seed_screened_opportunity(
        sqlite_session,
        title="Blend test",
        sub_scores={"trend": 50, "demand": 50, "monetization": 50,
                    "competition_gap": 50, "china_gap": 50, "execution": 50},
        items=[{"external_id": "1", "url": "https://x.com/1", "title": "AI thing"}],
    )

    # Bump the signals way up — the blend should shift trend/demand.
    links = (
        await sqlite_session.execute(
            select(OpportunitySource).where(
                OpportunitySource.opportunity_id == opp.id
            )
        )
    ).scalars().all()
    for link in links:
        sig = (
            await sqlite_session.execute(
                select(Signal).where(Signal.raw_item_id == link.raw_item_id)
            )
        ).scalars().first()
        sig.velocity_score = 95.0
        sig.engagement_score = 2000.0  # → ~ 90 after compression
    await sqlite_session.flush()

    service = ScoringService(sqlite_session, blend_signals=True)
    outcome = await service.score_one(opp)
    await sqlite_session.refresh(opp)

    # Trend was 50; with avg_velocity=95 → ~50 + (95+5-50)*0.15 = ~55.
    assert opp.trend_score > 50.0
    assert outcome.changed is True


async def test_score_one_returns_404_for_missing_opportunity(sqlite_session):
    service = ScoringService(sqlite_session, blend_signals=False)
    with pytest.raises(LookupError):
        await service.score_one(9999)
