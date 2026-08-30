"""Tests for Phase 16E — `/content <opportunity_id>` handler.

`/content` is quota-gated by ``content_full`` (1 piece per call,
legacy INCR path), and it:
  1. Validates the args is a numeric opportunity ID.
  2. GETs `/api/opportunities/{id}` to fetch the signal detail.
  3. Builds a `VerticalContext` from the sender's User preferences
     (Phase 15A columns).
  4. Hands the detail to `ContentRadarAgent.analyze()` (heuristic by
     default; LLMContentRadarAgent provider=None falls back).
  5. Renders the ContentOpportunity payload as Feishu lark_md.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.services.feishu.inbound import (
    BotCommand,
    FeishuCommandRouter,
)
from app.services.subscriptions.paywall import PaywallVerdict


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _stub_paywall(monkeypatch):
    """`/content` is gated by `content_full` quota. Tests bypass the
    DB-bound paywall check by stubbing `_paywall_check` to return a
    creator-tier verdict (high cap)."""
    from app.services.feishu import inbound as inbound_module

    async def _creator_verdict(*, command, redis_client, sender_open_id):
        return PaywallVerdict(
            allowed=True,
            plan="creator",
            quota_type="content_full",
            quota_limit=10**9,
            quota_used=0,
        )

    monkeypatch.setattr(
        inbound_module, "_paywall_check", _creator_verdict
    )


def _make_router(handler: Any) -> FeishuCommandRouter:
    settings = get_settings()
    settings.app_base_url = "http://radar.test"
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://radar.test"
    )
    return FeishuCommandRouter(settings=settings, http_client=client)


def _completed_signal(opportunity_id: int = 42) -> dict[str, Any]:
    """Shape matches GET /api/opportunities/{id} (OpportunityResponse)."""
    return {
        "id": opportunity_id,
        "slug": f"ai-signal-{opportunity_id}",
        "title": "AI 法律合同审核",
        "summary": "大模型解析 100 页 PDF 合同。",
        "category": "AI SaaS",
        "market": "legal",
        "target_user": "lawyers",
        "total_score": 88.5,
        "score": 88.5,
        "recommendation": "strongly_recommend",
    }


# ---------------------------------------------------------------------------
# /content — input validation
# ---------------------------------------------------------------------------
class TestContentInput:
    async def test_missing_args_returns_usage_hint(self):
        router = _make_router(
            lambda req: httpx.Response(200, json={}, request=req)
        )
        reply = await router.route(BotCommand(kind="content", args=""))
        assert "用法" in reply.text
        assert "/content" in reply.text
        assert reply.metadata["error"] == "bad_args"

    async def test_non_numeric_args_returns_usage_hint(self):
        router = _make_router(
            lambda req: httpx.Response(200, json={}, request=req)
        )
        reply = await router.route(BotCommand(kind="content", args="abc"))
        assert "用法" in reply.text
        assert reply.metadata["error"] == "bad_args"

    async def test_negative_id_returns_usage_hint(self):
        router = _make_router(
            lambda req: httpx.Response(200, json={}, request=req)
        )
        reply = await router.route(BotCommand(kind="content", args="-1"))
        # `isdigit()` rejects "-1" → usage.
        assert reply.metadata["error"] == "bad_args"


# ---------------------------------------------------------------------------
# /content — not-found
# ---------------------------------------------------------------------------
class TestContentNotFound:
    async def test_signal_404_returns_not_found_message(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"detail": "not found"},
                request=request,
            )

        router = _make_router(handler)
        reply = await router.route(BotCommand(kind="content", args="999"))
        assert "找不到" in reply.text
        assert "999" in reply.text
        assert reply.metadata["error"] == "not_found"
        assert reply.metadata["signal_id"] == 999


# ---------------------------------------------------------------------------
# /content — happy path
# ---------------------------------------------------------------------------
class TestContentHappyPath:
    async def test_calls_opportunity_detail_endpoint(self):
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url.path)
            if "/api/opportunities/42" in request.url.path:
                return httpx.Response(
                    200, json=_completed_signal(42), request=request
                )
            return httpx.Response(200, json={}, request=request)

        router = _make_router(handler)
        reply = await router.route(BotCommand(kind="content", args="42"))
        assert any("/api/opportunities/42" in p for p in captured)
        assert "🎬" in reply.text or "标题" in reply.text
        assert reply.metadata["signal_id"] == 42

    async def test_renders_full_payload_sections(self):
        """The render helper emits the 6-section ContentOpportunity
        payload — title_candidates / hook / script_outline /
        material_ideas / cta / risk_warning."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_completed_signal(42), request=request
            )

        router = _make_router(handler)
        router._sender_open_id = "ou_content_happy"
        reply = await router.route(BotCommand(kind="content", args="42"))
        # The heuristic agent always populates all six; verify each
        # section label appears in the rendered markdown.
        text = reply.text
        assert "📝 标题候选" in text
        assert "🪝 开场钩子" in text
        assert "🎞️ 脚本大纲" in text
        assert "🧰 素材建议" in text
        assert "📣 CTA" in text
        assert "⚠️ 风险提示" in text

    async def test_metadata_includes_signal_id_and_agent_name(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_completed_signal(7), request=request
            )

        router = _make_router(handler)
        reply = await router.route(BotCommand(kind="content", args="7"))
        assert reply.metadata["signal_id"] == 7
        assert reply.metadata["agent"]  # non-empty
        assert "platform" in reply.metadata
        assert "tone" in reply.metadata

    async def test_default_vertical_context_when_no_sender(self):
        """No `_sender_open_id` → VerticalContext defaults apply."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_completed_signal(1), request=request
            )

        router = _make_router(handler)
        # Note: no router._sender_open_id set.
        reply = await router.route(BotCommand(kind="content", args="1"))
        # Defaults from base.py: platform="general", tone="通俗", language="zh"
        assert reply.metadata["platform"] == "general"
        assert reply.metadata["tone"] == "通俗"
        assert reply.metadata["language"] == "zh"


# ---------------------------------------------------------------------------
# /content — User preferences injection
# ---------------------------------------------------------------------------
class TestContentUserPreferences:
    async def test_user_platform_propagates_to_vertical_context(self, client, monkeypatch):
        """`/preferences set platform=xiaohongshu` → next `/content`
        call has VerticalContext.platform == "xiaohongshu"."""
        import app.db as db_module

        # — Patch ``app.db._sessionmaker`` directly + make ``get_engine``
        # a no-op so the lazy initializer doesn't rebuild the prod
        # engine from settings (which would clobber our patched
        # _sessionmaker). The inbound helper imports
        # ``get_sessionmaker`` lazily inside the function body, so
        # patching the module globals is enough.
        monkeypatch.setattr(
            db_module, "_sessionmaker", client.sessionmaker  # type: ignore[attr-defined]
        )
        monkeypatch.setattr(db_module, "get_engine", lambda: None)

        from sqlalchemy import select
        from app.models import User
        from app.services.users import (
            apply_preference,
            get_or_create_user_by_feishu,
        )

        async with client.sessionmaker() as session:  # type: ignore[attr-defined]
            user = await get_or_create_user_by_feishu(
                session, "ou_pref_user", commit=True
            )
            user, _ = apply_preference(user, "platform", "xiaohongshu")
            user, _ = apply_preference(user, "tone", "专业")
            await session.commit()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_completed_signal(99), request=request
            )

        router = _make_router(handler)
        router._sender_open_id = "ou_pref_user"
        reply = await router.route(BotCommand(kind="content", args="99"))
        assert reply.metadata["platform"] == "xiaohongshu"
        assert reply.metadata["tone"] == "专业"


# ---------------------------------------------------------------------------
# /content — paywall denial
# ---------------------------------------------------------------------------
class TestContentPaywall:
    async def test_content_full_quota_exceeded_returns_deny(self, monkeypatch):
        """`/content` is gated by `content_full`; free user with
        content_pieces=0 gets the paywall-deny branch."""
        from app.services.feishu import inbound as inbound_module

        async def _deny_verdict(*, command, redis_client, sender_open_id):
            return PaywallVerdict(
                allowed=False,
                plan="free",
                quota_type="content_full",
                quota_limit=0,
                quota_used=0,
                deny_reason="plan_no_feature",
                deny_message_zh="🔒 完整内容方案是付费功能。",
            )

        monkeypatch.setattr(inbound_module, "_paywall_check", _deny_verdict)

        # Handler should NOT be called — no mock needed, but supply
        # one that would explode if invoked.
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("handler should not run after paywall deny")

        router = _make_router(handler)
        reply = await router.route(BotCommand(kind="content", args="1"))
        assert "付费" in reply.text or "🔒" in reply.text
        assert reply.metadata.get("denied") is True
