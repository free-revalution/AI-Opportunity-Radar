"""Tests for the Feishu `/preferences` command — Phase 15C v2.0.

Covers:

  * `/preferences` (read mode) → returns current state, auto-upserts User.
  * `/preferences set <key>=<value>` → persists + reads back.
  * `/preferences set platform=bad` → 422 + Chinese deny.
  * `/preferences set unknown_key=value` → 422 + allowed-key hint.
  * `/preferences reset` → clears all 6 columns.
  * `/preferences 乱写` → usage hint.
  * First `/preferences` call auto-creates User row (no /activate needed).
  * Without `_sender_open_id` → "请先 /activate 绑定账号" hint.
  * Subscription mirror: bind via /activate, then /preferences shows plan.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def patched_sessionmaker(client, monkeypatch):
    """`_preferences` does `from app.db import get_sessionmaker` lazily."""
    from app import db as db_module

    monkeypatch.setattr(
        db_module,
        "get_sessionmaker",
        lambda: client.sessionmaker,  # type: ignore[attr-defined]
    )
    return client.sessionmaker


def _make_router(open_id: str | None = "ou_user_a"):
    from app.services.feishu.inbound import FeishuCommandRouter

    r = FeishuCommandRouter()
    if open_id is not None:
        r._sender_open_id = open_id
    return r


# ---------------------------------------------------------------------------
# Read mode (no args)
# ---------------------------------------------------------------------------
class TestReadMode:
    async def test_read_auto_creates_user(self, client, patched_sessionmaker):
        from app.models import User
        from app.services.feishu.inbound import parse_command

        router = _make_router("ou_first_timer")
        reply = await router.route(parse_command("/preferences"))

        assert "vertical" in reply.text
        assert "platform" in reply.text
        assert reply.metadata["command"] == "preferences"
        assert reply.metadata["mode"] == "read"

        # User row was created
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            users = list(
                (
                    await session.execute(
                        __import__("sqlalchemy").select(User).where(
                            User.feishu_open_id == "ou_first_timer"
                        )
                    )
                ).scalars().all()
            )
        assert len(users) == 1

    async def test_read_shows_alias(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import parse_command

        router = _make_router("ou_x")
        reply = await router.route(parse_command("/偏好"))
        assert reply.metadata["command"] == "preferences"


# ---------------------------------------------------------------------------
# Set mode
# ---------------------------------------------------------------------------
class TestSetMode:
    async def test_set_platform_valid_persists(self, client, patched_sessionmaker):
        from app.models import User
        from app.services.feishu.inbound import parse_command

        router = _make_router("ou_set_platform")
        reply = await router.route(
            parse_command("/preferences set platform=xiaohongshu")
        )

        assert reply.metadata.get("error") is None
        assert "✅" in reply.text
        assert reply.metadata["key"] == "platform"
        assert reply.metadata["value"] == "xiaohongshu"

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = (
                await session.execute(
                    __import__("sqlalchemy").select(User).where(
                        User.feishu_open_id == "ou_set_platform"
                    )
                )
            ).scalar_one()
        assert user.platform == "xiaohongshu"

    async def test_set_tone_valid(self, client, patched_sessionmaker):
        from app.models import User
        from app.services.feishu.inbound import parse_command

        router = _make_router("ou_tone")
        reply = await router.route(parse_command("/preferences set tone=幽默"))
        assert reply.metadata.get("error") is None

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = (
                await session.execute(
                    __import__("sqlalchemy").select(User).where(
                        User.feishu_open_id == "ou_tone"
                    )
                )
            ).scalar_one()
        assert user.tone == "幽默"

    async def test_set_platform_invalid_rejected(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import parse_command

        router = _make_router("ou_bad_platform")
        reply = await router.route(
            parse_command("/preferences set platform=tiktok.com")
        )
        assert "❌" in reply.text
        assert "platform 不在允许列表" in reply.text
        assert reply.metadata["error"] == "invalid"

    async def test_set_tone_invalid_rejected(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import parse_command

        router = _make_router("ou_bad_tone")
        reply = await router.route(parse_command("/preferences set tone=诡谲"))
        assert "❌" in reply.text
        assert "tone" in reply.text
        assert reply.metadata["error"] == "invalid"

    async def test_set_unknown_key_rejected(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import parse_command

        router = _make_router("ou_bad_key")
        reply = await router.route(
            parse_command("/preferences set color=red")
        )
        assert "❌" in reply.text
        assert "未知" in reply.text
        assert reply.metadata["error"] == "invalid"

    async def test_set_missing_equals_rejected(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import parse_command

        router = _make_router("ou_bad_eq")
        reply = await router.route(
            parse_command("/preferences set platform")
        )
        assert "用法" in reply.text
        assert reply.metadata["error"] == "missing_equals"

    async def test_set_persists_and_reads_back(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import parse_command

        router = _make_router("ou_roundtrip")
        await router.route(parse_command("/preferences set niche=AI 法律"))
        reply = await router.route(parse_command("/preferences"))
        assert "AI 法律" in reply.text


# ---------------------------------------------------------------------------
# Reset mode
# ---------------------------------------------------------------------------
class TestResetMode:
    async def test_reset_clears_all_columns(self, client, patched_sessionmaker):
        from app.models import User
        from app.services.feishu.inbound import parse_command

        router = _make_router("ou_reset")

        # Seed
        await router.route(parse_command("/preferences set platform=bilibili"))
        await router.route(parse_command("/preferences set tone=专业"))

        # Reset
        reply = await router.route(parse_command("/preferences reset"))
        assert "✅" in reply.text
        assert reply.metadata["mode"] == "reset"

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = (
                await session.execute(
                    __import__("sqlalchemy").select(User).where(
                        User.feishu_open_id == "ou_reset"
                    )
                )
            ).scalar_one()
        assert user.platform is None
        assert user.tone is None


# ---------------------------------------------------------------------------
# No sender
# ---------------------------------------------------------------------------
class TestNoSender:
    async def test_no_open_id_returns_help(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import parse_command

        router = _make_router(open_id=None)
        reply = await router.route(parse_command("/preferences"))
        assert "请先" in reply.text
        assert "/activate" in reply.text
        assert reply.metadata["error"] == "no_sender"


# ---------------------------------------------------------------------------
# Garbage subcommand
# ---------------------------------------------------------------------------
class TestBadSubcommand:
    async def test_unknown_subcommand_returns_usage(self, client, patched_sessionmaker):
        from app.services.feishu.inbound import parse_command

        router = _make_router("ou_garbage")
        reply = await router.route(parse_command("/preferences garbage"))
        assert "用法" in reply.text
        assert reply.metadata["error"] == "bad_subcommand"


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------
class TestCommandParsing:
    def test_preferences_aliases(self):
        from app.services.feishu.inbound import parse_command

        assert parse_command("/preferences").kind == "preferences"
        assert parse_command("/偏好").kind == "preferences"
        assert parse_command("/preferences set platform=xhs").kind == "preferences"
        assert parse_command("/preferences reset").kind == "preferences"


# ---------------------------------------------------------------------------
# Subscription mirror — /activate 后 /preferences 能看到 plan
# ---------------------------------------------------------------------------
class TestSubscriptionMirror:
    async def test_preferences_reflects_active_subscription(
        self, client, patched_sessionmaker
    ):
        """Bind a code via /activate, then /preferences shows plan + expires."""
        from app.services.activation import generate_code, hash_code
        from app.models import ActivationCode, User
        from app.services.feishu.inbound import parse_command

        # Seed an ActivationCode
        plaintext = generate_code()
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            session.add(
                ActivationCode(
                    code_hash=hash_code(plaintext),
                    plan="pro",
                    expires_at=datetime.now(tz=timezone.utc) + timedelta(days=30),
                    status="unused",
                )
            )
            await session.commit()

        router = _make_router("ou_mirror")
        await router.route(parse_command(f"/activate {plaintext}"))
        reply = await router.route(parse_command("/preferences"))

        # Subscription mirror updated
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = (
                await session.execute(
                    __import__("sqlalchemy").select(User).where(
                        User.feishu_open_id == "ou_mirror"
                    )
                )
            ).scalar_one()
        assert user.subscription_status == "active"
        assert user.subscription_expires_at is not None
        assert "pro" in reply.text.lower() or "专业" in reply.text
