"""Tests for /api/internal/research/{run, run/{id}, cancel/{id}}."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Opportunity, OpportunitySource, RawItem, ResearchJob, Source

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_research_job(client) -> int:
    """Insert one research_eligible opportunity + pending ResearchJob via SQL.

    Uses `client.sessionmaker` (exposed by the conftest fixture) so we
    don't have to round-trip through HTTP. The fixture wires the same
    SQLite in-memory store the request handlers use, so the new rows are
    visible to subsequent requests.
    """
    sessionmaker = client.sessionmaker
    async with sessionmaker() as session:
        source = (
            await session.execute(
                select(Source).order_by(Source.id.asc()).limit(1)
            )
        ).scalars().first()
        if source is None:
            source = Source(
                name="r-test", type="api", url="https://example.com/r", enabled=True
            )
            session.add(source)
            await session.flush()

        items = [
            RawItem(
                source_id=source.id,
                external_id=f"res-{i}",
                url=f"https://news.example.com/{i}",
                title=f"Story {i} about AI sales coach",
                content="AI sales coach discussion, pricing, growth",
                content_hash=f"hash-{i}",
                metadata_json={"stars": 50 * (i + 1)},
            )
            for i in range(3)
        ]
        for it in items:
            session.add(it)
        await session.flush()

        opp = Opportunity(
            title="AI Sales Coach",
            slug="ai-sales-coach",
            summary="AI SaaS for SDRs that summarises calls and coaches them.",
            category="AI SaaS",
            target_user="B2B sales leaders",
            source_count=len(items),
            trend_score=85.0,
            demand_score=80.0,
            monetization_score=78.0,
            competition_gap_score=70.0,
            china_gap_score=65.0,
            execution_score=72.0,
            total_score=80.0,
            status="research_eligible",
        )
        session.add(opp)
        await session.flush()

        for it in items:
            session.add(
                OpportunitySource(
                    opportunity_id=opp.id, raw_item_id=it.id, relevance=1.0
                )
            )
        job = ResearchJob(opportunity_id=opp.id, status="pending")
        session.add(job)
        await session.commit()
        return job.id


# ---------------------------------------------------------------------------
# /api/internal/research/run
# ---------------------------------------------------------------------------
async def test_research_run_returns_report_shape(client):
    job_id = await _seed_research_job(client)
    response = client.post(
        "/api/internal/research/run", json={"limit": 5, "use_mock_web": True, "use_mock_llm": True}
    )
    assert response.status_code == 200
    body = response.json()
    for key in (
        "jobs_attempted",
        "jobs_completed",
        "jobs_failed",
        "urls_scraped",
        "reports_persisted",
        "errors",
    ):
        assert key in body, f"missing field {key}"
    assert body["jobs_attempted"] >= 1
    assert body["jobs_completed"] >= 1


async def test_research_run_noop_when_no_pending_jobs(client):
    response = client.post("/api/internal/research/run", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["jobs_attempted"] == 0
    assert body["jobs_completed"] == 0


async def test_research_run_web_prefer_overrides_service_web(
    client, monkeypatch
) -> None:
    """When `web_prefer` is supplied, the endpoint must rebuild the
    web provider through the factory (same seam as `use_mock_web`).
    """
    job_id = await _seed_research_job(client)
    captured: dict[str, object] = {}

    def fake_build(settings, *, prefer: str = "auto"):
        captured["prefer"] = prefer
        return _RecordingWebProvider("recorder")

    from app.services.research import web_data as web_data_mod

    monkeypatch.setattr(web_data_mod, "build_web_data_provider", fake_build)
    response = client.post(
        "/api/internal/research/run",
        json={"web_prefer": "browser_use", "use_mock_llm": True},
    )
    assert response.status_code == 200
    assert captured["prefer"] == "browser_use"


async def test_research_run_use_mock_web_beats_web_prefer(
    client, monkeypatch
) -> None:
    """`use_mock_web=True` must always win over `web_prefer` — back-compat."""
    job_id = await _seed_research_job(client)
    called = {"factory": 0}

    def fake_build(settings, *, prefer: str = "auto"):
        called["factory"] += 1
        return _RecordingWebProvider("recorder")

    from app.services.research import web_data as web_data_mod

    monkeypatch.setattr(web_data_mod, "build_web_data_provider", fake_build)
    response = client.post(
        "/api/internal/research/run",
        json={
            "use_mock_web": True,
            "web_prefer": "browser_use",
            "use_mock_llm": True,
        },
    )
    assert response.status_code == 200
    # Factory should NOT have been consulted when use_mock_web wins.
    assert called["factory"] == 0


# ---------------------------------------------------------------------------
# /api/internal/research/run/{job_id}
# ---------------------------------------------------------------------------
async def test_research_run_one_job_returns_payload(client):
    job_id = await _seed_research_job(client)
    response = client.post(
        f"/api/internal/research/run/{job_id}",
        json={"use_mock_web": True, "use_mock_llm": True},
    )
    assert response.status_code == 200
    body = response.json()
    for key in (
        "job_id",
        "opportunity_id",
        "status",
        "recommendation",
        "confidence",
        "sources_count",
        "warnings",
        "error",
    ):
        assert key in body, f"missing field {key}"
    assert body["status"] == "completed"
    assert body["job_id"] == job_id
    assert body["sources_count"] >= 1


async def test_research_run_one_job_unknown_id_returns_404(client):
    response = client.post("/api/internal/research/run/424242", json={})
    assert response.status_code == 404


async def test_research_run_one_job_can_be_re_processed(client):
    """Re-running a completed job should return completed again (idempotent)."""
    job_id = await _seed_research_job(client)
    first = client.post(
        f"/api/internal/research/run/{job_id}",
        json={"use_mock_web": True, "use_mock_llm": True},
    )
    assert first.status_code == 200
    # The service re-marks the job as running → completed and overwrites
    # the ResearchReport rows (we only assert no error here).
    second = client.post(
        f"/api/internal/research/run/{job_id}",
        json={"use_mock_web": True, "use_mock_llm": True},
    )
    assert second.status_code == 200


# ---------------------------------------------------------------------------
# /api/internal/research/cancel/{job_id}
# ---------------------------------------------------------------------------
async def test_research_cancel_pending_returns_true(client):
    job_id = await _seed_research_job(client)
    response = client.post(f"/api/internal/research/cancel/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body == {"job_id": job_id, "cancelled": True}


async def test_research_cancel_unknown_returns_false(client):
    response = client.post("/api/internal/research/cancel/424242")
    assert response.status_code == 200
    assert response.json() == {"job_id": 424242, "cancelled": False}


async def test_research_cancel_after_completion_returns_false(client):
    job_id = await _seed_research_job(client)
    # Complete the job first.
    run = client.post(
        f"/api/internal/research/run/{job_id}",
        json={"use_mock_web": True, "use_mock_llm": True},
    )
    assert run.status_code == 200
    # Now try to cancel — should refuse.
    response = client.post(f"/api/internal/research/cancel/{job_id}")
    assert response.status_code == 200
    assert response.json()["cancelled"] is False


# ---------------------------------------------------------------------------
# Phase 11 — web_prefer on the single-job endpoint
# ---------------------------------------------------------------------------
async def test_research_run_one_job_web_prefer_overrides_service_web(
    client, monkeypatch
) -> None:
    """The /{job_id} endpoint must also honour web_prefer."""
    job_id = await _seed_research_job(client)
    captured: dict[str, object] = {}

    def fake_build(settings, *, prefer: str = "auto"):
        captured["prefer"] = prefer
        return _RecordingWebProvider("recorder")

    from app.services.research import web_data as web_data_mod

    monkeypatch.setattr(web_data_mod, "build_web_data_provider", fake_build)
    response = client.post(
        f"/api/internal/research/run/{job_id}",
        json={"web_prefer": "browser_use"},
    )
    assert response.status_code == 200
    assert captured["prefer"] == "browser_use"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
class _RecordingWebProvider:
    """Tiny stand-in WebDataProvider — exists so we can verify the
    factory is called and assigned to `service.web`."""

    name = "recorder"

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        return

    async def search(self, query: str, *, limit: int = 5) -> list:  # pragma: no cover
        return []

    async def scrape(self, url: str):  # pragma: no cover
        raise NotImplementedError
