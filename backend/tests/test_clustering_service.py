"""End-to-end tests for ClusteringService."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Opportunity, OpportunitySource, RawItem, Source
from app.repositories import RawItemRepository, compute_content_hash
from app.services.clustering import (
    Clusterer,
    ClusteringService,
    HashingEmbedder,
)


pytestmark = pytest.mark.asyncio


async def _seed_source(session, slug: str = "test-source") -> Source:
    s = Source(name=slug, type="api", url=f"https://example.com/{slug}", enabled=True)
    session.add(s)
    await session.flush()
    return s


async def _seed_raw_items(
    session,
    source: Source,
    items: list[dict],
) -> list[RawItem]:
    repo = RawItemRepository(session)
    rows: list[RawItem] = []
    for it in items:
        row, _ = await repo.upsert(
            source_id=source.id,
            external_id=it["external_id"],
            url=it["url"],
            title=it["title"],
            content=it.get("content"),
            metadata_json=it.get("metadata_json", {}),
        )
        rows.append(row)
    await session.flush()
    return rows


async def test_run_once_creates_opportunities_from_unclustered(sqlite_session):
    source = await _seed_source(sqlite_session, "hn")
    items = await _seed_raw_items(
        sqlite_session,
        source,
        [
            {
                "external_id": "1",
                "url": "https://news.ycombinator.com/item?id=1",
                "title": "Show HN: AI Sales Coach for SDRs",
                "content": "Real-time call summaries and CRM enrichment.",
                "metadata_json": {"points": 320},
            },
            {
                "external_id": "2",
                "url": "https://reddit.com/r/SaaS/comments/abc",
                "title": "I built an AI Sales Coach for SDRs — feedback?",
                "content": "Real-time call summaries, CRM enrichment, follow-ups.",
                "metadata_json": {"upvotes": 80},
            },
            {
                "external_id": "3",
                "url": "https://news.ycombinator.com/item?id=2",
                "title": "Authentic Italian pasta recipes from Bologna",
                "content": "Hand-rolled tagliatelle, slow-cooked ragù.",
                "metadata_json": {"points": 42},
            },
        ],
    )
    await sqlite_session.commit()

    service = ClusteringService(
        sqlite_session,
        embedder=HashingEmbedder(dim=256, ngram=3),
        clusterer=Clusterer(threshold=0.5),
    )
    report = await service.run_once()

    assert report.raw_items_seen == 3
    assert report.opportunities_created == 2  # one cluster + one singleton
    assert report.links_created == 3
    assert report.errors == []

    opps = (await sqlite_session.execute(select(Opportunity))).scalars().all()
    assert len(opps) == 2

    # Each opportunity has at least one link.
    links = (await sqlite_session.execute(select(OpportunitySource))).scalars().all()
    assert len(links) == 3


async def test_run_once_is_idempotent(sqlite_session):
    source = await _seed_source(sqlite_session)
    await _seed_raw_items(
        sqlite_session,
        source,
        [
            {
                "external_id": "1",
                "url": "https://example.com/a",
                "title": "AI Sales Coach for SDRs",
                "metadata_json": {"stars": 10},
            },
            {
                "external_id": "2",
                "url": "https://example.com/b",
                "title": "Authentic Italian pasta recipes from Bologna",
                "metadata_json": {"stars": 5},
            },
        ],
    )
    await sqlite_session.commit()

    service = ClusteringService(
        sqlite_session,
        embedder=HashingEmbedder(dim=512, ngram=3),
        clusterer=Clusterer(threshold=0.5),
    )

    first = await service.run_once()
    assert first.opportunities_created == 2

    second = await service.run_once()
    # No new opportunities, no new links — everything is already linked.
    assert second.opportunities_created == 0
    assert second.opportunities_updated == 0
    assert second.links_created == 0
    assert second.raw_items_seen == 0  # filtered by `not in` subquery

    opps = (await sqlite_session.execute(select(Opportunity))).scalars().all()
    assert len(opps) == 2


async def test_run_once_no_raw_items_is_noop(sqlite_session):
    service = ClusteringService(
        sqlite_session,
        embedder=HashingEmbedder(dim=64),
        clusterer=Clusterer(threshold=0.5),
    )
    report = await service.run_once()
    assert report.raw_items_seen == 0
    assert report.opportunities_created == 0


async def test_run_once_uses_settings_threshold(sqlite_session):
    source = await _seed_source(sqlite_session)
    await _seed_raw_items(
        sqlite_session,
        source,
        [
            {
                "external_id": "1",
                "url": "https://example.com/a",
                "title": "AI Sales Coach for SDRs",
                "content": "call summaries, CRM enrichment, follow-ups",
                "metadata_json": {},
            },
            {
                "external_id": "2",
                "url": "https://example.com/b",
                "title": "AI Sales Coach for SDRs — now with CRM",
                "content": "call summaries, CRM enrichment, follow-ups",
                "metadata_json": {},
            },
        ],
    )
    await sqlite_session.commit()

    # High threshold → 2 clusters (each its own opportunity).
    high_service = ClusteringService(
        sqlite_session,
        embedder=HashingEmbedder(dim=512, ngram=3),
        clusterer=Clusterer(threshold=0.999),
    )
    high_report = await high_service.run_once()
    assert high_report.clusters_formed == 2
    opps = (await sqlite_session.execute(select(Opportunity))).scalars().all()
    assert len(opps) == 2


async def test_run_once_reports_merged_clusters(sqlite_session):
    source = await _seed_source(sqlite_session)
    await _seed_raw_items(
        sqlite_session,
        source,
        [
            {
                "external_id": "1",
                "url": "https://example.com/a",
                "title": "AI Sales Coach",
                "content": "call summaries",
            },
            {
                "external_id": "2",
                "url": "https://example.com/b",
                "title": "AI Sales Coach",
                "content": "call summaries",
            },
            {
                "external_id": "3",
                "url": "https://example.com/c",
                "title": "AI Sales Coach",
                "content": "call summaries",
            },
        ],
    )
    await sqlite_session.commit()

    service = ClusteringService(
        sqlite_session,
        embedder=HashingEmbedder(dim=512, ngram=3),
        clusterer=Clusterer(threshold=0.5),
    )
    report = await service.run_once()
    assert report.clusters_formed == 1
    assert report.merged_clusters == 1
    assert report.opportunities_created == 1
    assert report.links_created == 3
