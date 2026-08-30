"""MVP Feishu bot command tests (simplify §10).

Verifies the 3 newly-added dispatchers:

  /run     → POST /api/internal/pipeline/run
  /status  → GET  /api/internal/status
  /sources → GET  /api/internal/sources/healthy

The 4th and 5th MVP commands (/help, /today) are already covered by
``tests/test_feishu_inbound.py`` and not re-tested here.

The tests use httpx.MockTransport to stub the internal-API round-trip
so they run fully offline — no DB / Redis / Feishu required.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import get_settings
from app.services.feishu.inbound import (
    BotCommand,
    FeishuCommandRouter,
    parse_command,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _stub_paywall(monkeypatch):
    """`/run /status /sources` don't carry a quota — paywall check is
    never reached for them. Stub anyway so ``route()`` doesn't open a
    DB session in offline tests.
    """
    from app.services.feishu import inbound as inbound_module

    async def _noop_paywall(**_kwargs):
        from app.services.paywall import PaywallVerdict

        return PaywallVerdict(
            allowed=True,
            plan="unknown",
            quota_type="bypass",
            quota_limit=0,
            quota_used=0,
        )

    monkeypatch.setattr(inbound_module, "_paywall_check", _noop_paywall)


def _make_router(handler) -> FeishuCommandRouter:
    settings = get_settings()
    settings.app_base_url = "http://radar.test"
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://radar.test")
    return FeishuCommandRouter(settings=settings, http_client=client)


def _ok_json(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


# ---------------------------------------------------------------------------
# parse_command — MVP commands recognised
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected_kind",
    [
        ("/run", "run"),
        ("/运行", "run"),
        ("/status", "status"),
        ("/状态", "status"),
        ("/sources", "sources"),
        ("/源", "sources"),
    ],
)
def test_parse_command_mvp_commands(text: str, expected_kind: str):
    cmd = parse_command(text)
    assert cmd.kind == expected_kind


# ---------------------------------------------------------------------------
# /run dispatcher
# ---------------------------------------------------------------------------
async def test_router_run_posts_to_pipeline_run_endpoint():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/api/internal/pipeline/run":
            return _ok_json(
                {
                    "run_id": 7,
                    "status": "success",
                    "trigger": "manual",
                    "started_at": "2026-08-30T08:00:00+00:00",
                    "finished_at": "2026-08-30T08:00:42+00:00",
                    "raw_count": 128,
                    "new_count": 47,
                    "signal_count": 12,
                    "digest_sent": True,
                    "error": None,
                }
            )
        return httpx.Response(404, json={"detail": "not found"})

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="run"))

    assert len(captured) == 1
    assert captured[0].method == "POST"
    assert captured[0].url.path == "/api/internal/pipeline/run"
    assert "run_id=7" in reply.text
    assert "采集:128" in reply.text
    assert "新增:47" in reply.text
    assert "信号:12" in reply.text
    assert "日报已发送:是" in reply.text
    assert reply.metadata.get("command") == "run"


async def test_router_run_handles_error_response():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/internal/pipeline/run":
            return _ok_json(
                {
                    "run_id": 8,
                    "status": "failed",
                    "trigger": "manual",
                    "raw_count": 0,
                    "new_count": 0,
                    "signal_count": 0,
                    "digest_sent": False,
                    "error": "LLM timeout after 30s",
                }
            )
        return httpx.Response(404)

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="run"))
    assert "任务执行失败" in reply.text
    assert "LLM timeout" in reply.text
    assert reply.metadata.get("error") is True


# ---------------------------------------------------------------------------
# /status dispatcher
# ---------------------------------------------------------------------------
async def test_router_status_renders_run_summary():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/internal/status":
            return _ok_json(
                {
                    "last_run": {
                        "id": 7,
                        "status": "success",
                        "trigger": "scheduler",
                        "started_at": "2026-08-30T08:00:00+00:00",
                        "finished_at": "2026-08-30T08:00:42+00:00",
                        "raw_count": 128,
                        "new_count": 47,
                        "signal_count": 12,
                        "error": None,
                    },
                    "sources": {
                        "total": 5,
                        "healthy": 5,
                        "items": [],
                    },
                    "total_signals": 12,
                    "now": "2026-08-30T08:30:00+00:00",
                }
            )
        return httpx.Response(404)

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="status"))
    assert "系统状态" in reply.text
    assert "Collector: OK" in reply.text
    assert "Database: OK" in reply.text
    assert "Last Run" in reply.text
    assert "success" in reply.text
    assert "信息源: 5 / 5 healthy" in reply.text
    assert "累计信号: 12" in reply.text


async def test_router_status_handles_no_run_yet():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/internal/status":
            return _ok_json(
                {
                    "last_run": None,
                    "sources": {"total": 5, "healthy": 0, "items": []},
                    "total_signals": 0,
                    "now": "2026-08-30T08:30:00+00:00",
                }
            )
        return httpx.Response(404)

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="status"))
    assert "Last Run: 暂无" in reply.text


async def test_router_status_handles_endpoint_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "service unavailable"})

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="status"))
    assert "暂时无法获取系统状态" in reply.text


# ---------------------------------------------------------------------------
# /sources dispatcher
# ---------------------------------------------------------------------------
async def test_router_sources_lists_enabled_sources():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/internal/sources/healthy":
            return _ok_json(
                {
                    "count": 5,
                    "healthy": 4,
                    "items": [
                        {
                            "id": 1,
                            "name": "Hacker News",
                            "type": "hackernews",
                            "url": "https://news.ycombinator.com",
                            "healthy": True,
                            "last_success_at": "2026-08-30T07:55:00+00:00",
                            "last_error_at": None,
                            "compliance_level": "A",
                        },
                        {
                            "id": 2,
                            "name": "Reddit",
                            "type": "reddit",
                            "url": "https://reddit.com",
                            "healthy": True,
                            "last_success_at": "2026-08-30T07:55:00+00:00",
                            "last_error_at": None,
                            "compliance_level": "B",
                        },
                        {
                            "id": 3,
                            "name": "Broken",
                            "type": "rss",
                            "url": "https://example.com/feed",
                            "healthy": False,
                            "last_success_at": None,
                            "last_error_at": "2026-08-30T07:30:00+00:00",
                            "compliance_level": "C",
                        },
                    ],
                }
            )
        return httpx.Response(404)

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="sources"))
    assert "当前信息源" in reply.text
    assert "✓ Hacker News" in reply.text
    assert "✗ Broken" in reply.text
    assert "状态: 4 / 5 healthy" in reply.text


async def test_router_sources_handles_no_enabled_sources():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/internal/sources/healthy":
            return _ok_json({"count": 0, "healthy": 0, "items": []})
        return httpx.Response(404)

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="sources"))
    assert "当前没有启用的信息源" in reply.text
