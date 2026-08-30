"""Tests for Phase 16C — VerticalContext builder + Feishu open_id helper.

Covers:

  * `build_vertical_context(user)` — pure function mapping User row
    columns onto `VerticalContext` fields, with sensible defaults
    (`platform="general"`, `tone="通俗"`, `language="zh"`).

  * `build_vertical_context_for_open_id(session, open_id)` — async
    helper that lazy-creates the User row and returns a `VerticalContext`.
    The DB write is committed so the row survives the request.

  * The defaults must match `VerticalContext` field defaults — agents
    depending on those defaults should never see surprising None values.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# build_vertical_context  (pure)
# ---------------------------------------------------------------------------
class TestBuildVerticalContext:
    def _user(
        self,
        *,
        id: int = 7,
        feishu_open_id: str | None = "ou_u",
        vertical: str | None = "AI",
        niche: str | None = "writing",
        platform: str | None = "xiaohongshu",
        audience: str | None = "creators",
        tone: str | None = "专业",
        language: str | None = "en",
    ) -> Any:
        # Lightweight duck-typed stand-in so we don't need a real DB.
        from app.models import User

        return User(
            id=id,
            feishu_open_id=feishu_open_id,
            vertical=vertical,
            niche=niche,
            platform=platform,
            audience=audience,
            tone=tone,
            language=language,
        )

    def test_maps_all_six_preference_columns(self):
        from app.services.agents.base import VerticalContext
        from app.services.agents.context import build_vertical_context

        user = self._user()
        ctx = build_vertical_context(user)
        assert isinstance(ctx, VerticalContext)
        assert ctx.user_id == 7
        assert ctx.feishu_open_id == "ou_u"
        assert ctx.platform == "xiaohongshu"
        assert ctx.audience == "creators"
        assert ctx.niche == "writing"
        assert ctx.tone == "专业"
        assert ctx.language == "en"

    def test_sender_open_id_kwargs_overrides_user_value(self):
        from app.services.agents.context import build_vertical_context

        user = self._user(feishu_open_id="ou_old")
        ctx = build_vertical_context(user, sender_open_id="ou_new")
        assert ctx.feishu_open_id == "ou_new"

    def test_none_preferences_use_module_defaults(self):
        """All 6 preference columns None → context falls back to the
        module-level defaults that mirror VerticalContext defaults."""
        from app.services.agents.context import (
            _DEFAULT_LANGUAGE,
            _DEFAULT_NICHE,
            _DEFAULT_PLATFORM,
            _DEFAULT_TONE,
            build_vertical_context,
        )

        user = self._user(
            platform=None, audience=None, niche=None,
            tone=None, language=None, feishu_open_id="ou_blank",
        )
        ctx = build_vertical_context(user)
        assert ctx.platform == _DEFAULT_PLATFORM == "general"
        assert ctx.audience == ""
        assert ctx.niche == _DEFAULT_NICHE == ""
        assert ctx.tone == _DEFAULT_TONE == "通俗"
        assert ctx.language == _DEFAULT_LANGUAGE == "zh"

    def test_user_with_no_feishu_open_id_keeps_none(self):
        """Web-signup users have `feishu_open_id=None` — that's fine,
        VerticalContext.feishu_open_id is Optional[str]."""
        from app.services.agents.context import build_vertical_context

        user = self._user(feishu_open_id=None)
        ctx = build_vertical_context(user)
        assert ctx.feishu_open_id is None

    def test_kwargs_sender_open_id_wins_over_none_user_value(self):
        from app.services.agents.context import build_vertical_context

        user = self._user(feishu_open_id=None)
        ctx = build_vertical_context(user, sender_open_id="ou_kw")
        assert ctx.feishu_open_id == "ou_kw"


# ---------------------------------------------------------------------------
# build_vertical_context_for_open_id  (DB-touching)
# ---------------------------------------------------------------------------
class TestBuildVerticalContextForOpenId:
    async def test_existing_user_returns_context(self, client):
        from sqlalchemy import select
        from app.models import User
        from app.services.users import (
            apply_preference,
            build_vertical_context_for_open_id,
            get_or_create_user_by_feishu,
        )

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = await get_or_create_user_by_feishu(
                session, "ou_vc", commit=True
            )
            user, _ = apply_preference(user, "platform", "douyin")
            user, _ = apply_preference(user, "tone", "幽默")
            await session.commit()

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            ctx = await build_vertical_context_for_open_id(session, "ou_vc")
        assert ctx.platform == "douyin"
        assert ctx.tone == "幽默"
        assert ctx.feishu_open_id == "ou_vc"
        assert ctx.user_id == user.id

    async def test_new_open_id_auto_creates_user_with_defaults(self, client):
        from sqlalchemy import select
        from app.models import User
        from app.services.users import build_vertical_context_for_open_id

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            ctx = await build_vertical_context_for_open_id(
                session, "ou_brand_new"
            )
        assert ctx.platform == "general"
        assert ctx.tone == "通俗"
        assert ctx.language == "zh"
        assert ctx.feishu_open_id == "ou_brand_new"

        # User row was actually committed (helper commits for callers).
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            row = (
                await session.execute(
                    select(User).where(User.feishu_open_id == "ou_brand_new")
                )
            ).scalar_one()
        assert row.id is not None
        assert row.feishu_open_id == "ou_brand_new"

    async def test_idempotent_for_repeat_calls(self, client):
        from app.services.users import build_vertical_context_for_open_id

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            c1 = await build_vertical_context_for_open_id(session, "ou_repeat")
        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            c2 = await build_vertical_context_for_open_id(session, "ou_repeat")
        # Same underlying User row → same id.
        assert c1.user_id == c2.user_id
        assert c1.user_id is not None
