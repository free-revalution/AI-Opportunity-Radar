"""End-to-end ingestion tests — connectors → DB."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import RawItem, Source
from app.services.ingestion import IngestionService


pytestmark = pytest.mark.asyncio


async def test_ingestion_persists_all_items_in_mock_mode(sqlite_session):
    service = IngestionService(
        sqlite_session,
        source_slugs=["github", "hackernews", "reddit", "rss", "producthunt", "youtube"],
        mock=True,
    )
    report = await service.run_once()

    assert report.sources_attempted == 6
    assert report.sources_succeeded == 6
    assert report.sources_failed == 0
    assert report.items_seen > 0
    assert report.items_inserted > 0

    rows = (await sqlite_session.execute(select(RawItem))).scalars().all()
    assert len(rows) == report.items_inserted

    sources = (await sqlite_session.execute(select(Source))).scalars().all()
    assert {s.name for s in sources} == {
        "GitHub", "Hacker News", "Reddit", "Generic RSS", "Product Hunt", "YouTube"
    }


async def test_ingestion_is_idempotent(sqlite_session):
    """Running ingestion twice must NOT create duplicate RawItem rows."""
    # Phase 24 — first ingestion creates the Source row with the default
    # compliance_level="E" (block), which would trip the pre-fetch gate
    # on the second run. Whitelist it explicitly so this test exercises
    # only the dedup path, not the compliance path.
    from sqlalchemy import select as _select

    service = IngestionService(
        sqlite_session,
        source_slugs=["github"],
        mock=True,
    )
    first = await service.run_once()
    src = (
        await sqlite_session.execute(
            _select(Source).where(Source.name.ilike("github"))
        )
    ).scalar_one()
    src.compliance_level = "A"
    await sqlite_session.flush()
    second = await service.run_once()

    assert first.items_inserted > 0
    assert second.items_inserted == 0
    assert second.items_skipped == first.items_inserted


async def test_ingestion_handles_unknown_slug_gracefully(sqlite_session):
    service = IngestionService(
        sqlite_session,
        source_slugs=["github", "nonexistent-source"],
        mock=True,
    )
    report = await service.run_once()
    assert report.sources_succeeded == 1
    assert any("nonexistent" in e for e in report.errors) is False  # silently ignored


async def test_ingestion_writes_metadata(sqlite_session):
    service = IngestionService(
        sqlite_session,
        source_slugs=["github"],
        mock=True,
    )
    await service.run_once()

    row = (await sqlite_session.execute(select(RawItem))).scalars().first()
    assert row is not None
    assert isinstance(row.metadata_json, dict)
    # github fixture items carry "stars".
    assert "stars" in row.metadata_json or "forks" in row.metadata_json