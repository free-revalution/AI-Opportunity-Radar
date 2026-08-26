"""Tests for the synthesizer (cluster → Opportunity)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import RawItem, Source
from app.services.clustering.synthesizer import (
    SynthesisResult,
    _stable_slug,
    aggregate_category,
    aggregate_summary,
    pick_representative,
    synthesize_cluster,
)


async def _mk_raw_item(
    session,
    *,
    external_id: str,
    title: str,
    url: str = "https://example.com/x",
    content: str | None = None,
    metadata: dict | None = None,
    source_id: int = 1,
    published_at: datetime | None = None,
) -> RawItem:
    from app.repositories import compute_content_hash

    item = RawItem(
        source_id=source_id,
        external_id=external_id,
        url=url,
        title=title,
        content=content,
        author=None,
        published_at=published_at,
        content_hash=compute_content_hash(url, title),
        metadata_json=metadata or {},
    )
    session.add(item)
    await session.flush()
    return item


@pytest.fixture
async def source(sqlite_session):
    s = Source(name="test-source", type="api", url="https://example.com", enabled=True)
    sqlite_session.add(s)
    await sqlite_session.flush()
    return s


async def test_synthesize_cluster_requires_at_least_one_item():
    with pytest.raises(ValueError):
        synthesize_cluster([])


async def test_synthesize_cluster_sets_required_fields(sqlite_session, source):
    item = await _mk_raw_item(
        sqlite_session,
        external_id="a1",
        title="AI Sales Coach",
        content="Real-time call summaries.",
        metadata={"stars": 100},
        source_id=source.id,
    )
    result = synthesize_cluster([item])

    assert isinstance(result, SynthesisResult)
    assert result.opportunity_fields["title"] == "AI Sales Coach"
    assert result.opportunity_fields["slug"].startswith("opp-")
    assert result.opportunity_fields["source_count"] == 1
    assert result.opportunity_fields["status"] == "detected"
    assert result.opportunity_fields["total_score"] == 0.0
    assert result.representative.id == item.id


async def test_synthesize_cluster_aggregates_summary(sqlite_session, source):
    a = await _mk_raw_item(
        sqlite_session,
        external_id="a1",
        title="AI Coach",
        content="Real-time summaries.",
    )
    b = await _mk_raw_item(
        sqlite_session,
        external_id="b1",
        title="AI Coach Pro",
        content="CRM enrichment.",
    )
    result = synthesize_cluster([a, b])
    summary = result.opportunity_fields["summary"]
    assert summary is not None
    assert "AI Coach" in summary
    assert "CRM enrichment" in summary


async def test_synthesize_cluster_summary_capped_at_max(sqlite_session, source):
    a = await _mk_raw_item(
        sqlite_session,
        external_id="a1",
        title="Big",
        content="x" * 5000,
    )
    result = synthesize_cluster([a])
    assert len(result.opportunity_fields["summary"]) <= 1000


async def test_representative_is_highest_engagement(sqlite_session, source):
    low = await _mk_raw_item(
        sqlite_session,
        external_id="low",
        title="Low Engagement",
        content="c",
        metadata={"stars": 5},
    )
    high = await _mk_raw_item(
        sqlite_session,
        external_id="high",
        title="High Engagement",
        content="c",
        metadata={"stars": 5000, "comments": 200},
    )
    rep = pick_representative([low, high])
    assert rep.id == high.id


async def test_representative_ties_broken_by_oldest_published(sqlite_session, source):
    older = await _mk_raw_item(
        sqlite_session,
        external_id="older",
        title="Same Stars",
        content="c",
        metadata={"stars": 100},
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    newer = await _mk_raw_item(
        sqlite_session,
        external_id="newer",
        title="Same Stars 2",
        content="c",
        metadata={"stars": 100},
        published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    rep = pick_representative([newer, older])
    assert rep.id == older.id


async def test_stable_slug_changes_with_member_set(sqlite_session, source):
    a = await _mk_raw_item(sqlite_session, external_id="a", title="A")
    b = await _mk_raw_item(sqlite_session, external_id="b", title="B")
    c = await _mk_raw_item(sqlite_session, external_id="c", title="C")

    slug_ab = _stable_slug([a, b])
    slug_bc = _stable_slug([b, c])
    assert slug_ab != slug_bc
    assert slug_ab.startswith("opp-")
    assert _stable_slug([a, b]) == slug_ab  # deterministic


async def test_aggregate_category_picks_most_common(sqlite_session, source):
    a = await _mk_raw_item(
        sqlite_session,
        external_id="a",
        title="A",
        metadata={"topics": ["ai", "sales"]},
    )
    b = await _mk_raw_item(
        sqlite_session,
        external_id="b",
        title="B",
        metadata={"topics": ["ai"]},
    )
    c = await _mk_raw_item(
        sqlite_session,
        external_id="c",
        title="C",
        metadata={"topics": ["ai"]},
    )
    assert aggregate_category([a, b, c]) == "ai"


async def test_aggregate_category_returns_none_when_no_metadata(sqlite_session, source):
    a = await _mk_raw_item(sqlite_session, external_id="a", title="A")
    assert aggregate_category([a]) is None


async def test_aggregate_summary_skips_items_without_content(sqlite_session, source):
    a = await _mk_raw_item(sqlite_session, external_id="a", title="A", content="Hello")
    b = await _mk_raw_item(sqlite_session, external_id="b", title="B", content=None)
    summary = aggregate_summary([a, b])
    assert "Hello" in summary
    assert summary.count("[B]") == 0


async def test_link_relevance_decays_with_rank():
    """1.0 for representative, 0.5 for 2nd, 0.33 for 3rd, ..."""
    items = []  # we don't need real rows for the count check
    # synthesize_cluster builds the links; use stubs.
    from app.services.clustering.synthesizer import SynthesisResult

    synthesis = SynthesisResult(
        opportunity_fields={"slug": "x", "title": "t"},
        links=[
            {"raw_item_id": 1, "relevance": 1.0},
            {"raw_item_id": 2, "relevance": 0.5},
            {"raw_item_id": 3, "relevance": 0.3333333333},
        ],
        representative=None,  # type: ignore[arg-type]
    )
    relevances = [l["relevance"] for l in synthesis.links]
    assert relevances[0] == 1.0
    assert relevances[1] == pytest.approx(0.5)
    assert relevances[2] == pytest.approx(1.0 / 3, rel=1e-3)
