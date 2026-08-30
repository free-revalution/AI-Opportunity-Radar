"""Tests for Phase 23 activation-code auto-IM.

The ``POST /api/admin/activation/issue`` endpoint gained two new optional
fields:

  * ``feishu_open_id`` — destination user
  * ``send_im``        — default true; operator toggle

When both are present AND ``settings.send_activation_code_via_im`` is
true, the freshly issued plaintext is IM'd via
``FeishuAppClient.send_message(receive_id_type="open_id")`` + a new
``Notification`` audit row + an ``activation_im_send`` ``AuditLog`` row
are written. The response carries ``im_send`` with the result.

We mock ``FeishuAppClient`` so the test never hits the real Feishu
network — the focus is on row creation + payload shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


ADMIN_SECRET = "test-admin-secret-23"


# ---------------------------------------------------------------------------
# FeishuAppClient mock — captures every send_message call and lets us
# simulate both success and failure.
# ---------------------------------------------------------------------------
@dataclass
class _FeishuCall:
    receive_id: str
    receive_id_type: str
    msg_type: str
    content: dict[str, Any]


@dataclass
class _MockFeishuAppClient:
    """Stands in for ``app.services.feishu.app_client.FeishuAppClient``.

    ``raise_error`` switches it to a FeishuAppError-raising client; default
    succeeds with a fake message_id. Tests inject this via monkeypatch.
    """

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
        return {"code": 0, "msg": "ok", "data": {"message_id": "om_mock_1"}}

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


def _settings_with_im_enabled(enabled: bool = True) -> Any:
    """Build a Settings-shaped object that admin.py reads from get_settings()."""

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
        send_activation_code_via_im: bool = enabled
        subscription_renewal_reminder_enabled: bool = True
        subscription_renewal_reminder_days: int = 3
        subscription_renewal_reminder_cooldown_hours: int = 24

        def is_production(self) -> bool:
            return False

    return _S()


def _override_settings(client, settings_obj) -> None:
    from app.config import get_settings
    from app.api import admin as admin_module

    def _factory():
        return settings_obj

    client.app.dependency_overrides[get_settings] = _factory
    # Also patch the module-level reference inside admin.py so direct
    # `get_settings()` calls inside endpoint bodies (e.g.
    # `_send_activation_code_im`) resolve to our test settings.
    admin_module.get_settings = _factory  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestActivationAutoSend:
    async def test_issue_with_feishu_open_id_sends_im_and_writes_notification(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_im_enabled(enabled=True))

        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "pro", "feishu_open_id": "ou_target_user"},
            headers=_admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["im_send"] is not None
        assert body["im_send"]["sent"] is True
        assert body["im_send"]["message_id"] == "om_mock_1"
        assert body["im_send"]["error"] is None

        # 1 outbound send_message call with receive_id_type=open_id
        assert len(mock.calls) == 1
        call = mock.calls[0]
        assert call.receive_id == "ou_target_user"
        assert call.receive_id_type == "open_id"
        assert call.msg_type == "interactive"
        assert "激活码" in (call.content.get("header") or {}).get("title", {}).get("content", "")

        # Notification + AuditLog rows persisted
        from app.models import AuditLog, Notification

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            notifs = list(
                (await session.execute(
                    select(Notification).where(
                        Notification.channel == "feishu",
                    )
                )).scalars().all()
            )
            assert len(notifs) == 1
            payload = notifs[0].payload or {}
            assert payload.get("kind") == "activation_code_issued"
            assert payload.get("activation_code_id") == body["id"]
            assert payload.get("open_id") == "ou_target_user"
            assert notifs[0].delivered_at is not None
            assert notifs[0].error is None

            audits = list(
                (await session.execute(
                    select(AuditLog).where(AuditLog.action == "activation_im_send")
                )).scalars().all()
            )
            assert len(audits) == 1
            assert audits[0].result == "success"
            assert audits[0].resource_id == str(body["id"])

    async def test_issue_with_send_im_false_skips_outbound(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_im_enabled(enabled=True))

        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "basic", "feishu_open_id": "ou_x", "send_im": False},
            headers=_admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["im_send"] is None
        assert mock.calls == []

        # No Notification row written
        from app.models import Notification

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            notifs = list((await session.execute(select(Notification))).scalars().all())
            assert notifs == []

    async def test_issue_without_feishu_open_id_skips_outbound(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_im_enabled(enabled=True))

        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "basic"},
            headers=_admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["im_send"] is None
        assert mock.calls == []

    async def test_issue_setting_disabled_skips_outbound(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_im_enabled(enabled=False))

        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "basic", "feishu_open_id": "ou_x"},
            headers=_admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["im_send"] is None
        assert mock.calls == []

    async def test_issue_records_failure_when_feishu_rejects(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient(raise_error="robot disabled (code=230001)")
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_im_enabled(enabled=True))

        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "creator", "feishu_open_id": "ou_x"},
            headers=_admin_headers(),
        )
        # Issue itself succeeds — auto-IM is best-effort.
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["im_send"]["sent"] is False
        assert "robot disabled" in body["im_send"]["error"]
        assert body["im_send"]["message_id"] is None

        from app.models import AuditLog, Notification

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            notifs = list((await session.execute(select(Notification))).scalars().all())
            assert len(notifs) == 1
            assert notifs[0].error == "robot disabled (code=230001)"
            assert notifs[0].delivered_at is None

            audits = list(
                (await session.execute(
                    select(AuditLog).where(AuditLog.action == "activation_im_send")
                )).scalars().all()
            )
            assert len(audits) == 1
            assert audits[0].result == "failure"

    async def test_resend_endpoint_sends_to_supplied_open_id(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_im_enabled(enabled=True))

        # First issue without IM
        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "basic"},
            headers=_admin_headers(),
        )
        body = r.json()
        code_id = body["id"]
        assert mock.calls == []

        # Resend to a specific open_id
        r2 = client.post(
            f"/api/admin/activation/{code_id}/resend",
            json={"open_id": "ou_resend_target"},
            headers=_admin_headers(),
        )
        assert r2.status_code == 200, r2.text
        rs = r2.json()
        assert rs["sent"] is True
        assert rs["message_id"] == "om_mock_1"
        assert rs["id"] == code_id

        assert len(mock.calls) == 1
        assert mock.calls[0].receive_id == "ou_resend_target"
        assert mock.calls[0].receive_id_type == "open_id"

        from app.models import AuditLog, Notification

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            notifs = list((await session.execute(select(Notification))).scalars().all())
            assert len(notifs) == 1
            assert (notifs[0].payload or {}).get("kind") == "activation_code_resend"
            assert (notifs[0].payload or {}).get("activation_code_id") == code_id

            audits = list(
                (await session.execute(
                    select(AuditLog).where(AuditLog.action == "activation_im_resend")
                )).scalars().all()
            )
            assert len(audits) == 1
            assert audits[0].result == "success"

    async def test_resend_endpoint_requires_open_id(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_im_enabled(enabled=True))

        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "basic"},
            headers=_admin_headers(),
        )
        code_id = r.json()["id"]

        r2 = client.post(
            f"/api/admin/activation/{code_id}/resend",
            json={},
            headers=_admin_headers(),
        )
        assert r2.status_code == 422
        assert mock.calls == []

    async def test_resend_endpoint_rejects_revoked_code(
        self, client, monkeypatch
    ):
        mock = _MockFeishuAppClient()
        _patch_feishu(monkeypatch, mock)
        _override_settings(client, _settings_with_im_enabled(enabled=True))

        r = client.post(
            "/api/admin/activation/issue",
            json={"plan": "basic"},
            headers=_admin_headers(),
        )
        code_id = r.json()["id"]
        client.post(
            f"/api/admin/activation/{code_id}/revoke",
            headers=_admin_headers(),
        )

        r2 = client.post(
            f"/api/admin/activation/{code_id}/resend",
            json={"open_id": "ou_x"},
            headers=_admin_headers(),
        )
        assert r2.status_code == 409
        assert mock.calls == []