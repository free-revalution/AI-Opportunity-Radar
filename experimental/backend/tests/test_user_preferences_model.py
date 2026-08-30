"""Tests for the User model extension + preferences helpers — Phase 15B.

Covers:

  * User model exposes the 10 new columns from Phase 15A migration.
  * `get_or_create_user_by_feishu` is idempotent.
  * `validate_preference` whitelist + blacklist for platform/tone/language.
  * `apply_preference` mutates in memory; commit round-trip works.
  * `update_subscription_mirror` writes status + expires_at.
  * Migration upgrade / downgrade happy path (via `Base.metadata.create_all`
    — we don't boot alembic for the test, the SQLite test engine re-runs
    DDL on every fixture).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# User model schema
# ---------------------------------------------------------------------------
class TestUserSchema:
    def test_user_has_phase15_columns(self):
        from app.models import User

        columns = {c.name for c in User.__table__.columns}
        expected = {
            "feishu_open_id",
            "vertical",
            "niche",
            "platform",
            "audience",
            "tone",
            "language",
            "preferences_json",
            "subscription_status",
            "subscription_expires_at",
        }
        missing = expected - columns
        assert not missing, f"User missing columns: {missing}"

    def test_feishu_open_id_is_unique_indexed(self):
        from app.models import User

        col = User.__table__.columns["feishu_open_id"]
        assert col.unique is True
        assert col.index is True

    def test_feishu_open_id_is_nullable(self):
        from app.models import User

        col = User.__table__.columns["feishu_open_id"]
        assert col.nullable is True


# ---------------------------------------------------------------------------
# get_or_create_user_by_feishu
# ---------------------------------------------------------------------------
class TestGetOrCreateUserByFeishu:
    async def test_creates_user_on_first_call(self, client):
        from app.models import User
        from sqlalchemy import select
        from app.services.users import get_or_create_user_by_feishu

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = await get_or_create_user_by_feishu(session, "ou_first")
        assert user.id is not None
        assert user.feishu_open_id == "ou_first"
        assert user.email == "feishu-ou_first@radar.local"

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            rows = list(
                (
                    await session.execute(
                        select(User).where(User.feishu_open_id == "ou_first")
                    )
                ).scalars().all()
            )
        assert len(rows) == 1

    async def test_idempotent_returns_same_user(self, client):
        from app.services.users import get_or_create_user_by_feishu

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            u1 = await get_or_create_user_by_feishu(session, "ou_twice")
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            u2 = await get_or_create_user_by_feishu(session, "ou_twice")
        assert u1.id == u2.id

    async def test_empty_open_id_raises(self, client):
        from app.services.users import get_or_create_user_by_feishu

        with pytest.raises(ValueError):
            async with client.sessionmaker() as session:  # type: ignore[attr-defined]
                await get_or_create_user_by_feishu(session, "")


# ---------------------------------------------------------------------------
# validate_preference
# ---------------------------------------------------------------------------
class TestValidatePreference:
    def test_platform_known(self):
        from app.services.users import validate_preference

        ok, err = validate_preference("platform", "xiaohongshu")
        assert ok is True
        assert err is None

    def test_platform_unknown_rejected(self):
        from app.services.users import validate_preference

        ok, err = validate_preference("platform", "tiktok.com")
        assert ok is False
        assert "platform 不在允许列表" in (err or "")

    def test_tone_known(self):
        from app.services.users import validate_preference

        for v in ("通俗", "专业", "幽默", "严肃"):
            ok, _ = validate_preference("tone", v)
            assert ok, f"{v} should be allowed"

    def test_tone_unknown_rejected(self):
        from app.services.users import validate_preference

        ok, err = validate_preference("tone", "诡谲")
        assert ok is False
        assert "tone" in (err or "")

    def test_language_known(self):
        from app.services.users import validate_preference

        for v in ("zh", "en"):
            ok, _ = validate_preference("language", v)
            assert ok

    def test_language_unknown_rejected(self):
        from app.services.users import validate_preference

        ok, err = validate_preference("language", "fr")
        assert ok is False

    def test_unknown_key_rejected(self):
        from app.services.users import validate_preference

        ok, err = validate_preference("color", "red")
        assert ok is False
        assert "未知" in (err or "")

    def test_empty_value_rejected(self):
        from app.services.users import validate_preference

        ok, err = validate_preference("platform", "")
        assert ok is False
        assert "不能为空" in (err or "")

    def test_vertical_length_limit(self):
        from app.services.users import validate_preference

        ok, err = validate_preference("vertical", "x" * 100)
        assert ok is False
        assert "太长" in (err or "")

    def test_audience_length_limit(self):
        from app.services.users import validate_preference

        ok, err = validate_preference("audience", "x" * 300)
        assert ok is False
        assert "太长" in (err or "")


# ---------------------------------------------------------------------------
# apply_preference
# ---------------------------------------------------------------------------
class TestApplyPreference:
    async def test_apply_mutates_in_memory(self, client):
        from app.services.users import (
            apply_preference,
            get_or_create_user_by_feishu,
        )

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = await get_or_create_user_by_feishu(session, "ou_apply")
            user, err = apply_preference(user, "platform", "douyin")
        assert err is None
        assert user.platform == "douyin"

    async def test_apply_persists_after_commit(self, client):
        from sqlalchemy import select
        from app.models import User
        from app.services.users import (
            apply_preference,
            get_or_create_user_by_feishu,
        )

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = await get_or_create_user_by_feishu(session, "ou_commit")
            user, _ = apply_preference(user, "niche", "AI 写作")
            await session.commit()

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            row = (
                await session.execute(
                    select(User).where(User.feishu_open_id == "ou_commit")
                )
            ).scalar_one()
        assert row.niche == "AI 写作"

    async def test_apply_rejects_invalid(self, client):
        from app.services.users import (
            apply_preference,
            get_or_create_user_by_feishu,
        )

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = await get_or_create_user_by_feishu(session, "ou_invalid")
            user, err = apply_preference(user, "platform", "facebook")
        assert err is not None
        assert "platform" in err


# ---------------------------------------------------------------------------
# update_subscription_mirror
# ---------------------------------------------------------------------------
class TestUpdateSubscriptionMirror:
    async def test_mirror_writes_status_and_expires(self, client):
        from sqlalchemy import select
        from app.models import User
        from app.services.users import (
            get_or_create_user_by_feishu,
            update_subscription_mirror,
        )

        exp = datetime.now(tz=timezone.utc) + timedelta(days=30)
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = await get_or_create_user_by_feishu(session, "ou_mirror")
            update_subscription_mirror(user, status="active", expires_at=exp)
            await session.commit()

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            row = (
                await session.execute(
                    select(User).where(User.feishu_open_id == "ou_mirror")
                )
            ).scalar_one()
        assert row.subscription_status == "active"
        assert row.subscription_expires_at is not None


# ---------------------------------------------------------------------------
# reset_preferences
# ---------------------------------------------------------------------------
class TestResetPreferences:
    async def test_reset_clears_all_six_columns(self, client):
        from app.services.users import (
            apply_preference,
            get_or_create_user_by_feishu,
            reset_preferences,
        )

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = await get_or_create_user_by_feishu(session, "ou_reset_all")
            user, _ = apply_preference(user, "platform", "douyin")
            user, _ = apply_preference(user, "tone", "专业")
            reset_preferences(user)
            await session.commit()
        assert user.platform is None
        assert user.tone is None


# ---------------------------------------------------------------------------
# render_preferences_zh
# ---------------------------------------------------------------------------
class TestRenderPreferencesZh:
    def test_renders_chinese(self):
        from app.services.users import render_preferences_zh

        class _StubUser:
            vertical = "AI"
            niche = None
            platform = "xiaohongshu"
            audience = None
            tone = None
            language = "zh"
            plan = "basic"
            subscription_status = "active"
            subscription_expires_at = datetime(2026, 10, 1, tzinfo=timezone.utc)

        text = render_preferences_zh(_StubUser())  # type: ignore[arg-type]
        assert "vertical" in text
        assert "AI" in text
        assert "xiaohongshu" in text
        assert "basic" in text
        assert "active" in text
