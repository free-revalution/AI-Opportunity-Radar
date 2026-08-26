"""Tests for /api/research/{id} — covers the Phase 7 ResearchReport lookup."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Opportunity, ResearchReport, Source

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed(session_maker, *, slug: str = "ai-sales-coach") -> int:
    async with session_maker() as session:
        source = Source(
            name="r-src", type="api", url="https://example.com/r", enabled=True
        )
        session.add(source)
        await session.flush()
        opp = Opportunity(
            title="AI Sales Coach",
            slug=slug,
            summary="AI SaaS for SDRs.",
            category="AI SaaS",
            target_user="B2B sales leaders",
            source_count=3,
            trend_score=85.0,
            demand_score=80.0,
            monetization_score=78.0,
            competition_gap_score=70.0,
            china_gap_score=65.0,
            execution_score=72.0,
            total_score=80.0,
            status="research_complete",
        )
        session.add(opp)
        await session.flush()
        report = ResearchReport(
            opportunity_id=opp.id,
            executive_summary="AI sales coaches are growing fast.",
            market_analysis="Large expanding segment.",
            competition_analysis="Gong and Chorus dominate.",
            china_analysis="Local players emerging.",
            monetization_analysis="$99/seat/month.",
            mvp_analysis="8-week build on LLM API.",
            risk_analysis="Switching cost is the main barrier.",
            recommendation="recommend",
            confidence=0.78,
            sources_json={
                "items": [
                    {"url": "https://a.com/1", "title": "A"},
                    {"url": "https://b.com/2", "title": ""},
                ]
            },
        )
        session.add(report)
        await session.commit()
        return opp.id


# ---------------------------------------------------------------------------
# Lookup by id
# ---------------------------------------------------------------------------
async def test_research_by_numeric_id_returns_real_report(client):
    opp_id = await _seed(client.sessionmaker)
    response = client.get(f"/api/research/{opp_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(opp_id)
    assert body["opportunity_id"] == str(opp_id)
    assert body["executive_summary"].startswith("AI sales coaches")
    assert body["recommendation"] == "recommend"
    assert body["confidence"] == pytest.approx(0.78)
    assert len(body["sources"]) == 2
    assert body["sources"][0]["url"] == "https://a.com/1"


async def test_research_by_slug_returns_real_report(client):
    await _seed(client.sessionmaker, slug="ai-sales-coach")
    response = client.get("/api/research/ai-sales-coach")
    assert response.status_code == 200
    assert response.json()["executive_summary"].startswith("AI sales coaches")


async def test_research_demo_fallback_for_demo_id(client):
    response = client.get("/api/research/demo-001")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "demo-001"
    assert body["recommendation"] == "strongly_recommend"
    assert body["confidence"] > 0


# ---------------------------------------------------------------------------
# Pending fallback
# ---------------------------------------------------------------------------
async def test_research_returns_pending_fallback_when_no_report(client):
    async with client.sessionmaker() as session:
        source = Source(
            name="p-src", type="api", url="https://example.com/p", enabled=True
        )
        session.add(source)
        await session.flush()
        opp = Opportunity(
            title="Pending Opp",
            slug="pending-opp",
            summary="Waiting for research.",
            category="AI SaaS",
            target_user="Founders",
            source_count=0,
            trend_score=70.0,
            demand_score=70.0,
            monetization_score=70.0,
            competition_gap_score=70.0,
            china_gap_score=70.0,
            execution_score=70.0,
            total_score=70.0,
            status="research_eligible",
        )
        session.add(opp)
        await session.commit()
        opp_id = opp.id

    response = client.get(f"/api/research/{opp_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["pending"] is True
    assert "Deep research has not produced a report yet" in body["executive_summary"]
    assert body["recommendation"] == "recommend"  # 70 → recommend


async def test_research_unknown_id_returns_404(client):
    response = client.get("/api/research/does-not-exist")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Notifications public route
# ---------------------------------------------------------------------------
async def test_notifications_recent_returns_items(client):
    async with client.sessionmaker() as session:
        from datetime import datetime, timezone

        from app.models import Notification

        session.add(
            Notification(
                channel="telegram",
                payload={"kind": "digest", "chat_id": "chat-1", "entry_ids": [1, 2]},
                delivered_at=datetime.now(timezone.utc),
                error=None,
            )
        )
        await session.commit()
    response = client.get("/api/notifications/recent?limit=5")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["items"][0]["channel"] == "telegram"
    assert body["items"][0]["payload"]["kind"] == "digest"


async def test_notifications_recent_filters_by_channel(client):
    response = client.get("/api/notifications/recent?channel=telegram")
    assert response.status_code == 200
    # No rows in this DB.
    assert response.json() == {"count": 0, "items": []}


async def test_notifications_recent_rejects_bad_limit(client):
    response = client.get("/api/notifications/recent?limit=0")
    assert response.status_code == 422
