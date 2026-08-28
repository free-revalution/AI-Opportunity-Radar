"""Tests for /api/internal/notifications/{digest, opportunity, history}."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Notification, Opportunity, ResearchReport
from app.services.notification import MockTelegramProvider

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_opportunity(
    session_factory,
    *,
    title: str,
    slug: str,
    total_score: float,
    recommendation: str = "recommend",
    with_report: bool = True,
    summary: str = "An AI SaaS that helps B2B sales teams.",
) -> int:
    async with session_factory() as session:
        opp = Opportunity(
            title=title,
            slug=slug,
            summary=summary,
            category="AI SaaS",
            target_user="B2B sales leaders",
            source_count=4,
            trend_score=80.0,
            demand_score=75.0,
            monetization_score=70.0,
            competition_gap_score=65.0,
            china_gap_score=60.0,
            execution_score=70.0,
            total_score=total_score,
            status="research_complete" if with_report else "scored",
        )
        session.add(opp)
        await session.flush()
        if with_report:
            session.add(
                ResearchReport(
                    opportunity_id=opp.id,
                    executive_summary="Synthesised research.",
                    recommendation=recommendation,
                    confidence=0.7,
                    sources_json={"items": []},
                )
            )
        await session.commit()
        return opp.id


# ---------------------------------------------------------------------------
# /api/internal/notifications/digest/preview
# ---------------------------------------------------------------------------
async def test_digest_preview_returns_text(client):
    opp_id = await _seed_opportunity(
        client.sessionmaker,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
    )
    response = client.post(
        "/api/internal/notifications/digest/preview",
        json={"max_entries": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text_chars"] > 0
    slugs = [e["slug"] for e in body["entries"]]
    assert "ai-sales-coach" in slugs


async def test_digest_preview_rejects_bad_min_score(client):
    response = client.post(
        "/api/internal/notifications/digest/preview",
        json={"min_score": "not-a-number"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /api/internal/notifications/digest/send
# ---------------------------------------------------------------------------
async def test_digest_send_dry_run_returns_preview_only(client):
    await _seed_opportunity(
        client.sessionmaker,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
    )
    response = client.post(
        "/api/internal/notifications/digest/send",
        json={"dry_run": True, "chat_id": "chat-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["notifications_attempted"] == 0
    assert body["preview"]
    # No Notification row should be persisted.
    async with client.sessionmaker() as session:
        rows = (
            await session.execute(select(Notification))
        ).scalars().all()
        assert rows == []


async def test_digest_send_dispatches_and_persists(client):
    await _seed_opportunity(
        client.sessionmaker,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
    )
    response = client.post(
        "/api/internal/notifications/digest/send",
        json={"chat_id": "chat-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["chat_id"] == "chat-1"
    assert body["notifications_delivered"] == 1
    assert body["notifications_failed"] == 0

    async with client.sessionmaker() as session:
        rows = (
            await session.execute(select(Notification))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].channel == "telegram"
    assert rows[0].delivered_at is not None
    assert rows[0].payload.get("kind") == "digest"


async def test_digest_send_without_chat_returns_noop(client):
    # Override settings so telegram_chat_id is empty.
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    settings.telegram_chat_id = ""
    try:
        response = client.post(
            "/api/internal/notifications/digest/send", json={}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["notifications_attempted"] == 0
        assert body["errors"]
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# /api/internal/notifications/opportunity/{id}/preview
# ---------------------------------------------------------------------------
async def test_opportunity_preview_returns_text(client):
    opp_id = await _seed_opportunity(
        client.sessionmaker,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
    )
    response = client.post(
        f"/api/internal/notifications/opportunity/{opp_id}/preview",
        json={},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["entry"]["opportunity_id"] == opp_id
    assert body["text_chars"] > 0


async def test_opportunity_preview_unknown_returns_404(client):
    response = client.post(
        "/api/internal/notifications/opportunity/424242/preview", json={}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /api/internal/notifications/opportunity/{id}/send
# ---------------------------------------------------------------------------
async def test_opportunity_send_dispatches(client):
    opp_id = await _seed_opportunity(
        client.sessionmaker,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
    )
    response = client.post(
        f"/api/internal/notifications/opportunity/{opp_id}/send",
        json={"chat_id": "chat-2", "extra_note": "now"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] is True
    assert body["chat_id"] == "chat-2"
    assert body["notification_id"] is not None
    assert body["message_id"] is not None
    assert body["error"] is None


async def test_opportunity_send_dry_run_persists_no_row(client):
    opp_id = await _seed_opportunity(
        client.sessionmaker,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
    )
    response = client.post(
        f"/api/internal/notifications/opportunity/{opp_id}/send",
        json={"chat_id": "chat-2", "dry_run": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] is False
    assert body["notification_id"] is None

    async with client.sessionmaker() as session:
        rows = (
            await session.execute(select(Notification))
        ).scalars().all()
    assert rows == []


async def test_opportunity_send_unknown_returns_404_with_error(client):
    response = client.post(
        "/api/internal/notifications/opportunity/424242/send",
        json={"chat_id": "chat-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] is False
    assert "not found" in (body["error"] or "")


# ---------------------------------------------------------------------------
# /api/internal/notifications/history
# ---------------------------------------------------------------------------
async def test_history_returns_recent_rows(client):
    await _seed_opportunity(
        client.sessionmaker,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
    )
    client.post(
        "/api/internal/notifications/digest/send",
        json={"chat_id": "chat-1"},
    )
    response = client.get("/api/internal/notifications/history?limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    assert body["items"][0]["channel"] == "telegram"
    assert body["items"][0]["delivered_at"] is not None


async def test_history_filters_by_channel(client):
    response = client.get(
        "/api/internal/notifications/history?channel=telegram"
    )
    assert response.status_code == 200
    assert response.json()["count"] == 0  # empty DB


async def test_history_with_no_rows_is_empty(client):
    response = client.get("/api/internal/notifications/history")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Phase 6 — channel param on /notifications/digest/send
# ---------------------------------------------------------------------------
async def test_digest_send_with_channel_feishu_uses_feishu_provider(client):
    """`POST /api/internal/notifications/digest/send` with `channel="feishu"`
    builds a Feishu mock + adapter under MOCK_EXTERNAL_SERVICES=true and
    persists `Notification.channel == "feishu"`.
    """
    await _seed_opportunity(
        client.sessionmaker,
        title="AI Sales Coach",
        slug="ai-sales-coach",
        total_score=82.0,
    )
    response = client.post(
        "/api/internal/notifications/digest/send",
        json={"channel": "feishu", "chat_id": "feishu-target"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["notifications_delivered"] == 1
    assert body["notifications_failed"] == 0

    async with client.sessionmaker() as session:
        rows = (
            await session.execute(select(Notification))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].channel == "feishu"
    assert rows[0].delivered_at is not None
    assert rows[0].payload.get("kind") == "digest"
