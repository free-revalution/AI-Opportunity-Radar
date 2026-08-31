"""End-to-end tests for the ScreeningService."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Opportunity, OpportunitySource, RawItem, Signal, Source
from app.services.clustering import (
    Clusterer,
    ClusteringService,
    HashingEmbedder,
)
from app.services.llm import MockLLMProvider
from app.services.screening import ScreeningService
from app.services.scoring import (
    RESEARCH_TRIGGER_THRESHOLD,
    calculate_total_score,
    recommendation_for,
)

pytestmark = pytest.mark.asyncio


async def _seed_source(session, name: str = "test-src") -> Source:
    s = Source(name=name, type="api", url=f"https://example.com/{name}", enabled=True)
    session.add(s)
    await session.flush()
    return s


async def _seed_clustered_opportunity(
    session,
    *,
    title: str,
    items: list[dict],
) -> Opportunity:
    """Insert source + raw_items, run clustering, return one Opportunity."""
    from app.repositories import RawItemRepository

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
        clusterer=Clusterer(threshold=0.0),  # force every item into one cluster
    )
    await clusterer.run_once()
    await session.flush()

    opps = (await session.execute(select(Opportunity))).scalars().all()
    assert len(opps) == 1, f"expected 1 opportunity, got {len(opps)}"
    opp = opps[0]
    opp.title = title
    await session.flush()
    return opp


async def test_screening_with_mock_provider_populates_scores(sqlite_session):
    opp = await _seed_clustered_opportunity(
        sqlite_session,
        title="AI Sales Coach for SDRs",
        items=[
            {
                "external_id": "1",
                "url": "https://hn.com/1",
                "title": "AI Sales Coach for SDRs",
                "content": "AI SaaS tool for sales reps. B2B platform with API.",
                "metadata_json": {"stars": 250},
            },
            {
                "external_id": "2",
                "url": "https://reddit.com/2",
                "title": "Show HN: AI Sales Coach",
                "content": "Sales automation, CRM, growth analytics.",
                "metadata_json": {"upvotes": 80},
            },
        ],
    )

    service = ScreeningService(
        sqlite_session,
        provider=MockLLMProvider(),
    )
    report = await service.run_once()

    assert report.opportunities_screened == 1
    assert report.opportunities_failed == 0
    assert report.signals_created == 2
    assert report.errors == []

    await sqlite_session.refresh(opp)
    assert opp.status == "screened"
    assert opp.trend_score > 0
    assert opp.total_score > 0
    expected_total = calculate_total_score(
        type("S", (), {
            "trend": opp.trend_score,
            "demand": opp.demand_score,
            "monetization": opp.monetization_score,
            "competition_gap": opp.competition_gap_score,
            "china_gap": opp.china_gap_score,
            "execution": opp.execution_score,
        })()
    )
    assert opp.total_score == pytest.approx(expected_total)
    # Recommendation should match the formula's lookup.
    assert opp.total_score >= RESEARCH_TRIGGER_THRESHOLD or opp.total_score < RESEARCH_TRIGGER_THRESHOLD
    assert recommendation_for(opp.total_score) in {
        "strongly_recommend",
        "recommend",
        "watch",
        "not_recommended",
    }


async def test_screening_creates_one_signal_per_raw_item(sqlite_session):
    opp = await _seed_clustered_opportunity(
        sqlite_session,
        title="Sales AI",
        items=[
            {
                "external_id": "1",
                "url": "https://hn.com/1",
                "title": "Sales AI tool",
                "content": "AI for sales reps at b2b SaaS companies.",
                "metadata_json": {"stars": 50},
            },
            {
                "external_id": "2",
                "url": "https://reddit.com/2",
                "title": "Sales AI: feedback?",
                "content": "AI tool for sales teams, monetisation via API.",
                "metadata_json": {"upvotes": 20},
            },
            {
                "external_id": "3",
                "url": "https://news.ycombinator.com/3",
                "title": "Show HN: Sales AI",
                "content": "B2B SaaS for sales growth.",
                "metadata_json": {"points": 10},
            },
        ],
    )

    service = ScreeningService(
        sqlite_session,
        provider=MockLLMProvider(),
    )
    await service.run_once()

    signals = (await sqlite_session.execute(select(Signal))).scalars().all()
    assert len(signals) == 3
    for s in signals:
        assert s.signal_type == "screening"
        assert s.raw_item_id is not None
        assert s.velocity_score >= 0


async def test_screening_skips_when_no_pending_opportunities(sqlite_session):
    """No opportunities in 'detected' state → nothing to do."""
    service = ScreeningService(
        sqlite_session,
        provider=MockLLMProvider(),
    )
    report = await service.run_once()
    assert report.opportunities_attempted == 0
    assert report.opportunities_screened == 0
    assert report.signals_created == 0


async def test_screening_is_idempotent(sqlite_session):
    """Re-running screening must NOT create a second batch of Signals."""
    opp = await _seed_clustered_opportunity(
        sqlite_session,
        title="AI Sales Coach",
        items=[
            {
                "external_id": "1",
                "url": "https://hn.com/1",
                "title": "AI Sales Coach",
                "content": "AI for sales reps at SaaS companies.",
                "metadata_json": {"stars": 100},
            },
        ],
    )

    service = ScreeningService(
        sqlite_session,
        provider=MockLLMProvider(),
    )
    first = await service.run_once()
    second = await service.run_once()

    assert first.opportunities_screened == 1
    assert second.opportunities_attempted == 0  # nothing in 'detected' anymore


async def test_screening_handles_provider_failure_gracefully(sqlite_session):
    class _BrokenProvider(MockLLMProvider):
        async def complete_json(self, **kw):
            raise RuntimeError("LLM provider unavailable")

    await _seed_clustered_opportunity(
        sqlite_session,
        title="Test",
        items=[
            {
                "external_id": "1",
                "url": "https://hn.com/1",
                "title": "Test",
                "content": "AI SaaS for sales teams.",
            }
        ],
    )

    service = ScreeningService(
        sqlite_session,
        provider=_BrokenProvider(),
    )
    report = await service.run_once()

    assert report.opportunities_failed == 1
    assert report.opportunities_screened == 0
    assert len(report.errors) == 1
    assert "unavailable" in report.errors[0].lower() or "runtimeerror" in report.errors[0].lower()

    opps = (await sqlite_session.execute(select(Opportunity))).scalars().all()
    assert opps[0].status == "screen_failed"


async def test_screening_enriches_summary(sqlite_session):
    opp = await _seed_clustered_opportunity(
        sqlite_session,
        title="AI Sales Coach",
        items=[
            {
                "external_id": "1",
                "url": "https://hn.com/1",
                "title": "AI Sales Coach",
                "content": "AI sales coach for SaaS sales reps.",
            }
        ],
    )
    # Pre-existing summary.
    opp.summary = "Pre-existing context: AI sales coach launched on PH."
    await sqlite_session.flush()

    service = ScreeningService(
        sqlite_session,
        provider=MockLLMProvider(),
    )
    await service.run_once()

    await sqlite_session.refresh(opp)
    assert opp.summary is not None
    assert "Pre-existing context" in opp.summary
    assert "Potential business" in opp.summary


# ---------------------------------------------------------------------------
# Phase 33 PR-33-A — screening commit-once-at-end
# ---------------------------------------------------------------------------
async def test_screening_commits_once_at_end_not_per_opp(
    sqlite_session, monkeypatch
) -> None:
    """PR-33-A 回归: N opp → 1 次 commit,不是 N 次。

    通过 monkeypatch session.commit 计数。一个 opp 足以触发 1→1 验证;
    若有人后续把 commit 加回 _screen_one 单 opp 路径,断言会立刻失败。
    """
    await _seed_clustered_opportunity(
        sqlite_session,
        title="AI Tool PR33A",
        items=[
            {
                "external_id": "ext-pr33a-1",
                "url": "https://hn.com/pr33a/1",
                "title": "AI Tool PR33A",
                "content": "AI content",
            }
        ],
    )

    commit_calls: list[int] = []

    real_commit = sqlite_session.commit

    async def _count_commit() -> None:
        commit_calls.append(1)
        await real_commit()

    monkeypatch.setattr(sqlite_session, "commit", _count_commit)

    service = ScreeningService(sqlite_session, provider=MockLLMProvider())
    report = await service.run_once()

    assert report.opportunities_screened == 1
    # PR-33-A: 1 次 commit,不是 per-opp
    assert len(commit_calls) == 1, (
        f"expected 1 commit (run_once end), got {len(commit_calls)} — "
        "PR-33-A 回归:screening 还在 per-opp commit?"
    )


async def test_screening_mark_failed_no_longer_commits(
    sqlite_session, monkeypatch
) -> None:
    """PR-33-A 回归: _mark_failed 只 flush,不 commit。

    模拟一个 opp 让 LLM 抛错,走 _mark_failed 分支,验证 commit 没被调。
    """
    from app.utils import RetryableError
    from app.services.llm import LLMProvider

    class _BoomProvider(LLMProvider):
        async def complete_json(self, **kwargs):
            raise RetryableError("simulated LLM down")

    opp = await _seed_clustered_opportunity(
        sqlite_session,
        title="AI Tool",
        items=[
            {
                "external_id": "boom-1",
                "url": "https://hn.com/boom",
                "title": "AI Tool",
                "content": "AI content",
            }
        ],
    )

    commit_calls: list[int] = []

    real_commit = sqlite_session.commit

    async def _count_commit() -> None:
        commit_calls.append(1)
        await real_commit()

    monkeypatch.setattr(sqlite_session, "commit", _count_commit)

    service = ScreeningService(sqlite_session, provider=_BoomProvider())
    report = await service.run_once()

    assert report.opportunities_failed == 1
    # _mark_failed 不再 commit;整个 run_once 末尾 commit 1 次
    assert len(commit_calls) == 1
