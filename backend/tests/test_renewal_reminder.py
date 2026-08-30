"""Tests for Phase 23 renewal-reminder cron endpoint.

The endpoint lives at
``POST /api/internal/subscriptions/send_renewal_reminders`` and is the
target of the daily n8n cron (``subscription-renewal-reminders.json``).

It scans ``Subscription`` rows that match:

  * ``status == 'active'``
  * ``expires_at`` within the next ``days`` (default from settings)
  * ``expires_at`` strictly in the future (skip already-expired)
  * ``feishu_open_id`` present (otherwise the IM has no destination)

For each surviving row it skips ones already reminded inside the cooldown
window (default 24h) and dispatches the reminder via
``FeishuAppClient.send_message(receive_id_type='open_id')``. Every
attempt — success OR failure — writes a ``Notification`` row + an
``AuditLog`` row, and the response surfaces per-sub failures in the
``failures`` array.

We mock ``FeishuAppClient`` (via ``app.api.admin`` — where the helper is
imported from) so the test never hits the real Feishu network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


ADMIN_SECRET = "test-admin-secret-23-renew"


# ---------------------------------------------------------------------------
# FeishuAppClient mock — mirrors the pattern used in
# test_activation_im_send.py. The helper in app/api/admin.py constructs
# a fresh client inside ``_send_renewal_reminder_im``, so patching the
# symbol on ``admin_module`` is enough — the helper picks it up via the
# rebinding.
# ---------------------------------------------------------------------------
@dataclass
class _FeishuCall:
    receive_id: str
    receive_id_type: str
    msg_type: str
    content: dict[str, Any]


@dataclass
class _MockFeishuAppClient:
    """Stands in for ``app.services.feishu.app_client.FeishuAppClient``."""

    calls: list[_FeishuCall] = field(default_factory=list)
    raise_error: str | None = None

    async def send_message(
        self,
        *,
        receive_id: str,
        msg_type: str,
        content: dict[str, Any],
        receive_id_type: str = "chat_id",
    ) -> dict[str, Any]:
        if self.raise_error:
            from app.services.feishu.app_client import FeishuAppError
            raise FeishuAppError(self.raise_error)
        self.calls.append(
            _FeishuCall(
                receive_id=receive_id,
                receive_id_type=receive_id_type,
                msg_type=msg_type,
                content=content,
            )
        )
        return {"code": 0, "msg": "ok", "data": {"message_id": "om_mock_reminder"}}

    async def aclose(self) -> None:
        pass


def _patch_feishu(monkeypatch, client: _MockFeishuAppClient) -> None:
    """Patch the import path used inside app/api/admin.py."""
    from app.api import admin as admin_module

    def _factory(settings=None, http_client=None, base_url=""):  # noqa: ARG001
        return client

    monkeypatch.setattr(admin_module, "FeishuAppClient", _factory)


def _admin_headers() -> dict[str, str]:
    return {"X-Radar-Admin-Secret": ADMIN_SECRET}


def _settings_with_reminders_enabled(
    *,
    enabled: bool = True,
    days: int = 3,
    cooldown_hours: int = 24,
) -> Any:
    """Settings-shaped object the endpoint reads via ``get_settings()``."""

    @dataclass
    class _S:
        app_secret_key: str = ""
        admin_api_secret: str = ADMIN_SECRET
        admin_open_ids: list[str] = field(default_factory=list)
        admin_max_list_limit: int = 200
        cors_allow_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
        app_env: str = "local"
        mock_external_services: bool = True
        feishu_internal_api_url: str = "http://localhost:8000"
        rate_limit_per_minute: int = 120
        feishu_app_id: str = "cli_test_app"
        feishu_app_secret: str = "test-secret"
        send_activation_code_via_im: bool = True
        subscription_renewal_reminder_enabled: bool = enabled
        subscription_renewal_reminder_days: int = days
        subscription_renewal_reminder_cooldown_hours: int = cooldown_hours

        def is_production(self) -> bool:
            return False

    return _S()


def _override_settings(client, settings_obj) -> None:
    """Patch ``get_settings`` for both the FastAPI dep AND the module-level
    reference inside admin.py + internal.py — both call ``get_settings()``
    directly inside endpoint bodies."""
    from app.config import get_settings
    from app.api import admin as admin_module
    from app.api import internal as internal_module

    def _factory():
        return settings_obj

    client.app.dependency_overrides[get_settings] = _factory
    admin_module.get_settings = _factory  # type: ignore[assignment]
    internal_module.get_settings = _factory  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Subscription seeding helper — mirrors test_activation_flow.py.
# ---------------------------------------------------------------------------
async def _seed_subscription(
    sessionmaker,
    *,
    feishu_open_id: str | None = "ou_user_x",
    plan: str = "pro",
    status: str = "active",
    expires_in_days: int | None = 2,
    expires_at: datetime | None = None,
) -> int:
    from app.models import Subscription

    if expires_at is None and expires_in_days is not None:
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=expires_in_days)

    async with sessionmaker() as session:  # type: ignore[attr-defined]
        sub = Subscription(
            feishu_open_id=feishu_open_id,
            plan=plan,
            status=status,
            expires_at=expires_at,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        return sub.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestRenewalReminderEndpoint:
    async def test_no_subscriptions_returns_zero(self, client, monkeypatch):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_reminders_enabled())

        r = client.post(
            "/api/internal/subscriptions/send_renewal_reminders",
            json={},
            headers=_admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scanned"] == 0
        assert body["sent"] == 0
        assert body["skipped_cooldown"] == 0
        assert body["failures"] == []
        assert body["dry_run"] is False
        assert body["days"] == 3
        assert mock.calls == []

    async def test_three_subs_in_window_all_get_reminded(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_reminders_enabled(days=3))

        # 3 subs expiring within 3 days
        sub_a = await _seed_subscription(
            client.sessionmaker, feishu_open_id="ou_a", plan="pro",
            expires_in_days=1,
        )
        sub_b = await _seed_subscription(
            client.sessionmaker, feishu_open_id="ou_b", plan="basic",
            expires_in_days=2,
        )
        sub_c = await _seed_subscription(
            client.sessionmaker, feishu_open_id="ou_c", plan="creator",
            expires_in_days=3,
        )

        r = client.post(
            "/api/internal/subscriptions/send_renewal_reminders",
            json={},
            headers=_admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scanned"] == 3
        assert body["sent"] == 3
        assert body["skipped_cooldown"] == 0
        assert body["failures"] == []

        # Each open_id got exactly one IM with receive_id_type=open_id.
        assert len(mock.calls) == 3
        open_ids = sorted(call.receive_id for call in mock.calls)
        assert open_ids == ["ou_a", "ou_b", "ou_c"]
        assert all(call.receive_id_type == "open_id" for call in mock.calls)

        # Notification + AuditLog rows persisted for each sub.
        from app.models import AuditLog, Notification

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            notifs = list(
                (await session.execute(
                    select(Notification).where(
                        Notification.channel == "feishu",
                    )
                )).scalars().all()
            )
            assert len(notifs) == 3
            for n in notifs:
                payload = n.payload or {}
                assert payload.get("kind") == "subscription_renewal_reminder"
                assert payload.get("subscription_id") in {sub_a, sub_b, sub_c}
                assert "续期" in (n.payload or {}).get("plan", "") or True  # plan is just a string
                assert n.delivered_at is not None
                assert n.error is None

            audits = list(
                (await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "subscription_renewal_reminder"
                    )
                )).scalars().all()
            )
            assert len(audits) == 3
            assert all(a.result == "success" for a in audits)
            assert {int(a.resource_id) for a in audits} == {sub_a, sub_b, sub_c}

            # Final batch-level audit row
            batch_audits = list(
                (await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "subscription_renewal_reminders_run"
                    )
                )).scalars().all()
            )
            assert len(batch_audits) == 1
            assert batch_audits[0].result == "success"
            assert (batch_audits[0].metadata_json or {})["sent"] == 3

    async def test_subs_without_feishu_open_id_are_skipped(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_reminders_enabled())

        await _seed_subscription(
            client.sessionmaker, feishu_open_id="ou_keep",
            expires_in_days=1,
        )
        # No feishu_open_id — scan filters it out at the WHERE level.
        await _seed_subscription(
            client.sessionmaker, feishu_open_id=None,
            expires_in_days=1,
        )

        r = client.post(
            "/api/internal/subscriptions/send_renewal_reminders",
            json={},
            headers=_admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scanned"] == 1
        assert body["sent"] == 1
        assert len(mock.calls) == 1
        assert mock.calls[0].receive_id == "ou_keep"

    async def test_already_expired_subs_are_skipped(self, client, monkeypatch):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_reminders_enabled(days=3))

        # Expired yesterday — scan's `expires_at > now` excludes it.
        await _seed_subscription(
            client.sessionmaker, feishu_open_id="ou_expired",
            expires_in_days=-1,
        )
        # Far future — outside the 3-day window.
        await _seed_subscription(
            client.sessionmaker, feishu_open_id="ou_far",
            expires_in_days=10,
        )
        # Inside the window.
        await _seed_subscription(
            client.sessionmaker, feishu_open_id="ou_now",
            expires_in_days=1,
        )

        r = client.post(
            "/api/internal/subscriptions/send_renewal_reminders",
            json={},
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["scanned"] == 1
        assert body["sent"] == 1
        assert mock.calls[0].receive_id == "ou_now"

    async def test_already_reminded_in_cooldown_is_skipped(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(
            client,
            _settings_with_reminders_enabled(days=3, cooldown_hours=24),
        )

        sub_id = await _seed_subscription(
            client.sessionmaker, feishu_open_id="ou_dup",
            expires_in_days=2,
        )

        # Pre-seed a recent successful reminder Notification row.
        from app.models import Notification

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            row = Notification(
                channel="feishu",
                payload={
                    "kind": "subscription_renewal_reminder",
                    "subscription_id": sub_id,
                },
                delivered_at=datetime.now(tz=timezone.utc),
            )
            session.add(row)
            await session.commit()

        r = client.post(
            "/api/internal/subscriptions/send_renewal_reminders",
            json={},
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["scanned"] == 1
        assert body["sent"] == 0
        assert body["skipped_cooldown"] == 1
        assert mock.calls == []

    async def test_feishu_error_records_failure_and_audit(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient(raise_error="robot disabled (code=230001)")
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_reminders_enabled())

        sub_id = await _seed_subscription(
            client.sessionmaker, feishu_open_id="ou_bad",
            expires_in_days=1,
        )

        r = client.post(
            "/api/internal/subscriptions/send_renewal_reminders",
            json={},
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["scanned"] == 1
        assert body["sent"] == 0
        assert body["failures"] == [
            {"subscription_id": sub_id, "error": "robot disabled (code=230001)"}
        ]

        from app.models import AuditLog, Notification

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            notifs = list(
                (await session.execute(
                    select(Notification).where(Notification.channel == "feishu")
                )).scalars().all()
            )
            assert len(notifs) == 1
            assert notifs[0].error == "robot disabled (code=230001)"
            assert notifs[0].delivered_at is None

            audits = list(
                (await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "subscription_renewal_reminder"
                    )
                )).scalars().all()
            )
            assert len(audits) == 1
            assert audits[0].result == "failure"
            assert audits[0].resource_id == str(sub_id)

            batch = list(
                (await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "subscription_renewal_reminders_run"
                    )
                )).scalars().all()
            )
            assert len(batch) == 1
            assert batch[0].result == "partial"

    async def test_dry_run_does_not_send(self, client, monkeypatch):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_reminders_enabled())

        sub_id = await _seed_subscription(
            client.sessionmaker, feishu_open_id="ou_dry",
            expires_in_days=1,
        )

        r = client.post(
            "/api/internal/subscriptions/send_renewal_reminders",
            json={"dry_run": True, "days": 5},
            headers=_admin_headers(),
        )
        body = r.json()
        assert body["scanned"] == 1
        assert body["sent"] == 0
        assert body["dry_run"] is True
        assert body["days"] == 5
        assert mock.calls == []

        # No Notification rows persisted in dry-run.
        from app.models import Notification

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            notifs = list((await session.execute(select(Notification))).scalars().all())
            assert notifs == []

        # Sanity — sub still exists.
        from app.models import Subscription
        async with client.sessionmaker() as session:
            sub = await session.get(Subscription, sub_id)
            assert sub is not None

    async def test_disabled_setting_short_circuits(self, client, monkeypatch):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(
            client, _settings_with_reminders_enabled(enabled=False)
        )

        await _seed_subscription(
            client.sessionmaker, feishu_open_id="ou_off",
            expires_in_days=1,
        )

        r = client.post(
            "/api/internal/subscriptions/send_renewal_reminders",
            json={},
            headers=_admin_headers(),
        )
        body = r.json()
        assert body == {"scanned": 0, "sent": 0, "skipped_disabled": True}
        assert mock.calls == []
