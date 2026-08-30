"""Tests for Phase 17C — `SignalRepository.list_recent`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import RawItem, Signal, Source
from app.repositories import SignalRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_source(session) -> int:
    src = Source(name="test-src", type="rss", url="https://example.test")
    session.add(src)
    await session.flush()
    return src.id


async def _seed_signal(
    session,
    *,
    signal_score: float,
    status: str = "discovered",
    created_at: datetime | None = None,
) -> Signal:
    """Insert a parent Source + RawItem + Signal row so the FK is satisfied."""
    src_id = await _seed_source(session)
    raw = RawItem(
        source_id=src_id,
        external_id=f"ext-{signal_score}-{status}",
        url=f"https://example.test/{signal_score}",
        title="seed",
        content_hash=f"h{signal_score}",
    )
    session.add(raw)
    await session.flush()

    sig = Signal(
        raw_item_id=raw.id,
        signal_type="trend",
        keyword="AI",
        signal_score=signal_score,
        confidence_score=signal_score,
        status=status,
        title=f"signal-{signal_score}",
        summary="...",
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(sig)
    await session.flush()
    return sig


# ---------------------------------------------------------------------------
# list_recent
# ---------------------------------------------------------------------------
class TestListRecent:
    async def test_empty_table_returns_zero(self, sqlite_session):
        repo = SignalRepository(sqlite_session)
        rows, total = await repo.list_recent()
        assert rows == []
        assert total == 0

    async def test_orders_by_signal_score_desc(self, sqlite_session):
        await _seed_signal(sqlite_session, signal_score=10.0)
        await _seed_signal(sqlite_session, signal_score=80.0)
        await _seed_signal(sqlite_session, signal_score=50.0)
        await sqlite_session.commit()

        repo = SignalRepository(sqlite_session)
        rows, total = await repo.list_recent()
        assert total == 3
        assert [r.signal_score for r in rows] == [80.0, 50.0, 10.0]

    async def test_filter_by_status(self, sqlite_session):
        await _seed_signal(sqlite_session, signal_score=70.0, status="discovered")
        await _seed_signal(sqlite_session, signal_score=80.0, status="verified")
        await _seed_signal(sqlite_session, signal_score=90.0, status="verified")
        await sqlite_session.commit()

        repo = SignalRepository(sqlite_session)
        rows, total = await repo.list_recent(status="verified")
        assert total == 2
        assert all(r.status == "verified" for r in rows)

    async def test_filter_by_min_signal_score(self, sqlite_session):
        await _seed_signal(sqlite_session, signal_score=10.0)
        await _seed_signal(sqlite_session, signal_score=50.0)
        await _seed_signal(sqlite_session, signal_score=90.0)
        await sqlite_session.commit()

        repo = SignalRepository(sqlite_session)
        rows, total = await repo.list_recent(min_signal_score=50.0)
        assert total == 2
        assert all(r.signal_score >= 50.0 for r in rows)

    async def test_pagination(self, sqlite_session):
        for i in range(5):
            await _seed_signal(sqlite_session, signal_score=float(i * 10))
        await sqlite_session.commit()

        repo = SignalRepository(sqlite_session)
        page1, total = await repo.list_recent(limit=2, offset=0)
        page2, _ = await repo.list_recent(limit=2, offset=2)
        assert total == 5
        assert len(page1) == 2
        assert len(page2) == 2
