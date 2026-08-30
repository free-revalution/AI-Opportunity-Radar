"""End-to-end tests for the Phase 7 ResearchService.

Uses MockLLMProvider + MockWebDataProvider so the tests run offline and
deterministically. Verifies the full pipeline:

  pending ResearchJob → scrape URLs → LLM synthesis → ResearchReport row
  + Opportunity.status='research_complete' + ResearchJob.status='completed'.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import (
    Opportunity,
    OpportunitySource,
    RawItem,
    ResearchJob,
    ResearchReport,
    Source,
)
from app.services.research import (
    MockResearchLLMProvider,
    MockWebDataProvider,
    ResearchService,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_source(session, name: str = "r-src") -> Source:
    s = Source(name=name, type="api", url=f"https://example.com/{name}", enabled=True)
    session.add(s)
    await session.flush()
    return s


async def _seed_research_eligible_opportunity(
    session,
    *,
    title: str = "AI Sales Coach for SDRs",
    summary: str = (
        "An AI SaaS that helps B2B sales reps summarise calls and coach them. "
        "Pricing: $99/seat/month."
    ),
    total_score: float = 82.0,
    raw_urls: list[str] | None = None,
) -> tuple[Opportunity, list[RawItem]]:
    """Insert an Opportunity + linked RawItems + a pending ResearchJob."""
    raw_urls = raw_urls or [
        "https://hn.com/1",
        "https://reddit.com/2",
        "https://producthunt.com/3",
    ]

    source = await _seed_source(session)
    raw_items: list[RawItem] = []
    for idx, url in enumerate(raw_urls, start=1):
        item = RawItem(
            source_id=source.id,
            external_id=f"ext-{idx}",
            url=url,
            title=f"{title} — story {idx}",
            content=f"discussion of {title} — momentum, growth, pricing",
            content_hash=f"hash-{idx}",
            metadata_json={"stars": 100 * idx},
        )
        session.add(item)
        raw_items.append(item)
    await session.flush()

    opp = Opportunity(
        title=title,
        slug=f"slug-{title.lower().replace(' ', '-')}",
        summary=summary,
        category="AI SaaS",
        target_user="B2B sales leaders",
        source_count=len(raw_items),
        trend_score=85.0,
        demand_score=80.0,
        monetization_score=78.0,
        competition_gap_score=70.0,
        china_gap_score=65.0,
        execution_score=72.0,
        total_score=total_score,
        status="research_eligible",
    )
    session.add(opp)
    await session.flush()

    for item in raw_items:
        link = OpportunitySource(opportunity_id=opp.id, raw_item_id=item.id, relevance=1.0)
        session.add(link)
    await session.flush()

    job = ResearchJob(opportunity_id=opp.id, status="pending")
    session.add(job)
    await session.flush()

    return opp, raw_items


# ---------------------------------------------------------------------------
# Run-once sweep
# ---------------------------------------------------------------------------
async def test_run_once_processes_pending_jobs(sqlite_session):
    await _seed_research_eligible_opportunity(sqlite_session)
    service = ResearchService(
        sqlite_session,
        llm_provider=MockResearchLLMProvider(),
        web_provider=MockWebDataProvider(),
        max_urls=5,
        max_depth=0,
    )
    report = await service.run_once()
    assert report.jobs_attempted == 1
    assert report.jobs_completed == 1
    assert report.jobs_failed == 0
    assert report.reports_persisted == 1
    assert report.urls_scraped >= 1


async def test_run_once_is_noop_when_no_pending_jobs(sqlite_session):
    service = ResearchService(
        sqlite_session,
        llm_provider=MockResearchLLMProvider(),
        web_provider=MockWebDataProvider(),
    )
    report = await service.run_once()
    assert report.jobs_attempted == 0
    assert report.jobs_completed == 0


# ---------------------------------------------------------------------------
# Per-job processing
# ---------------------------------------------------------------------------
async def test_process_job_persists_report_and_marks_opportunity_complete(
    sqlite_session,
):
    opp, _raw = await _seed_research_eligible_opportunity(sqlite_session)
    service = ResearchService(
        sqlite_session,
        llm_provider=MockResearchLLMProvider(),
        web_provider=MockWebDataProvider(),
        max_urls=3,
        max_depth=0,
    )

    job = (
        await sqlite_session.execute(
            select(ResearchJob).where(ResearchJob.opportunity_id == opp.id)
        )
    ).scalars().first()
    assert job is not None

    outcome = await service.process_job(job.id)
    assert outcome.status == "completed"
    assert outcome.sources_count >= 1

    # Refresh from DB.
    reports = (
        await sqlite_session.execute(
            select(ResearchReport).where(ResearchReport.opportunity_id == opp.id)
        )
    ).scalars().all()
    assert len(reports) == 1
    r = reports[0]
    assert r.executive_summary
    assert r.recommendation in {
        "strongly_recommend",
        "recommend",
        "watch",
        "not_recommended",
        "insufficient_data",
    }
    assert 0.0 <= r.confidence <= 1.0

    # Opportunity should have flipped status.
    refreshed = await sqlite_session.get(Opportunity, opp.id)
    assert refreshed.status == "research_complete"

    # Job should be marked completed.
    refreshed_job = await sqlite_session.get(ResearchJob, job.id)
    assert refreshed_job.status == "completed"
    assert refreshed_job.started_at is not None
    assert refreshed_job.completed_at is not None
    assert refreshed_job.provider == "mock"


async def test_process_job_unknown_id_raises(sqlite_session):
    service = ResearchService(
        sqlite_session,
        llm_provider=MockResearchLLMProvider(),
        web_provider=MockWebDataProvider(),
    )
    with pytest.raises(LookupError):
        await service.process_job(9999)


async def test_process_job_records_error_when_llm_raises(sqlite_session):
    """A broken LLM provider should mark the job failed (not raise)."""

    class _BoomLLM:
        async def complete_json(self, **kwargs):
            raise RuntimeError("synthetic boom")

    opp, _ = await _seed_research_eligible_opportunity(sqlite_session)
    service = ResearchService(
        sqlite_session,
        llm_provider=_BoomLLM(),  # type: ignore[arg-type]
        web_provider=MockWebDataProvider(),
    )
    job = (
        await sqlite_session.execute(
            select(ResearchJob).where(ResearchJob.opportunity_id == opp.id)
        )
    ).scalars().first()

    outcome = await service.process_job(job.id)
    assert outcome.status == "failed"
    assert "synthetic boom" in (outcome.error or "")

    refreshed_job = await sqlite_session.get(ResearchJob, job.id)
    assert refreshed_job.status == "failed"
    assert "synthetic boom" in (refreshed_job.error)


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------
async def test_cancel_pending_job_marks_cancelled(sqlite_session):
    opp, _ = await _seed_research_eligible_opportunity(sqlite_session)
    service = ResearchService(sqlite_session)
    job = (
        await sqlite_session.execute(
            select(ResearchJob).where(ResearchJob.opportunity_id == opp.id)
        )
    ).scalars().first()

    ok = await service.cancel(job.id)
    assert ok is True
    refreshed = await sqlite_session.get(ResearchJob, job.id)
    assert refreshed.status == "cancelled"


async def test_cancel_completed_job_returns_false(sqlite_session):
    opp, _ = await _seed_research_eligible_opportunity(sqlite_session)
    service = ResearchService(
        sqlite_session,
        llm_provider=MockResearchLLMProvider(),
        web_provider=MockWebDataProvider(),
    )
    job = (
        await sqlite_session.execute(
            select(ResearchJob).where(ResearchJob.opportunity_id == opp.id)
        )
    ).scalars().first()
    await service.process_job(job.id)

    ok = await service.cancel(job.id)
    assert ok is False


async def test_cancel_unknown_job_returns_false(sqlite_session):
    service = ResearchService(sqlite_session)
    assert await service.cancel(424242) is False


# ---------------------------------------------------------------------------
# Use-mock override on process_job
# ---------------------------------------------------------------------------
async def test_process_job_use_mock_web_uses_mock_provider(sqlite_session):
    """`use_mock_web=True` should override the configured provider."""

    class _ForbiddenWeb:
        name = "firecrawl"

        async def search(self, *a, **kw):
            raise AssertionError("should be replaced by the mock override")

        async def scrape(self, *a, **kw):
            raise AssertionError("should be replaced by the mock override")

    opp, _ = await _seed_research_eligible_opportunity(sqlite_session)
    service = ResearchService(
        sqlite_session,
        llm_provider=MockResearchLLMProvider(),
        web_provider=_ForbiddenWeb(),  # type: ignore[arg-type]
    )
    job = (
        await sqlite_session.execute(
            select(ResearchJob).where(ResearchJob.opportunity_id == opp.id)
        )
    ).scalars().first()

    outcome = await service.process_job(job.id, use_mock_web=True)
    assert outcome.status == "completed"
