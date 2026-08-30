"""Tests for Phase 19 — ``GET /api/admin/dashboard``.

Aggregates ContentOpportunity status breakdown + Signal health + the
last 20 ``content_opportunity_transition`` AuditLog rows into one
endpoint for the sole-operator dashboard.

Webhook auth: conftest clears ``APP_SECRET_KEY`` and
``RADAR_WEBHOOK_SECRET`` so the dep short-circuits and accepts all
callers — no extra auth test needed here (covered by
``test_internal_api``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import AuditLog, ContentOpportunity
from app.repositories import ContentOpportunityRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_co(
    session, *, status: str = "draft", signal_id: int = 42,
    compliance_blocked: bool = False, age_days: int = 0,
) -> int:
    """Insert one row and (optionally) rewind ``created_at`` so the
    "new today" / "recent 7d" buckets can be exercised."""
    repo = ContentOpportunityRepository(session)
    row = await repo.create(
        signal_id=signal_id,
        platform="xiaohongshu",
        tone="专业",
        hook="AI 颠覆跨境",
        content_score=85.0,
        status=status,
        metadata_json={"compliance_blocked": compliance_blocked},
    )
    if age_days:
        row.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
        await session.flush()
    await session.commit()
    return row.id


async def _seed_audit(
    session, *, action: str = "content_opportunity_transition",
    resource_id: str = "1", metadata_json: dict | None = None,
    result: str = "success", age_days: int = 0,
) -> int:
    row = AuditLog(
        actor_type="webhook",
        actor_id="webhook",
        action=action,
        resource_type="content_opportunity",
        resource_id=resource_id,
        result=result,
        metadata_json=metadata_json or {"from": "draft", "to": "approved"},
    )
    session.add(row)
    await session.flush()
    if age_days:
        row.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
        await session.flush()
    await session.commit()
    return row.id


# ---------------------------------------------------------------------------
# /dashboard — empty DB
# ---------------------------------------------------------------------------
class TestEmptyDB:
    def test_returns_zeros_and_empty_activity(self, client):
        resp = client.get("/api/admin/dashboard")
        assert resp.status_code == 200
        body = resp.json()
        co = body["content_opportunities"]
        sig = body["signals"]
        assert co["total"] == 0
        assert co["by_status"] == {
            "draft": 0, "approved": 0, "published": 0,
            "rejected": 0, "archived": 0,
        }
        assert co["blocked_review_queue"] == 0
        assert co["recent_7d_count"] == 0
        assert co["new_today"] == 0

        assert sig["total"] == 0
        assert sig["by_status"]["verified"] == 0
        assert sig["verified_count"] == 0
        assert body["recent_activity"] == []
        assert "generated_at" in body


# ---------------------------------------------------------------------------
# /dashboard — status breakdown + blocked queue
# ---------------------------------------------------------------------------
class TestAggregations:
    def test_by_status_counts_each_bucket(self, client):
        async def _seed():
            sessionmaker = client.sessionmaker  # type: ignore[attr-defined]
            async with sessionmaker() as session:
                for _ in range(3):
                    await _seed_co(session, status="draft", signal_id=1)
                for _ in range(2):
                    await _seed_co(session, status="approved", signal_id=2)
                await _seed_co(session, status="published", signal_id=3)
                await _seed_co(session, status="rejected", signal_id=4)

        import asyncio
        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/admin/dashboard")
        body = resp.json()["content_opportunities"]
        assert body["total"] == 7
        assert body["by_status"] == {
            "draft": 3, "approved": 2, "published": 1,
            "rejected": 1, "archived": 0,
        }

    def test_blocked_review_queue_only_counts_drafts(self, client):
        """One blocked draft + one blocked approved row → queue = 1."""
        async def _seed():
            sessionmaker = client.sessionmaker  # type: ignore[attr-defined]
            async with sessionmaker() as session:
                await _seed_co(session, status="draft",
                               compliance_blocked=True, signal_id=1)
                await _seed_co(session, status="approved",
                               compliance_blocked=True, signal_id=2)

        import asyncio
        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/admin/dashboard")
        assert resp.json()["content_opportunities"]["blocked_review_queue"] == 1

    def test_recent_7d_and_new_today_split_correctly(self, client):
        """1 row from today + 1 row from 3 days ago + 1 row from 10
        days ago → recent_7d_count=2, new_today=1."""
        async def _seed():
            sessionmaker = client.sessionmaker  # type: ignore[attr-defined]
            async with sessionmaker() as session:
                await _seed_co(session, status="draft", signal_id=1, age_days=0)
                await _seed_co(session, status="draft", signal_id=2, age_days=3)
                await _seed_co(session, status="draft", signal_id=3, age_days=10)

        import asyncio
        asyncio.get_event_loop().run_until_complete(_seed())

        body = client.get("/api/admin/dashboard").json()["content_opportunities"]
        assert body["total"] == 3
        assert body["recent_7d_count"] == 2
        assert body["new_today"] == 1


# ---------------------------------------------------------------------------
# /dashboard — recent_activity feed
# ---------------------------------------------------------------------------
class TestRecentActivity:
    def test_filters_out_non_transition_actions(self, client):
        """AuditLog contains other actions (activate / refresh etc).
        The dashboard feed must only return content_opportunity_transition."""
        async def _seed():
            sessionmaker = client.sessionmaker  # type: ignore[attr-defined]
            async with sessionmaker() as session:
                await _seed_audit(session, action="content_opportunity_transition",
                                  resource_id="1")
                await _seed_audit(session, action="content_opportunity_transition",
                                  resource_id="2")
                await _seed_audit(session, action="content_opportunity_transition",
                                  resource_id="3")
                # noise:
                await _seed_audit(session, action="activate", resource_id="x")
                await _seed_audit(session, action="refresh", resource_id="y")

        import asyncio
        asyncio.get_event_loop().run_until_complete(_seed())

        body = client.get("/api/admin/dashboard").json()
        assert len(body["recent_activity"]) == 3
        assert all(
            it["action"] == "content_opportunity_transition"
            for it in body["recent_activity"]
        )

    def test_activity_rows_carry_metadata_and_resource_id(self, client):
        async def _seed():
            sessionmaker = client.sessionmaker  # type: ignore[attr-defined]
            async with sessionmaker() as session:
                await _seed_audit(
                    session,
                    resource_id="42",
                    metadata_json={
                        "from": "draft", "to": "approved", "actor": "test"
                    },
                )

        import asyncio
        asyncio.get_event_loop().run_until_complete(_seed())

        item = client.get("/api/admin/dashboard").json()["recent_activity"][0]
        assert item["actor_type"] == "webhook"
        assert item["resource_id"] == "42"
        assert item["result"] == "success"
        assert item["metadata_json"]["from"] == "draft"
        assert item["metadata_json"]["to"] == "approved"

    def test_activity_capped_at_20_rows(self, client):
        async def _seed():
            sessionmaker = client.sessionmaker  # type: ignore[attr-defined]
            async with sessionmaker() as session:
                for i in range(25):
                    await _seed_audit(
                        session, resource_id=str(i),
                        metadata_json={"from": "draft", "to": "approved"},
                    )

        import asyncio
        asyncio.get_event_loop().run_until_complete(_seed())

        body = client.get("/api/admin/dashboard").json()
        assert len(body["recent_activity"]) == 20

    def test_activity_ordered_newest_first(self, client):
        async def _seed():
            sessionmaker = client.sessionmaker  # type: ignore[attr-defined]
            async with sessionmaker() as session:
                old_id = await _seed_audit(
                    session, resource_id="old", age_days=2,
                    metadata_json={"from": "draft", "to": "approved"},
                )
                new_id = await _seed_audit(
                    session, resource_id="new", age_days=0,
                    metadata_json={"from": "draft", "to": "approved"},
                )

        import asyncio
        asyncio.get_event_loop().run_until_complete(_seed())

        activity = client.get("/api/admin/dashboard").json()["recent_activity"]
        assert len(activity) == 2
        # newest (age_days=0) comes first.
        assert activity[0]["resource_id"] == "new"
        assert activity[1]["resource_id"] == "old"


# ---------------------------------------------------------------------------
# /dashboard — Signal summary (light check, mirrors the CO logic)
# ---------------------------------------------------------------------------
class TestSignalSummary:
    def test_by_status_includes_all_buckets(self, client):
        from app.models import RawItem, Signal, Source

        async def _seed():
            sessionmaker = client.sessionmaker  # type: ignore[attr-defined]
            async with sessionmaker() as session:
                src = Source(name="t", type="rss", url="https://x")
                session.add(src)
                await session.flush()
                for i, st in enumerate(["verified", "verified", "rejected", "expired"]):
                    raw = RawItem(
                        source_id=src.id,
                        external_id=f"ext-{i}",
                        url=f"https://x/{i}",
                        title=f"r-{i}",
                        content_hash=f"h{i}",
                    )
                    session.add(raw)
                    await session.flush()
                    sig = Signal(
                        raw_item_id=raw.id,
                        signal_type="trend",
                        signal_score=80.0,
                        confidence_score=80.0,
                        status=st,
                    )
                    session.add(sig)
                await session.commit()

        import asyncio
        asyncio.get_event_loop().run_until_complete(_seed())

        sig = client.get("/api/admin/dashboard").json()["signals"]
        assert sig["total"] == 4
        assert sig["by_status"]["verified"] == 2
        assert sig["by_status"]["rejected"] == 1
        assert sig["by_status"]["expired"] == 1
        # Unseen statuses default to 0.
        assert sig["by_status"]["published"] == 0
        assert sig["verified_count"] == 2