"""Tests for Phase 23 admin notifications endpoint.

The ``GET /api/admin/notifications`` route powers the
``/admin/messages`` viewer — paginated Notification history filtered by
``kind`` / ``channel`` / ``since``. We mock ``FeishuAppClient`` only
where needed (most of these tests just seed Notification rows directly
through ``client.sessionmaker`` since the endpoint doesn't itself
invoke Feishu).

Each row is augmented server-side with:
  * ``kind`` — pulled out of the JSON ``payload.kind``.
  * ``deep_link`` — admin route back to the underlying resource.
  * ``failed`` — boolean convenience flag.

JSON-path extraction must work cross-dialect:
  * PostgreSQL → ``payload['kind'].astext``
  * SQLite    → ``func.json_extract(payload, '$.kind')``

We test against the SQLite test DB and trust the parallel
``astext`` branch is exercised by the integration suite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


ADMIN_SECRET = "test-admin-secret-23-notif"


def _admin_headers() -> dict[str, str]:
    return {"X-Radar-Admin-Secret": ADMIN_SECRET}


async def _seed_notification(
    sessionmaker,
    *,
    channel: str = "feishu",
    kind: str | None = None,
    payload: dict | None = None,
    delivered_at: datetime | None = None,
    error: str | None = None,
    created_offset_seconds: int = 0,
) -> int:
    from app.models import Notification

    payload = dict(payload or {})
    if kind is not None:
        payload.setdefault("kind", kind)

    created_at = datetime.now(tz=timezone.utc) - timedelta(
        seconds=created_offset_seconds
    )
    async with sessionmaker() as session:  # type: ignore[attr-defined]
        row = Notification(
            channel=channel,
            payload=payload,
            delivered_at=delivered_at or (datetime.now(tz=timezone.utc) if error is None else None),
            error=error,
        )
        # created_at has server_default; we explicitly set so the
        # `since` filter test can rely on a known value.
        row.created_at = created_at
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


# ---------------------------------------------------------------------------
class TestAdminNotificationsList:
    async def test_requires_admin_auth(self, client, monkeypatch):
        # Configure a non-empty admin_api_secret so the dev short-circuit
        # in `require_admin` no longer accepts all callers. Then send a
        # wrong header to trigger 401.
        from dataclasses import dataclass, field

        from app.config import get_settings
        from app.api import admin as admin_module

        @dataclass
        class _S:
            app_secret_key: str = ""
            admin_api_secret: str = "real-admin-secret"
            admin_open_ids: list[str] = field(default_factory=list)
            admin_max_list_limit: int = 200
            cors_allow_origins: list[str] = field(
                default_factory=lambda: ["http://localhost:3000"]
            )

            def is_production(self) -> bool:
                return False

        def _factory():
            return _S()

        client.app.dependency_overrides[get_settings] = _factory
        admin_module.get_settings = _factory  # type: ignore[assignment]

        r = client.get(
            "/api/admin/notifications",
            headers={"X-Radar-Admin-Secret": "nope"},
        )
        assert r.status_code == 401

    async def test_returns_empty_when_no_rows(self, client):
        r = client.get(
            "/api/admin/notifications",
            headers=_admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0

    async def test_returns_recent_first_with_kind_and_deep_link(self, client):
        code_id = 42
        await _seed_notification(
            client.sessionmaker,  # type: ignore[attr-defined]
            kind="activation_code_issued",
            payload={
                "kind": "activation_code_issued",
                "activation_code_id": code_id,
                "open_id": "ou_x",
                "plan": "pro",
            },
        )

        r = client.get(
            "/api/admin/notifications",
            headers=_admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["kind"] == "activation_code_issued"
        assert item["channel"] == "feishu"
        assert item["failed"] is False
        assert item["deep_link"] == f"/admin/activation?id={code_id}"
        assert item["payload"]["plan"] == "pro"
        assert item["error"] is None
        assert item["delivered_at"] is not None

    async def test_kind_filter(self, client):
        await _seed_notification(
            client.sessionmaker,  # type: ignore[attr-defined]
            kind="activation_code_issued",
        )
        await _seed_notification(
            client.sessionmaker,  # type: ignore[attr-defined]
            kind="subscription_renewal_reminder",
            payload={
                "kind": "subscription_renewal_reminder",
                "subscription_id": 7,
            },
        )
        await _seed_notification(
            client.sessionmaker,  # type: ignore[attr-defined]
            kind="activation_code_resend",
            payload={
                "kind": "activation_code_resend",
                "activation_code_id": 99,
            },
        )

        r = client.get(
            "/api/admin/notifications?kind=subscription_renewal_reminder",
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["kind"] == "subscription_renewal_reminder"
        assert body["items"][0]["deep_link"] == "/admin/subscriptions?id=7"

    async def test_channel_filter(self, client):
        await _seed_notification(client.sessionmaker, channel="feishu")  # type: ignore[attr-defined]
        await _seed_notification(
            client.sessionmaker,  # type: ignore[attr-defined]
            channel="telegram",
        )

        r = client.get(
            "/api/admin/notifications?channel=feishu",
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["channel"] == "feishu"

    async def test_since_filter(self, client):
        # Old row — should be excluded.
        await _seed_notification(
            client.sessionmaker,  # type: ignore[attr-defined]
            created_offset_seconds=3600 * 24,  # 1 day ago
        )
        # Recent row — should be included.
        await _seed_notification(
            client.sessionmaker,  # type: ignore[attr-defined]
            created_offset_seconds=10,
        )

        # Window: 1 hour ago → only the recent row qualifies.
        # URL-encode the timezone `+` (httpx/urllib turns it into a space).
        from urllib.parse import quote

        since = quote(
            (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
        )
        r = client.get(
            f"/api/admin/notifications?since={since}",
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["total"] == 1

    async def test_pagination(self, client):
        # 5 rows, limit 2.
        for i in range(5):
            await _seed_notification(
                client.sessionmaker,  # type: ignore[attr-defined]
                created_offset_seconds=i,  # 0, 1, 2, 3, 4
            )

        r = client.get(
            "/api/admin/notifications?limit=2&offset=0",
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["total"] == 5
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert len(body["items"]) == 2

        r2 = client.get(
            "/api/admin/notifications?limit=2&offset=4",
            headers=_admin_headers(),
        )
        body2 = r2.json()
        assert len(body2["items"]) == 1

    async def test_failed_flag_for_error_rows(self, client):
        await _seed_notification(
            client.sessionmaker,  # type: ignore[attr-defined]
            error="robot disabled (code=230001)",
        )
        r = client.get(
            "/api/admin/notifications",
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["items"][0]["failed"] is True
        assert body["items"][0]["delivered_at"] is None
        assert body["items"][0]["error"] == "robot disabled (code=230001)"

    async def test_kind_filter_combined_with_channel(self, client):
        await _seed_notification(
            client.sessionmaker,  # type: ignore[attr-defined]
            channel="feishu",
            kind="activation_code_issued",
        )
        await _seed_notification(
            client.sessionmaker,  # type: ignore[attr-defined]
            channel="telegram",
            kind="activation_code_issued",
        )
        await _seed_notification(
            client.sessionmaker,  # type: ignore[attr-defined]
            channel="feishu",
            kind="subscription_renewal_reminder",
        )

        r = client.get(
            "/api/admin/notifications?kind=activation_code_issued&channel=feishu",
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["channel"] == "feishu"
        assert body["items"][0]["kind"] == "activation_code_issued"
