"""Phase 24 — admin compliance API tests.

Covers ``GET /api/admin/compliance`` + ``POST /api/admin/compliance/{id}/override``.

* list default
* risk_level filter
* risk_type filter
* since filter
* pagination
* override happy
* override requires reason
* override on non-compliance_block row rejected
"""

from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_audit_log(
    session: Any,
    *,
    action: str = "compliance_block",
    risk_level: str = "medium",
    risk_types: list[str] | None = None,
    resource_type: str = "feishu_message",
    resource_id: str = "ou_001",
    context: str = "smoke",
    since_offset_minutes: int = 0,
) -> Any:
    from datetime import datetime, timedelta, timezone

    from app.models import AuditLog

    row = AuditLog(
        actor_type="compliance_gate",
        actor_id=f"compliance_gate:{context}",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result="failure",
        metadata_json={
            "risk_level": risk_level,
            "risk_types": risk_types or ["pii"],
            "risk_score": 0.42,
            "reason": f"{risk_level} risk sample",
            "requires_human_review": risk_level in {"medium", "high"},
            "context": context,
        },
        created_at=datetime.now(timezone.utc) - timedelta(minutes=since_offset_minutes),
    )
    session.add(row)
    await session.flush()
    return row


def _compliance_path(**params: Any) -> str:
    from urllib.parse import urlencode

    base = "/api/admin/compliance"
    if not params:
        return base
    return f"{base}?{urlencode({k: v for k, v in params.items() if v is not None})}"


def _admin_headers() -> dict[str, str]:
    """Phase 21 unified require_admin accepts X-Radar-Admin-Secret or
    X-Radar-Webhook. Tests use the webhook secret (set in conftest).
    """
    import os

    secret = os.environ.get("RADAR_WEBHOOK_SECRET", "test-webhook-secret")
    return {"X-Radar-Webhook": secret}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestComplianceList:
    async def test_empty_list(self, client: Any) -> None:
        resp = client.get("/api/admin/compliance", headers=_admin_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    async def test_lists_compliance_block_rows(
        self, client: Any, sqlite_session: Any
    ) -> None:
        await _seed_audit_log(sqlite_session, risk_level="medium", risk_types=["pii"])
        await _seed_audit_log(
            sqlite_session, risk_level="blocked", risk_types=["prompt_injection"]
        )
        await sqlite_session.commit()

        resp = client.get("/api/admin/compliance", headers=_admin_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        levels = {item["risk_level"] for item in body["items"]}
        assert levels == {"medium", "blocked"}

    async def test_filter_by_risk_level(
        self, client: Any, sqlite_session: Any
    ) -> None:
        await _seed_audit_log(sqlite_session, risk_level="medium")
        await _seed_audit_log(sqlite_session, risk_level="blocked")
        await sqlite_session.commit()

        resp = client.get(
            _compliance_path(risk_level="blocked"), headers=_admin_headers()
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["risk_level"] == "blocked"

    async def test_filter_by_risk_type(
        self, client: Any, sqlite_session: Any
    ) -> None:
        await _seed_audit_log(sqlite_session, risk_level="medium", risk_types=["pii"])
        await _seed_audit_log(
            sqlite_session,
            risk_level="blocked",
            risk_types=["prompt_injection"],
        )
        await sqlite_session.commit()

        resp = client.get(
            _compliance_path(risk_type="prompt_injection"),
            headers=_admin_headers(),
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["risk_types"] == ["prompt_injection"]

    async def test_filter_by_since(
        self, client: Any, sqlite_session: Any
    ) -> None:
        await _seed_audit_log(sqlite_session, since_offset_minutes=120)
        await _seed_audit_log(sqlite_session, since_offset_minutes=1)
        await sqlite_session.commit()

        from datetime import datetime, timedelta, timezone

        since = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        resp = client.get(
            _compliance_path(since=since), headers=_admin_headers()
        )
        body = resp.json()
        # Only the row created 1 minute ago falls in the window.
        assert body["total"] == 1

    async def test_pagination(self, client: Any, sqlite_session: Any) -> None:
        for i in range(5):
            await _seed_audit_log(sqlite_session, resource_id=f"ou_{i:03d}")
        await sqlite_session.commit()

        resp = client.get(
            _compliance_path(limit=2, offset=0), headers=_admin_headers()
        )
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2

        resp2 = client.get(
            _compliance_path(limit=2, offset=2), headers=_admin_headers()
        )
        body2 = resp2.json()
        assert len(body2["items"]) == 2
        # Different ids.
        page1_ids = {item["id"] for item in body["items"]}
        page2_ids = {item["id"] for item in body2["items"]}
        assert page1_ids.isdisjoint(page2_ids)


class TestComplianceOverride:
    async def test_override_happy_path(
        self, client: Any, sqlite_session: Any
    ) -> None:
        from sqlalchemy import select

        from app.models import AuditLog

        source = await _seed_audit_log(sqlite_session)
        await sqlite_session.commit()

        resp = client.post(
            f"/api/admin/compliance/{source.id}/override",
            headers=_admin_headers(),
            json={"reason": "Operator reviewed and approved"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["original_audit_log_id"] == source.id
        assert body["override_audit_log_id"] != source.id

        # Source row updated.
        await sqlite_session.refresh(source)
        meta = source.metadata_json
        assert meta["overridden"] is True
        assert meta["override_reason"] == "Operator reviewed and approved"

        # Override row exists.
        stmt = select(AuditLog).where(AuditLog.action == "compliance_override")
        rows = (await sqlite_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].metadata_json["original_audit_log_id"] == source.id

    async def test_override_requires_minimum_reason_length(
        self, client: Any, sqlite_session: Any
    ) -> None:
        source = await _seed_audit_log(sqlite_session)
        await sqlite_session.commit()

        resp = client.post(
            f"/api/admin/compliance/{source.id}/override",
            headers=_admin_headers(),
            json={"reason": "short"},
        )
        assert resp.status_code == 422

    async def test_override_on_non_compliance_row_rejected(
        self, client: Any, sqlite_session: Any
    ) -> None:
        # Seed a NON-compliance_block row.
        source = await _seed_audit_log(sqlite_session, action="auth_login")
        await sqlite_session.commit()

        resp = client.post(
            f"/api/admin/compliance/{source.id}/override",
            headers=_admin_headers(),
            json={"reason": "Operator reviewed and approved"},
        )
        assert resp.status_code == 422

    async def test_override_unknown_id_404(
        self, client: Any, sqlite_session: Any
    ) -> None:
        await sqlite_session.commit()
        resp = client.post(
            "/api/admin/compliance/999999/override",
            headers=_admin_headers(),
            json={"reason": "Operator reviewed and approved"},
        )
        assert resp.status_code == 404
