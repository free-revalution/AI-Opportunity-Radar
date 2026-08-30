"""Tests for Phase 20 — ``GET /api/admin/audit_logs``.

Sole-operator audit viewer with webhook auth (Phase 18 AdminGuard works),
richer filter set (actor_id, resource_type, resource_id, until, offset)
and a real ``total`` for pagination.

The Phase 12 ``/api/admin/audit`` endpoint is not exercised here — that
path uses admin-secret auth and is covered by ``test_admin_api.py``.

Webhook auth: conftest clears ``APP_SECRET_KEY`` and
``RADAR_WEBHOOK_SECRET`` so ``_require_webhook`` short-circuits and
accepts all callers. No extra auth test needed here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import AuditLog
from app.repositories import ContentOpportunityRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_audit(
    client,
    *,
    actor_type: str = "webhook",
    actor_id: str | None = "webhook",
    action: str = "publish",
    resource_type: str = "content_opportunity",
    resource_id: str = "1",
    result: str = "success",
    metadata_json: dict | None = None,
    age_hours: int = 0,
) -> int:
    """Insert one AuditLog row + optional ``created_at`` rewind."""
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        row = AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            metadata_json=metadata_json,
        )
        session.add(row)
        await session.flush()
        if age_hours:
            row.created_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)
            await session.flush()
        await session.commit()
        await session.refresh(row)
        return row.id


# ---------------------------------------------------------------------------
# Empty DB
# ---------------------------------------------------------------------------
class TestEmptyDB:
    async def test_returns_empty_items_and_zero_total(self, client):
        resp = client.get("/api/admin/audit_logs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["limit"] == 50  # default
        assert body["offset"] == 0


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
class TestFilters:
    async def test_actor_type_subset(self, client):
        await _seed_audit(client, actor_type="admin", action="activation_issue")
        await _seed_audit(client, actor_type="admin", action="activation_revoke")
        await _seed_audit(client, actor_type="system", action="refresh")
        await _seed_audit(client, actor_type="user", action="activate")

        resp = client.get("/api/admin/audit_logs?actor_type=admin")
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2
        assert {it["actor_type"] for it in body["items"]} == {"admin"}

    async def test_action_subset(self, client):
        for _ in range(3):
            await _seed_audit(client, action="publish")
        await _seed_audit(client, action="reject")

        resp = client.get("/api/admin/audit_logs?action=publish")
        body = resp.json()
        assert body["total"] == 3
        assert all(it["action"] == "publish" for it in body["items"])

    async def test_result_filter_rejects_unknown_value(self, client):
        resp = client.get("/api/admin/audit_logs?result=bogus")
        assert resp.status_code == 422

    async def test_resource_id_exact_match(self, client):
        await _seed_audit(client, resource_id="42", action="approve")
        await _seed_audit(client, resource_id="99", action="approve")
        await _seed_audit(client, resource_id="42", action="reject")

        resp = client.get("/api/admin/audit_logs?resource_id=42")
        body = resp.json()
        assert body["total"] == 2
        assert all(it["resource_id"] == "42" for it in body["items"])

    async def test_time_range_since_and_until(self, client):
        await _seed_audit(client, action="old", age_hours=72)
        await _seed_audit(client, action="recent1", age_hours=2)
        await _seed_audit(client, action="recent2", age_hours=0)

        # URL-encode the `+` in the timezone offset (TestClient doesn't
        # transparently encode query strings the way httpx does).
        import urllib.parse

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        cutoff_q = urllib.parse.quote(cutoff)
        resp = client.get(f"/api/admin/audit_logs?since={cutoff_q}")
        body = resp.json()
        assert body["total"] == 2
        assert {it["action"] for it in body["items"]} == {"recent1", "recent2"}

        resp = client.get(f"/api/admin/audit_logs?until={cutoff_q}")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["action"] == "old"

    async def test_combined_filters_apply_as_and(self, client):
        await _seed_audit(
            client, actor_type="admin", action="publish",
            resource_id="42", result="success",
        )
        await _seed_audit(
            client, actor_type="admin", action="reject",
            resource_id="42", result="success",
        )
        await _seed_audit(
            client, actor_type="admin", action="publish",
            resource_id="99", result="success",
        )
        await _seed_audit(
            client, actor_type="system", action="publish",
            resource_id="42", result="success",
        )

        resp = client.get(
            "/api/admin/audit_logs?actor_type=admin&action=publish&resource_id=42"
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["actor_type"] == "admin"
        assert body["items"][0]["action"] == "publish"
        assert body["items"][0]["resource_id"] == "42"


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
class TestPagination:
    async def test_offset_returns_correct_slice_and_total(self, client):
        for i in range(25):
            # Higher i → newer (smaller age_hours) so DESC sort matches i.
            await _seed_audit(
                client, action="publish", resource_id=str(i),
                age_hours=25 - i,
            )

        # Page 1: limit=10 offset=0
        resp = client.get("/api/admin/audit_logs?limit=10&offset=0")
        body = resp.json()
        assert body["total"] == 25
        assert body["limit"] == 10
        assert body["offset"] == 0
        assert len(body["items"]) == 10
        page1_ids = [it["resource_id"] for it in body["items"]]

        # Page 2: limit=10 offset=10 — disjoint from page 1
        resp = client.get("/api/admin/audit_logs?limit=10&offset=10")
        body = resp.json()
        assert body["total"] == 25
        assert body["limit"] == 10
        assert body["offset"] == 10
        assert len(body["items"]) == 10
        page2_ids = [it["resource_id"] for it in body["items"]]
        assert set(page1_ids).isdisjoint(set(page2_ids))

        # Page 3: limit=10 offset=20 — only 5 rows left
        resp = client.get("/api/admin/audit_logs?limit=10&offset=20")
        body = resp.json()
        assert body["total"] == 25
        assert len(body["items"]) == 5

    async def test_offset_past_end_returns_empty(self, client):
        await _seed_audit(client, action="publish")

        resp = client.get("/api/admin/audit_logs?offset=100")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"] == []


# ---------------------------------------------------------------------------
# Phase 19 from-field fix — _transition_content_opportunity now records
# both `from` and `to` in AuditLog metadata_json.
# ---------------------------------------------------------------------------
class TestTransitionFromField:
    async def _seed_draft_co(self, client) -> int:
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            repo = ContentOpportunityRepository(session)
            row = await repo.create(
                signal_id=42,
                platform="xiaohongshu",
                tone="专业",
                hook="AI 颠覆跨境",
                content_score=85.0,
                status="draft",
            )
            await session.commit()
            await session.refresh(row)
            return row.id

    async def _fetch_transition_rows(self, client) -> list[AuditLog]:
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            return list((
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "content_opportunity_transition"
                    ).order_by(AuditLog.created_at.desc())
                )
            ).scalars().all())

    async def test_approve_records_from_and_to(self, client):
        co_id = await self._seed_draft_co(client)

        resp = client.post(f"/api/admin/content_opportunities/{co_id}/approve")
        assert resp.status_code == 200, resp.text

        rows = await self._fetch_transition_rows(client)
        assert len(rows) == 1
        meta = rows[0].metadata_json or {}
        assert meta.get("from") == "draft", f"expected from=draft, got {meta}"
        assert meta.get("to") == "approved", f"expected to=approved, got {meta}"

    async def test_reject_records_from_to_and_reason(self, client):
        co_id = await self._seed_draft_co(client)

        resp = client.post(
            f"/api/admin/content_opportunities/{co_id}/reject",
            json={"reason": "包含违禁词"},
        )
        assert resp.status_code == 200, resp.text

        rows = await self._fetch_transition_rows(client)
        assert len(rows) == 1
        meta = rows[0].metadata_json or {}
        assert meta.get("from") == "draft"
        assert meta.get("to") == "rejected"
        assert meta.get("reason") == "包含违禁词"
