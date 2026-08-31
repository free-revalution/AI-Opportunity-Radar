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


async def test_ingestion_updates_source_last_success_at(sqlite_session):
    """Phase 29 regression — after a successful /run, every fetched
    Source row must have ``last_success_at`` set to a non-NULL timestamp.
    The bug was that :meth:`IngestionService._persist` only called
    ``source_repo.upsert()`` (which writes name/type/url/enabled) and
    never touched ``last_success_at``. The bot's ``/sources`` reply
    therefore showed "尚未采集" for every source, and the
    ``/api/internal/sources/healthy`` endpoint returned
    ``last_success_at: null`` regardless of how many /runs had
    completed.
    """
    from sqlalchemy import select as _select

    # Pre-flight — Source rows start with last_success_at NULL.
    service = IngestionService(
        sqlite_session,
        source_slugs=["github", "hackernews"],
        mock=True,
    )
    report = await service.run_once()
    assert report.sources_succeeded == 2

    rows = (
        await sqlite_session.execute(
            _select(Source).where(Source.name.in_(["GitHub", "Hacker News"]))
        )
    ).scalars().all()
    assert len(rows) == 2
    for row in rows:
        assert row.last_success_at is not None, (
            f"Source {row.name} has last_success_at=NULL after a "
            f"successful /run — the _persist() fix did not apply."
        )