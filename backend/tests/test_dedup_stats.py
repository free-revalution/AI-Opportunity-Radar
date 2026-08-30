"""Phase 25 v2.1 — /status dedup funnel (今日采集 → URL 去重 → 聚类).

Seeds RawItem + Opportunity rows with deterministic timestamps
relative to ``now`` and verifies ``/api/internal/status``'s
``dedup_today`` block returns the expected counts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.models import Opportunity, RawItem, Source


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


@pytest.fixture
def seeded_db(sqlite_session: Any) -> Any:
    """Seed 5 today-fresh RawItems + 3 today-fresh Opportunities.

    Of the 5 RawItems, 2 share a URL (so dedup should report 4 unique).
    The third Opportunity is older (2 days ago) so it does NOT count.
    """
    async def _seed() -> None:
        # — single Source for FK integrity (RawItem.source_id is non-null
        # based on the model). Tests for sources cover the multi-source path.
        src = Source(
            name="Dedup Test",
            type="rss",
            url="https://example.com",
            enabled=True,
            compliance_level="B",
        )
        sqlite_session.add(src)
        await sqlite_session.flush()

        now = _now()
        today = now.replace(hour=10, minute=0, second=0, microsecond=0)
        # — 5 RawItems, 2 share URL "https://x.com/1"
        for i, url in enumerate(
            [
                "https://x.com/1",
                "https://x.com/2",
                "https://x.com/1",  # dup
                "https://x.com/3",
                "https://x.com/4",
            ]
        ):
            ri = RawItem(
                source_id=src.id,
                external_id=f"ext-{i}",
                url=url,
                title=f"item {i}",
                content_hash=f"hash-{i}",
                fetched_at=today + timedelta(minutes=i),
            )
            sqlite_session.add(ri)

        # — 3 today Opportunities
        for i in range(3):
            opp = Opportunity(
                title=f"opp {i}",
                slug=f"opp-{i}",
                created_at=today + timedelta(minutes=i),
                status="pending",
                total_score=70.0,
            )
            sqlite_session.add(opp)

        # — 1 old Opportunity (2 days ago) — must NOT be counted
        old_opp = Opportunity(
            title="old",
            slug="old-opp",
            created_at=now - timedelta(days=2),
            status="pending",
            total_score=70.0,
        )
        sqlite_session.add(old_opp)

        await sqlite_session.commit()

    asyncio.get_event_loop().run_until_complete(_seed())
    return sqlite_session


def test_status_dedup_today_counts(seeded_db: Any, client: Any) -> None:
    resp = client.get("/api/internal/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "dedup_today" in body
    stats = body["dedup_today"]
    # — 5 raw, 4 unique (https://x.com/1 dedup'd), 3 opps today
    assert stats["raw_items_collected"] == 5
    assert stats["unique_urls"] == 4
    assert stats["opportunities_created"] == 3
    assert "window_start" in stats


def test_status_dedup_today_empty_db(client: Any) -> None:
    resp = client.get("/api/internal/status")
    assert resp.status_code == 200
    stats = resp.json()["dedup_today"]
    assert stats["raw_items_collected"] == 0
    assert stats["unique_urls"] == 0
    assert stats["opportunities_created"] == 0
