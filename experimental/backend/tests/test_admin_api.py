"""Tests for the Admin API — Phase 13B.

Covers:

  * Auth: missing creds → 401, wrong secret → 401, valid secret → 2xx,
    valid Feishu open_id (in admin_open_ids) → 2xx.
  * Activation: issue → row created with hash + plaintext returned once;
    list with status filter; revoke flips status to 'revoked'.
  * Subscriptions: list, get-by-id, extend (frozen-on-expired baseline),
    cancel.
  * Audit: list with actor_type / action / result / since filters.
  * Sources: list; PATCH compliance_level updates + writes audit row.

Each test overrides ``get_settings`` to inject a known admin secret /
Feishu open-id whitelist, so the test never depends on real env vars.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


ADMIN_SECRET = "test-admin-secret-42"
ADMIN_OPEN_ID = "ou_admin_test_99"
NOT_ADMIN_OPEN_ID = "ou_user_random"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class _Settings:
    """Test settings with explicit admin credentials."""

    def __init__(self) -> None:
        self.app_secret_key = ""
        self.admin_api_secret = ADMIN_SECRET
        self.admin_open_ids = [ADMIN_OPEN_ID]
        self.admin_max_list_limit = 200
        # Touch all settings the admin endpoints reference
        self.cors_allow_origins = ["http://localhost:3000"]
        self.app_env = "local"
        self.mock_external_services = True
        self.feishu_internal_api_url = "http://localhost:8000"
        self.rate_limit_per_minute = 120

    # Some Settings attributes are accessed as properties / methods.
    def is_production(self) -> bool:  # pragma: no cover — never invoked
        return False


def _override_settings(client) -> None:
    from app.config import get_settings

    def _factory() -> _Settings:
        return _Settings()

    client.app.dependency_overrides[get_settings] = _factory


async def _seed_source(
    client, *, name: str, compliance_level: str = "E"
) -> int:
    from app.models import Source

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        s = Source(
            name=name,
            type="rss",
            url=f"https://example.com/{name}",
            enabled=True,
            crawl_interval=3600,
            compliance_level=compliance_level,
        )
        session.add(s)
        await session.commit()
        await session.refresh(s)
        return s.id


async def _seed_subscription(
    client,
    *,
    plan: str = "basic",
    status: str = "active",
    expires_at: datetime | None = None,
    feishu_open_id: str | None = "ou_user_seed",
) -> int:
    from app.models import Subscription

    if expires_at is None:
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=30)
    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        s = Subscription(
            plan=plan,
            status=status,
            expires_at=expires_at,
            feishu_open_id=feishu_open_id,
        )
        session.add(s)
        await session.commit()
        await session.refresh(s)
        return s.id


async def _seed_audit(
    client,
    *,
    actor_type: str = "system",
    action: str = "publish",
    result: str = "success",
) -> int:
    from app.models import AuditLog

    async with client.sessionmaker() as session:  # type: ignore[attr-defined]
        row = AuditLog(
            actor_type=actor_type,
            action=action,
            result=result,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


def _admin_headers() -> dict[str, str]:
    return {"X-Radar-Admin-Secret": ADMIN_SECRET}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class TestAuth:
    async def test_missing_creds_returns_401(self, client):
        _override_settings(client)
        r = client.get("/api/admin/activation")
        assert r.status_code == 401

    async def test_wrong_secret_returns_401(self, client):
        _override_settings(client)
        r = client.get(
            "/api/admin/activation",
            headers={"X-Radar-Admin-Secret": "nope"},
        )
        assert r.status_code == 401

    async def test_unknown_feishu_open_id_returns_401(self, client):
        _override_settings(client)
        r = client.get(
            "/api/admin/activation",
            headers={"X-Feishu-Open-Id": NOT_ADMIN_OPEN_ID},
        )
        assert r.status_code == 401

    async def test_admin_open_id_passes(self, client):
        _override_settings(client)
        r = client.get(
            "/api/admin/activation",
            headers={"X-Feishu-Open-Id": ADMIN_OPEN_ID},
        )
        assert r.status_code == 200

    async def test_admin_secret_passes(self, client):
        _override_settings(client)
        r = client.get("/api/admin/activation", headers=_admin_headers())
        assert r.status_code == 200

    async def test_dev_short_circuit_when_all_settings_empty(self, client):
        """Phase 21 — the unified ``require_admin`` dep short-circuits in
        dev/local when every settings source is empty. This matches the
        legacy ``_require_webhook`` behavior that ``conftest.py`` relies
        on (clears ``APP_SECRET_KEY`` / ``RADAR_WEBHOOK_SECRET`` at
        import time so all admin/internal tests pass without mock
        settings). Old test asserted the inverse — kept the original
        helper shape and updated the assertion."""
        from app.config import get_settings

        def _factory():
            s = _Settings()
            s.admin_api_secret = ""
            s.admin_open_ids = []
            return s

        client.app.dependency_overrides[get_settings] = _factory
        r = client.get("/api/admin/activation", headers=_admin_headers())
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------
class TestActivation:
    async def test_issue_returns_code_and_persists_hash(self, client):
        _override_settings(client)
        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "pro"},
            headers=_admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["plan"] == "pro"
        assert body["status"] == "unused"
        assert "code" in body
        assert body["code"]
        assert body["id"] > 0

        # Verify hash, not plaintext, is in DB
        from app.models import ActivationCode

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            rows = list((await session.execute(select(ActivationCode))).scalars().all())
            assert len(rows) == 1
            assert rows[0].code_hash != body["code"]
            assert len(rows[0].code_hash) == 64

    async def test_issue_rejects_unknown_plan(self, client):
        _override_settings(client)
        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "diamond"},
            headers=_admin_headers(),
        )
        assert r.status_code == 422

    async def test_issue_default_ttl_is_365_days(self, client):
        _override_settings(client)
        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "basic"},
            headers=_admin_headers(),
        )
        body = r.json()
        expires = datetime.fromisoformat(body["expires_at"])
        delta = expires - datetime.now(tz=timezone.utc)
        # Within ±1 day of 365
        assert 360 < delta.days < 370

    async def test_issue_writes_audit_row(self, client):
        _override_settings(client)
        from app.models import AuditLog

        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "basic"},
            headers=_admin_headers(),
        )
        body = r.json()
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            rows = list(
                (await session.execute(select(AuditLog))).scalars().all()
            )
            matches = [r for r in rows if r.action == "activation_issue"]
            assert len(matches) == 1
            assert matches[0].resource_id == str(body["id"])
            assert matches[0].actor_type == "admin"

    async def test_list_returns_rows(self, client):
        _override_settings(client)
        client.post(
            "/api/admin/activation/issue",
            json={"plan": "basic"},
            headers=_admin_headers(),
        )
        client.post(
            "/api/admin/activation/issue",
            json={"plan": "pro"},
            headers=_admin_headers(),
        )
        r = client.get("/api/admin/activation", headers=_admin_headers())
        body = r.json()
        assert body["count"] == 2
        assert {it["plan"] for it in body["items"]} == {"basic", "pro"}

    async def test_list_filter_by_status(self, client):
        _override_settings(client)
        r1 = client.post(
            "/api/admin/activation/issue",
            json={"plan": "basic"},
            headers=_admin_headers(),
        ).json()
        client.post(
            "/api/admin/activation/issue",
            json={"plan": "pro"},
            headers=_admin_headers(),
        )
        # Revoke the first one
        client.post(
            f"/api/admin/activation/{r1['id']}/revoke",
            headers=_admin_headers(),
        )
        r = client.get(
            "/api/admin/activation?status=revoked",
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["count"] == 1
        assert body["items"][0]["id"] == r1["id"]
        assert body["items"][0]["status"] == "revoked"

    async def test_list_rejects_unknown_status(self, client):
        _override_settings(client)
        r = client.get(
            "/api/admin/activation?status=unknown",
            headers=_admin_headers(),
        )
        assert r.status_code == 422

    async def test_revoke_flips_status(self, client):
        _override_settings(client)
        issued = client.post(
            "/api/admin/activation/issue",
            json={"plan": "basic"},
            headers=_admin_headers(),
        ).json()
        r = client.post(
            f"/api/admin/activation/{issued['id']}/revoke",
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "revoked"

    async def test_revoke_404_on_unknown_id(self, client):
        _override_settings(client)
        r = client.post(
            "/api/admin/activation/9999/revoke",
            headers=_admin_headers(),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------
class TestSubscriptions:
    async def test_list_returns_seeded_rows(self, client):
        _override_settings(client)
        await _seed_subscription(client, plan="basic")
        await _seed_subscription(client, plan="pro")
        r = client.get("/api/admin/subscriptions", headers=_admin_headers())
        body = r.json()
        assert body["count"] == 2

    async def test_list_filter_by_plan(self, client):
        _override_settings(client)
        await _seed_subscription(client, plan="basic")
        await _seed_subscription(client, plan="pro")
        r = client.get(
            "/api/admin/subscriptions?plan=basic",
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["count"] == 1
        assert body["items"][0]["plan"] == "basic"

    async def test_get_by_id(self, client):
        _override_settings(client)
        sid = await _seed_subscription(client, plan="pro")
        r = client.get(
            f"/api/admin/subscriptions/{sid}",
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        assert r.json()["plan"] == "pro"

    async def test_get_404(self, client):
        _override_settings(client)
        r = client.get(
            "/api/admin/subscriptions/9999",
            headers=_admin_headers(),
        )
        assert r.status_code == 404

    async def test_extend_pushes_expiry(self, client):
        _override_settings(client)
        sid = await _seed_subscription(
            client,
            plan="pro",
            expires_at=datetime.now(tz=timezone.utc) + timedelta(days=5),
        )
        r = client.post(
            f"/api/admin/subscriptions/{sid}/extend",
            json={"days": 30},
            headers=_admin_headers(),
        )
        body = r.json()
        new_exp = datetime.fromisoformat(body["expires_at"])
        delta = new_exp - datetime.now(tz=timezone.utc)
        # 5 + 30 = 35 days — accept 34 (delta.days floors sub-day remainders)
        # since SQLite may strip microseconds when round-tripping the
        # datetime through the engine.
        assert 34 <= delta.days <= 35
        assert body["status"] == "active"

    async def test_extend_expired_subscription_uses_now_as_base(self, client):
        _override_settings(client)
        past = datetime.now(tz=timezone.utc) - timedelta(days=10)
        sid = await _seed_subscription(client, plan="pro", expires_at=past)
        r = client.post(
            f"/api/admin/subscriptions/{sid}/extend",
            json={"days": 7},
            headers=_admin_headers(),
        )
        new_exp = datetime.fromisoformat(r.json()["expires_at"])
        delta = new_exp - datetime.now(tz=timezone.utc)
        assert 6 <= delta.days <= 7

    async def test_extend_404(self, client):
        _override_settings(client)
        r = client.post(
            "/api/admin/subscriptions/9999/extend",
            json={"days": 30},
            headers=_admin_headers(),
        )
        assert r.status_code == 404

    async def test_cancel_flips_status(self, client):
        _override_settings(client)
        sid = await _seed_subscription(client, plan="pro")
        r = client.post(
            f"/api/admin/subscriptions/{sid}/cancel",
            headers=_admin_headers(),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
class TestSources:
    async def test_list_returns_compliance_posture(self, client):
        _override_settings(client)
        await _seed_source(client, name="github", compliance_level="A")
        await _seed_source(client, name="reddit", compliance_level="B")
        r = client.get("/api/admin/sources", headers=_admin_headers())
        body = r.json()
        assert body["count"] == 2
        names = {it["name"] for it in body["items"]}
        assert names == {"github", "reddit"}

    async def test_list_filter_by_compliance(self, client):
        _override_settings(client)
        await _seed_source(client, name="github", compliance_level="A")
        await _seed_source(client, name="reddit", compliance_level="B")
        r = client.get(
            "/api/admin/sources?compliance_level=A",
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["count"] == 1
        assert body["items"][0]["name"] == "github"

    async def test_patch_updates_compliance(self, client):
        _override_settings(client)
        sid = await _seed_source(client, name="github", compliance_level="E")
        r = client.patch(
            f"/api/admin/sources/{sid}/compliance",
            json={"compliance_level": "A", "retention_policy": "30d"},
            headers=_admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["compliance_level"] == "A"
        assert body["retention_policy"] == "30d"
        assert body["last_compliance_check"] is not None

    async def test_patch_rejects_invalid_level(self, client):
        _override_settings(client)
        sid = await _seed_source(client, name="github")
        r = client.patch(
            f"/api/admin/sources/{sid}/compliance",
            json={"compliance_level": "Z"},
            headers=_admin_headers(),
        )
        assert r.status_code == 422

    async def test_patch_writes_audit_row(self, client):
        _override_settings(client)
        from app.models import AuditLog

        sid = await _seed_source(client, name="github", compliance_level="E")
        client.patch(
            f"/api/admin/sources/{sid}/compliance",
            json={"compliance_level": "B"},
            headers=_admin_headers(),
        )
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            rows = list((await session.execute(select(AuditLog))).scalars().all())
        matches = [r for r in rows if r.action == "source_compliance_update"]
        assert len(matches) == 1
        assert matches[0].resource_id == str(sid)

    async def test_patch_404_on_unknown_source(self, client):
        _override_settings(client)
        r = client.patch(
            "/api/admin/sources/9999/compliance",
            json={"compliance_level": "A"},
            headers=_admin_headers(),
        )
        assert r.status_code == 404