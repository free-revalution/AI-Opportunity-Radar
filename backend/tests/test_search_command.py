"""Tests for Phase 16E — `/search` real implementation.

`/search <query>` is a quota-gated command that:
  1. Validates the query is non-empty.
  2. Calls `/api/opportunities?q=<encoded>` (Phase 16D SQL-LIKE filter).
  3. Truncates to the residual `view_top_signals` quota.
  4. SADD the actually-shown distinct IDs so a 2nd `/search` (or
     `/today`) does NOT re-bill the same signal.

The paywall check happens in `route()` — `_stub_paywall` (autouse)
short-circuits it for most tests. We poke at
`router._last_verdict` directly to simulate Free user quota=1 / Pro
quota=20 paths.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

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
# Stub: paywall short-circuit (same shape as Phase 7 tests)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _stub_paywall(monkeypatch):
    """Phase 16E — `route()` opens a DB session via `get_sessionmaker()`
    inside `_paywall_check`. The unit tests don't exercise that path
    — they bypass it by returning a controllable PaywallVerdict. Each
    test sets the desired quota by monkey-patching the stub at the
    module level (`inbound_module._paywall_check`).
    """
    from app.services.feishu import inbound as inbound_module

    async def _pro_verdict(*, command, redis_client, sender_open_id):
        return PaywallVerdict(
            allowed=True,
            plan="pro",
            quota_type="view_top_signals",
            quota_limit=20,
            quota_used=0,
        )

    monkeypatch.setattr(
        inbound_module, "_paywall_check", _pro_verdict
    )


def _make_router(handler: Any) -> FeishuCommandRouter:
    settings = get_settings()
    settings.app_base_url = "http://radar.test"
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://radar.test"
    )
    return FeishuCommandRouter(settings=settings, http_client=client)


def _fake_opportunities(
    items: list[dict[str, Any]] | None = None,
    *,
    total: int | None = None,
) -> dict[str, Any]:
    if items is None:
        items = [
            {"id": 1, "title": "AI 法律合同审核", "total_score": 90.0,
             "summary": "大模型解析合同。", "category": "AI SaaS",
             "market": "legal", "target_user": "lawyers"},
            {"id": 2, "title": "AI 电商主播", "total_score": 80.0,
             "summary": "跨境直播 AI 主播。", "category": "AI Media",
             "market": "ecommerce", "target_user": "sellers"},
        ]
    return {"items": items, "total": total if total is not None else len(items)}


# ---------------------------------------------------------------------------
# /search — input validation
# ---------------------------------------------------------------------------
class TestSearchInput:
    async def test_empty_query_returns_usage_hint(self):
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.url.path)
            return httpx.Response(200, json=_fake_opportunities(), request=request)

        router = _make_router(handler)
        reply = await router.route(BotCommand(kind="search", args=""))
        assert "用法" in reply.text
        assert "/search" in reply.text
        assert reply.metadata.get("error") == "missing_query"
        # — No HTTP calls when the query is empty.
        assert captured == []

    async def test_whitespace_only_query_returns_usage_hint(self):
        router = _make_router(
            lambda req: httpx.Response(200, json=_fake_opportunities(), request=req)
        )
        reply = await router.route(BotCommand(kind="search", args="   "))
        assert reply.metadata.get("error") == "missing_query"


# ---------------------------------------------------------------------------
# /search — happy path
# ---------------------------------------------------------------------------
class TestSearchHappyPath:
    async def test_calls_opportunities_with_q_query_param(self):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=_fake_opportunities(), request=request)

        router = _make_router(handler)
        router._sender_open_id = "ou_search_happy"
        reply = await router.route(BotCommand(kind="search", args="AI 法律"))
        assert any(
            "q=" in (r.url.query.decode() if isinstance(r.url.query, bytes) else r.url.query)
            for r in captured
        ), captured
        assert "搜索结果" in reply.text

    async def test_url_encodes_cjk_and_special_chars(self):
        captured_queries: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            q = request.url.query
            captured_queries.append(q.decode() if isinstance(q, bytes) else q)
            return httpx.Response(200, json=_fake_opportunities(), request=request)

        router = _make_router(handler)
        router._sender_open_id = "ou_cjk"
        await router.route(BotCommand(kind="search", args="AI 法律&价格"))
        # The query string passed to the internal API must be URL-encoded —
        # otherwise `&价格` would land as two separate params.
        q_value = captured_queries[0].split("q=", 1)[1].split("&", 1)[0]
        assert "&" not in q_value
        assert quote_plus("AI 法律&价格") in captured_queries[0]

    async def test_renders_results_in_chinese(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": 11, "title": "AI 法律审查", "total_score": 88.0},
                        {"id": 22, "title": "AI 跨境电商", "total_score": 77.0},
                    ]
                },
                request=request,
            )

        router = _make_router(handler)
        reply = await router.route(BotCommand(kind="search", args="AI"))
        assert "搜索结果" in reply.text
        assert "AI 法律审查" in reply.text
        assert "AI 跨境电商" in reply.text
        assert "查看详情" in reply.text
        assert reply.metadata["items_count"] == 2

    async def test_empty_results_returns_helpful_hint(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"items": []}, request=request)

        router = _make_router(handler)
        reply = await router.route(BotCommand(kind="search", args="xyz_no_match"))
        assert "没找到" in reply.text
        assert "/today" in reply.text or "/top" in reply.text
        assert reply.metadata["items_count"] == 0


# ---------------------------------------------------------------------------
# /search — distinct-quota SADD
# ---------------------------------------------------------------------------
class TestSearchQuota:
    async def test_pro_records_all_shown_ids(self, fake_redis):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_fake_opportunities(
                    items=[
                        {"id": 101, "title": "A", "total_score": 90.0},
                        {"id": 102, "title": "B", "total_score": 80.0},
                        {"id": 103, "title": "C", "total_score": 70.0},
                    ]
                ),
                request=request,
            )

        router = _make_router(handler)
        router._sender_open_id = "ou_pro"
        router._redis = fake_redis

        reply = await router.route(BotCommand(kind="search", args="A"))
        assert reply.metadata["view_top_signals_recorded"] is True
        # SADD wrote 3 IDs.
        from app.services.subscriptions.paywall import (
            peek_view_top_signals_count,
        )
        used = await peek_view_top_signals_count(fake_redis, "ou_pro")
        assert used == 3

    async def test_repeat_search_does_not_double_count(self, fake_redis):
        """Phase 16 — SADD is idempotent for the same ID. Second
        /search call with the same hit must NOT bump the SCARD."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_fake_opportunities(
                    items=[
                        {"id": 200, "title": "A", "total_score": 90.0},
                        {"id": 201, "title": "B", "total_score": 80.0},
                    ]
                ),
                request=request,
            )

        router = _make_router(handler)
        router._sender_open_id = "ou_dup"
        router._redis = fake_redis

        await router.route(BotCommand(kind="search", args="A"))
        await router.route(BotCommand(kind="search", args="A"))
        from app.services.subscriptions.paywall import (
            peek_view_top_signals_count,
        )
        used = await peek_view_top_signals_count(fake_redis, "ou_dup")
        assert used == 2  # not 4

    async def test_search_truncates_to_residual_quota(self, monkeypatch, fake_redis):
        """Free user (limit=1, used=0) searching must only see 1 item
        even though the API returns 5."""
        from app.services.feishu import inbound as inbound_module

        async def _free_verdict(*, command, redis_client, sender_open_id):
            return PaywallVerdict(
                allowed=True,
                plan="free",
                quota_type="view_top_signals",
                quota_limit=1,
                quota_used=0,
            )

        monkeypatch.setattr(inbound_module, "_paywall_check", _free_verdict)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_fake_opportunities(
                    items=[
                        {"id": i, "title": f"T{i}", "total_score": 90.0 - i}
                        for i in range(1, 6)
                    ]
                ),
                request=request,
            )

        router = _make_router(handler)
        router._sender_open_id = "ou_free"
        router._redis = fake_redis

        reply = await router.route(BotCommand(kind="search", args="T"))
        assert reply.metadata["items_count"] == 1
        from app.services.subscriptions.paywall import (
            peek_view_top_signals_count,
        )
        used = await peek_view_top_signals_count(fake_redis, "ou_free")
        assert used == 1

    async def test_no_redis_records_still_renders(self):
        """Redis missing → record_view_top_signals no-ops, but the
        handler still returns results (fail-open)."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_fake_opportunities(items=[
                    {"id": 1, "title": "X", "total_score": 90.0}
                ]),
                request=request,
            )

        router = _make_router(handler)
        router._sender_open_id = "ou_noredis"
        router._redis = None  # explicit no-redis
        reply = await router.route(BotCommand(kind="search", args="X"))
        assert "X" in reply.text
        assert reply.metadata["view_top_signals_recorded"] is False
