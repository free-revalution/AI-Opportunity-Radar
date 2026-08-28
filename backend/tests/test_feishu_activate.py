"""Tests for the Feishu `/activate` command — Phase 14A.

Covers:

  * Command parsing for `/activate <code>` + `/激活 <code>`.
  * Missing args → usage hint reply.
  * Route dispatch into the activation flow.
  * RBAC: anyone can run (USER role), no admin check needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------
class TestActivateCommandParsing:
    def test_slash_activate_parses(self):
        from app.services.feishu.inbound import parse_command

        cmd = parse_command("/activate ABCD-EFGH-JKLM")
        assert cmd.kind == "activate"
        assert cmd.args == "ABCD-EFGH-JKLM"

    def test_zh_activate_alias_parses(self):
        from app.services.feishu.inbound import parse_command

        cmd = parse_command("/激活 ABCD-EFGH-JKLM")
        assert cmd.kind == "activate"
        assert cmd.args == "ABCD-EFGH-JKLM"

    def test_activate_with_no_args(self):
        from app.services.feishu.inbound import parse_command

        cmd = parse_command("/activate")
        assert cmd.kind == "activate"
        assert cmd.args == ""

    def test_mentions_stripped_before_parsing(self):
        """`_strip_mentions` is upstream; verify parse handles clean text."""
        from app.services.feishu.inbound import parse_command

        cmd = parse_command("/activate ABCD-EFGH-JKLM extra trailing")
        assert cmd.kind == "activate"
        assert cmd.args == "ABCD-EFGH-JKLM extra trailing"


# ---------------------------------------------------------------------------
# Route dispatch
# ---------------------------------------------------------------------------
class TestActivateRouteDispatch:
    """`/activate` route uses the same `client.sessionmaker` the FastAPI
    fixture installs — but `_activate` calls `get_sessionmaker()` directly
    rather than via `Depends(get_session)`, so we have to monkey-patch the
    import inside `app.services.feishu.inbound` to point at the test engine.
    """

    @pytest.fixture
    def patched_sessionmaker(self, client, monkeypatch):
        """`_activate` does `from app.db import get_sessionmaker` at call-time,
        so patching the source attribute is enough — every call re-imports.
        """
        from app import db as db_module

        monkeypatch.setattr(
            db_module,
            "get_sessionmaker",
            lambda: client.sessionmaker,  # type: ignore[attr-defined]
        )
        return client.sessionmaker

    async def _seed_code(self, sessionmaker, *, plan: str = "pro") -> str:
        from app.services.activation import generate_code, hash_code
        from app.models import ActivationCode

        plaintext = generate_code()
        async with sessionmaker() as session:
            row = ActivationCode(
                code_hash=hash_code(plaintext),
                plan=plan,
                expires_at=datetime.now(tz=timezone.utc) + timedelta(days=30),
                status="unused",
            )
            session.add(row)
            await session.commit()
        return plaintext

    async def test_route_dispatches_to_activation(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import FeishuCommandRouter, parse_command

        plaintext = await self._seed_code(patched_sessionmaker)
        router = FeishuCommandRouter()
        router._sender_open_id = "ou_test_user"
        cmd = parse_command(f"/activate {plaintext}")

        reply = await router.route(cmd)

        assert reply.metadata["command"] == "activate"
        assert reply.metadata["success"] is True
        assert reply.metadata["plan"] == "pro"
        assert reply.metadata["code_id"] is not None
        assert "激活成功" in reply.text

    async def test_route_missing_code_returns_usage(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import FeishuCommandRouter, parse_command

        router = FeishuCommandRouter()
        router._sender_open_id = "ou_test_user"
        cmd = parse_command("/activate")

        reply = await router.route(cmd)
        assert reply.metadata["error"] == "missing_code"
        assert "用法" in reply.text

    async def test_route_invalid_code_returns_friendly_error(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import FeishuCommandRouter, parse_command

        router = FeishuCommandRouter()
        router._sender_open_id = "ou_test_user"
        cmd = parse_command("/activate ABCD-EFGH-JKLM")

        reply = await router.route(cmd)
        assert reply.metadata["success"] is False
        assert reply.metadata["status"] == "not_found"
        assert "激活码无效" in reply.text

    async def test_route_no_sender_open_id_returns_invalid_format(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import FeishuCommandRouter, parse_command

        router = FeishuCommandRouter()
        # No _sender_open_id set
        cmd = parse_command("/activate ABCD-EFGH-JKLM")

        reply = await router.route(cmd)
        assert reply.metadata["status"] == "invalid_format"


# ---------------------------------------------------------------------------
# RBAC: anyone can run /activate (USER role)
# ---------------------------------------------------------------------------
class TestActivateRbac:
    def test_role_required_for_activate_is_user(self):
        from app.services.feishu.rbac import CommandRole, role_required_for

        assert role_required_for("activate") is CommandRole.USER

    def test_is_activation_command(self):
        from app.services.feishu.rbac import is_activation_command

        assert is_activation_command("activate")
        assert not is_activation_command("today")
        assert not is_activation_command("admin_refresh")

    def test_authorize_unknown_user_allowed_for_activate(self):
        from app.services.feishu.rbac import authorize

        v = authorize("activate", "ou_random_user", admin_open_ids=["ou_admin"])
        assert v.allowed

    def test_authorize_anonymous_allowed_for_activate(self):
        from app.services.feishu.rbac import authorize

        v = authorize("activate", None, admin_open_ids=["ou_admin"])
        assert v.allowed


# ---------------------------------------------------------------------------
# Audit log persistence
# ---------------------------------------------------------------------------
class TestActivateAudit:
    async def test_successful_activation_writes_audit(self, client, monkeypatch):
        from app.services.feishu.inbound import FeishuCommandRouter, parse_command
        from app.models import AuditLog
        from app.services.activation import generate_code, hash_code
        from app.models import ActivationCode

        # Patch the source so the lazy `from app.db import get_sessionmaker`
        # inside `_activate` re-binds to the test engine.
        from app import db as db_module

        monkeypatch.setattr(
            db_module,
            "get_sessionmaker",
            lambda: client.sessionmaker,  # type: ignore[attr-defined]
        )

        plaintext = generate_code()
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            row = ActivationCode(
                code_hash=hash_code(plaintext),
                plan="basic",
                expires_at=datetime.now(tz=timezone.utc) + timedelta(days=30),
                status="unused",
            )
            session.add(row)
            await session.commit()

        router = FeishuCommandRouter()
        router._sender_open_id = "ou_audit_test"
        cmd = parse_command(f"/activate {plaintext}")
        await router.route(cmd)

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            audits = list(
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "activate")
                    )
                ).scalars().all()
            )
        assert len(audits) == 1
        assert audits[0].actor_type == "user"
        assert audits[0].actor_id == "ou_audit_test"
        assert audits[0].result == "success"