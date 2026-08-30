"""Tests for Phase 17E — `/api/admin/content_opportunities` endpoints.

Five endpoints:
  GET    /api/admin/content_opportunities                  (list + filter)
  GET    /api/admin/content_opportunities/{id}             (detail)
  POST   /api/admin/content_opportunities/{id}/approve     (draft → approved)
  POST   /api/admin/content_opportunities/{id}/reject      (* → rejected)
  POST   /api/admin/content_opportunities/{id}/publish     (approved → published)

Auth: ``X-Radar-Webhook`` — the conftest clears both ``APP_SECRET_KEY``
and ``RADAR_WEBHOOK_SECRET``, so the dependency short-circuits and
accepts every caller in tests.
"""

from __future__ import annotations

import pytest

from app.models import ContentOpportunity
from app.repositories import ContentOpportunityRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_co(
    session, *, status: str = "draft", signal_id: int = 42,
    compliance_blocked: bool = False,
) -> int:
    repo = ContentOpportunityRepository(session)
    row = await repo.create(
        signal_id=signal_id,
        platform="xiaohongshu",
        audience="creators",
        tone="专业",
        hook="AI 颠覆跨境",
        content_score=85.0,
        status=status,
        metadata_json={
            "compliance_blocked": compliance_blocked,
            "compliance_risk_score": 0.9 if compliance_blocked else 0.0,
            "compliance_risk_types": ["MEDICAL_ADVICE"] if compliance_blocked else [],
            "feishu_open_id": "ou_seed",
        },
    )
    await session.commit()
    return row.id


# ---------------------------------------------------------------------------
# GET list
# ---------------------------------------------------------------------------
class TestList:
    def test_empty_db(self, client):
        resp = client.get("/api/admin/content_opportunities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_returns_seeded_row(self, client):
        import asyncio
        sm = client.sessionmaker  # type: ignore[attr-defined]
        co_id = asyncio.get_event_loop().run_until_complete(
            _seed_co(sm(), status="draft")
        )
        resp = client.get("/api/admin/content_opportunities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == co_id
        assert item["status"] == "draft"
        assert item["platform"] == "xiaohongshu"
        assert item["compliance_blocked"] is False
        assert "metadata" in item

    def test_filter_by_status(self, client):
        import asyncio
        sm = client.sessionmaker  # type: ignore[attr-defined]

        async def _seed():
            await _seed_co(sm(), status="draft")
            await _seed_co(sm(), status="approved")
            await _seed_co(sm(), status="approved")

        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/admin/content_opportunities?status=approved")
        body = resp.json()
        assert body["total"] == 2
        assert all(it["status"] == "approved" for it in body["items"])

    def test_filter_by_compliance_blocked(self, client):
        """compliance_blocked is read from ``metadata_json`` in Python
        (the SQL filter is deferred to Phase 18 admin UI)."""
        import asyncio
        sm = client.sessionmaker  # type: ignore[attr-defined]

        async def _seed():
            await _seed_co(sm(), status="draft", compliance_blocked=False)
            await _seed_co(sm(), status="draft", compliance_blocked=True)

        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get(
            "/api/admin/content_opportunities?compliance_blocked=true"
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["compliance_blocked"] is True

    def test_pagination(self, client):
        import asyncio
        sm = client.sessionmaker  # type: ignore[attr-defined]

        async def _seed():
            for i in range(5):
                await _seed_co(sm(), signal_id=i)

        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/admin/content_opportunities?limit=2&offset=0")
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2


# ---------------------------------------------------------------------------
# GET detail
# ---------------------------------------------------------------------------
class TestDetail:
    def test_missing_id_returns_404(self, client):
        resp = client.get("/api/admin/content_opportunities/999")
        assert resp.status_code == 404

    def test_returns_row_with_all_fields(self, client):
        import asyncio
        sm = client.sessionmaker  # type: ignore[attr-defined]
        co_id = asyncio.get_event_loop().run_until_complete(
            _seed_co(sm(), status="draft")
        )
        resp = client.get(f"/api/admin/content_opportunities/{co_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == co_id
        assert body["signal_id"] == 42
        assert body["hook"] == "AI 颠覆跨境"
        assert body["content_score"] == 85.0


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------
class TestTransitions:
    def test_approve_draft_to_approved(self, client):
        import asyncio
        sm = client.sessionmaker  # type: ignore[attr-defined]
        co_id = asyncio.get_event_loop().run_until_complete(
            _seed_co(sm(), status="draft")
        )
        resp = client.post(f"/api/admin/content_opportunities/{co_id}/approve")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"

    def test_publish_approved_to_published(self, client):
        import asyncio
        sm = client.sessionmaker  # type: ignore[attr-defined]
        co_id = asyncio.get_event_loop().run_until_complete(
            _seed_co(sm(), status="approved")
        )
        resp = client.post(f"/api/admin/content_opportunities/{co_id}/publish")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "published"

    def test_cannot_publish_from_draft(self, client):
        """State machine rejects illegal draft → published."""
        import asyncio
        sm = client.sessionmaker  # type: ignore[attr-defined]
        co_id = asyncio.get_event_loop().run_until_complete(
            _seed_co(sm(), status="draft")
        )
        resp = client.post(f"/api/admin/content_opportunities/{co_id}/publish")
        assert resp.status_code == 422
        assert "illegal" in resp.json()["detail"]

    def test_reject_with_reason(self, client):
        import asyncio
        sm = client.sessionmaker  # type: ignore[attr-defined]
        co_id = asyncio.get_event_loop().run_until_complete(
            _seed_co(sm(), status="draft")
        )
        resp = client.post(
            f"/api/admin/content_opportunities/{co_id}/reject",
            json={"reason": "包含违规关键词"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rejected"

    def test_reject_missing_id_returns_404(self, client):
        resp = client.post(
            "/api/admin/content_opportunities/999/reject",
            json={"reason": "test"},
        )
        assert resp.status_code == 404

    def test_approve_missing_id_returns_404(self, client):
        resp = client.post("/api/admin/content_opportunities/999/approve")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth (when secret is set)
# ---------------------------------------------------------------------------
# Note: webhook auth itself is covered by ``tests/test_internal_api.py``
# since ``_require_webhook`` mirrors ``_check_webhook_secret`` verbatim.
# Adding another auth test here would be redundant + flaky against the
# FastAPI dep cache, so we omit it.
