"""Tests for Phase 17C — `GET /api/signals`.

Webhook auth + filter + pagination. The conftest sets
``APP_SECRET_KEY=""`` + ``RADAR_WEBHOOK_SECRET=""`` so the webhook
dependency short-circuits and accepts any caller.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import RawItem, Signal, Source

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_signal_via_api_seed(session, *, score: float, status: str = "verified") -> int:
    src = Source(name="test-src", type="rss", url="https://example.test")
    session.add(src)
    await session.flush()

    raw = RawItem(
        source_id=src.id,
        external_id=f"ext-{score}-{status}",
        url=f"https://example.test/{score}-{status}",
        title=f"raw-{score}",
        content_hash=f"hash-{score}-{status}",
    )
    session.add(raw)
    await session.flush()
    sig = Signal(
        raw_item_id=raw.id,
        signal_type="trend",
        keyword="AI",
        signal_score=score,
        confidence_score=score,
        status=status,
        title=f"signal-{score}",
        summary="...",
    )
    session.add(sig)
    await session.commit()
    return sig.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestListSignals:
    def test_empty_db_returns_zero(self, client):
        resp = client.get("/api/signals")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0

    def test_lists_seeded_signals(self, client):
        # Seed two signals via raw SQL.
        import asyncio

        from app.db import get_sessionmaker

        sessionmaker = client.sessionmaker  # type: ignore[attr-defined]

        async def _seed():
            async with sessionmaker() as session:
                await _seed_signal_via_api_seed(session, score=70.0)
                await _seed_signal_via_api_seed(session, score=90.0)

        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/signals")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        # Ordered by signal_score DESC.
        assert body["items"][0]["signal_score"] == 90.0
        assert body["items"][1]["signal_score"] == 70.0

    def test_filter_by_status(self, client):
        import asyncio

        sessionmaker = client.sessionmaker  # type: ignore[attr-defined]

        async def _seed():
            async with sessionmaker() as session:
                await _seed_signal_via_api_seed(session, score=50.0, status="verified")
                await _seed_signal_via_api_seed(session, score=60.0, status="rejected")

        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/signals?status=verified")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "verified"

    def test_filter_by_min_signal_score(self, client):
        import asyncio

        sessionmaker = client.sessionmaker  # type: ignore[attr-defined]

        async def _seed():
            async with sessionmaker() as session:
                await _seed_signal_via_api_seed(session, score=10.0)
                await _seed_signal_via_api_seed(session, score=50.0)
                await _seed_signal_via_api_seed(session, score=90.0)

        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/signals?min_signal_score=50")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2

    def test_min_signal_score_above_range_returns_422(self, client):
        resp = client.get("/api/signals?min_signal_score=150")
        assert resp.status_code == 422

    def test_limit_too_large_returns_422(self, client):
        resp = client.get("/api/signals?limit=10000")
        assert resp.status_code == 422
