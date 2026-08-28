"""Tests for Phase 7 Feishu inbound commands — /report /doc /table.

Builds on the Phase 6 patterns from `test_feishu_inbound.py`:
  * router tests use `_make_router()` + a transport handler that
    intercepts httpx calls going to the internal API.
  * Drive / Bitable are **stubbed** via the optional `drive_client=`,
    `bitable_digest_client=`, `bitable_ops_client=` kwargs so we
    don't need real Feishu credentials.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.services.feishu.content_client import FeishuContentError
from app.services.feishu.inbound import (
    BotCommand,
    FeishuCommandRouter,
    _render_report_markdown,
    parse_command,
)


# ---------------------------------------------------------------------------
# Stubs for content clients
# ---------------------------------------------------------------------------
class _StubDrive:
    """Captures `create_docx_from_markdown` calls without real Drive API."""

    def __init__(
        self,
        *,
        configured: bool = True,
        doc_id: str = "DOC_ABC",
        url: str = "https://feishu.cn/docx/DOC_ABC",
        raise_exc: Exception | None = None,
    ) -> None:
        self.is_configured = configured
        self._doc_id = doc_id
        self._url = url
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def create_docx_from_markdown(self, *, title, markdown):
        self.calls.append({"title": title, "markdown": markdown})
        if self._raise is not None:
            raise self._raise
        return {"doc_id": self._doc_id, "url": self._url}


class _StubBitable:
    """Captures bulk_insert + ensure_table calls without real Bitable API."""

    def __init__(
        self,
        *,
        configured: bool = True,
        app_token: str = "BITAPP",
        inserted: int = 0,
        raise_exc: Exception | None = None,
    ) -> None:
        self.is_configured = configured
        self._app_token = app_token
        self._inserted = inserted
        self._raise = raise_exc
        self.inserted_calls: list[list[dict[str, Any]]] = []

    async def ensure_app(self):
        return self._app_token

    async def ensure_table(self):
        return self._app_token, "TBL_1"

    def public_url(self, *, app_token=None) -> str:
        return f"https://feishu.cn/base/{app_token or self._app_token}"

    async def bulk_insert_opportunities(
        self, *, items, base_url_for_links, chunk_size=500
    ):
        self.inserted_calls.append(items)
        if self._raise is not None:
            raise self._raise
        self._inserted = len(items)
        return self._inserted


# ---------------------------------------------------------------------------
# Helpers — router construction
# ---------------------------------------------------------------------------
def _make_router(
    handler: Any,
    *,
    drive: _StubDrive | None = None,
    bitable_digest: _StubBitable | None = None,
    bitable_ops: _StubBitable | None = None,
) -> FeishuCommandRouter:
    """Build a router whose `httpx.AsyncClient` uses an in-memory
    `MockTransport`. Content clients are passed through directly."""
    settings = get_settings()
    settings.app_base_url = "http://radar.test"
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://radar.test"
    )
    return FeishuCommandRouter(
        settings=settings,
        http_client=client,
        drive_client=drive,
        bitable_digest_client=bitable_digest,
        bitable_ops_client=bitable_ops,
    )


def _completed_report_payload(job_id: int = 5) -> dict[str, Any]:
    """One complete on-demand report (matches `get_on_demand_research` shape)."""
    return {
        "job_id": job_id,
        "opportunity_id": 100,
        "opportunity_title": "AI 法律合同审核",
        "status": "completed",
        "recommendation": "GO",
        "confidence": 0.85,
        "sources_count": 1,
        "error": None,
        "started_at": "2026-08-28T10:00:00Z",
        "completed_at": "2026-08-28T10:05:00Z",
        "seed_url": None,
        "seed_topic": "AI 法律合同审核",
        "report": {
            "executive_summary": "大模型可解析 100 页 PDF 合同。",
            "market_analysis": "中国法律科技市场 ¥50 亿/年。",
            "competition_analysis": "竞品: 法狗狗, 法信等。",
            "china_analysis": "国内监管友好。",
            "monetization_analysis": "订阅 ¥299/月。",
            "mvp_analysis": "MVP 周期 30 天。",
            "risk_analysis": "数据合规风险。",
            "recommendation": "GO",
            "confidence": 0.85,
            "sources": [
                {"title": "中国法律科技白皮书 2025", "url": "https://example.com/w.pdf"}
            ],
        },
    }


# ---------------------------------------------------------------------------
# parse_command — new aliases
# ---------------------------------------------------------------------------
def test_parse_command_recognises_report_aliases():
    assert parse_command("/report 5").kind == "report"
    assert parse_command("/doc 5").kind == "report"
    assert parse_command("/文档 5").kind == "report"


def test_parse_command_recognises_table_aliases():
    assert parse_command("/table").kind == "table"
    assert parse_command("/表格").kind == "table"


# ---------------------------------------------------------------------------
# /report handler
# ---------------------------------------------------------------------------
async def test_router_report_pushes_to_docx_and_returns_url():
    """`/report <job_id>` → GET on_demand/{id} → drive.create_docx → reply has doc URL."""
    captured: list[str] = []
    drive = _StubDrive(doc_id="DOC_42", url="https://feishu.cn/docx/DOC_42")

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.path)
        if "/research/on_demand/5" in request.url.path:
            return httpx.Response(200, json=_completed_report_payload(5), request=request)
        return httpx.Response(200, json={}, request=request)

    router = _make_router(handler, drive=drive)
    reply = await router.route(BotCommand(kind="report", args="5"))

    assert "DOC_42" in reply.text
    assert "https://feishu.cn/docx/DOC_42" in reply.text
    assert reply.metadata["job_id"] == 5
    assert reply.metadata["doc_id"] == "DOC_42"
    assert len(drive.calls) == 1
    assert drive.calls[0]["title"].startswith("研究报告 #5")


async def test_router_report_handles_non_numeric_job_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, request=request)

    drive = _StubDrive()
    router = _make_router(handler, drive=drive)
    reply = await router.route(BotCommand(kind="report", args="abc"))
    assert "用法" in reply.text
    assert "/report" in reply.text
    assert drive.calls == []


async def test_router_report_returns_not_configured_when_drive_unset():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, request=request)

    drive = _StubDrive(configured=False)
    router = _make_router(handler, drive=drive)
    reply = await router.route(BotCommand(kind="report", args="5"))
    assert "未配置" in reply.text or "FEISHU_DRIVE" in reply.text


async def test_router_report_handles_pending_job():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "job_id": 5,
                "opportunity_title": "X",
                "status": "running",
                "report": None,
            },
            request=request,
        )

    drive = _StubDrive()
    router = _make_router(handler, drive=drive)
    reply = await router.route(BotCommand(kind="report", args="5"))
    assert "尚未完成" in reply.text or "运行中" in reply.text
    assert drive.calls == []


async def test_router_report_surfaces_historical_pending_job():
    """Regression for production bug: a Phase 5 on-demand job with
    `status=pending` AND `started_at=null` is almost certainly a
    *historical* row from before the on-demand pipeline migrated to
    synchronous mode (the worker was removed but old rows remained).
    The user should be told to re-run via `/research` rather than
    wait indefinitely."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "job_id": 1,
                "opportunity_title": "历史遗留任务",
                "status": "pending",
                "started_at": None,
                "report": None,
            },
            request=request,
        )

    drive = _StubDrive()
    router = _make_router(handler, drive=drive)
    reply = await router.route(BotCommand(kind="report", args="1"))
    # — Tells the user it's a stale historical row + how to fix it.
    assert "历史遗留" in reply.text or "Phase 5" in reply.text
    assert "/research" in reply.text
    assert drive.calls == []


async def test_router_report_translates_drive_failure_to_chat():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completed_report_payload(5), request=request)

    drive = _StubDrive(
        raise_exc=FeishuContentError("drive/v1/import_tasks rejected: quota")
    )
    router = _make_router(handler, drive=drive)
    reply = await router.route(BotCommand(kind="report", args="5"))
    assert "失败" in reply.text
    assert "quota" in reply.text


# ---------------------------------------------------------------------------
# /research auto-Docx upgrade
# ---------------------------------------------------------------------------
async def test_router_research_auto_creates_docx_on_completion():
    """`/research <topic>` calls on_demand synchronously, then if the
    detail endpoint returns `completed`, drive.create_docx runs."""
    drive = _StubDrive()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/research/on_demand" in request.url.path and not request.url.path.endswith("/5"):
            body = json.loads(request.content)
            return httpx.Response(
                200, json={"job_id": 5, "topic": body["topic"]}, request=request
            )
        if "/research/on_demand/5" in request.url.path:
            return httpx.Response(
                200, json=_completed_report_payload(5), request=request
            )
        return httpx.Response(200, json={}, request=request)

    router = _make_router(handler, drive=drive)
    reply = await router.route(BotCommand(kind="research", args="AI 法律合同"))
    assert "飞书云文档已生成" in reply.text
    assert "DOC_ABC" in reply.text
    assert len(drive.calls) == 1


async def test_router_research_silently_skips_docx_on_failure():
    """Drive throws → chat reply still has Web URL; no doc line appended."""
    drive = _StubDrive(raise_exc=FeishuContentError("quota exceeded"))

    def handler(request: httpx.Request) -> httpx.Response:
        if "/research/on_demand/5" in request.url.path:
            return httpx.Response(200, json=_completed_report_payload(5), request=request)
        return httpx.Response(
            200,
            json={"job_id": 5, "topic": "X"},
            request=request,
        )

    router = _make_router(handler, drive=drive)
    reply = await router.route(BotCommand(kind="research", args="X"))
    # — Web link still present, no failure surfaced.
    assert "Web 上查看" in reply.text
    assert "飞书云文档已生成" not in reply.text


async def test_router_research_does_not_call_drive_if_unconfigured():
    """When drive.is_configured is False, /research doesn't even try."""
    drive = _StubDrive(configured=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job_id": 5, "topic": "X"}, request=request)

    router = _make_router(handler, drive=drive)
    reply = await router.route(BotCommand(kind="research", args="X"))
    assert "Web 上查看" in reply.text
    assert drive.calls == []


# ---------------------------------------------------------------------------
# /table handler
# ---------------------------------------------------------------------------
async def test_router_table_manual_syncs_and_returns_count():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/opportunities" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": 1, "title": "A", "total_score": 90.0},
                        {"id": 2, "title": "B", "total_score": 80.0},
                    ],
                    "total": 2,
                },
                request=request,
            )
        return httpx.Response(200, json={}, request=request)

    bitable = _StubBitable(app_token="BIT_OPS")
    router = _make_router(handler, bitable_ops=bitable)
    reply = await router.route(BotCommand(kind="table"))
    assert "已同步 2 条机会" in reply.text
    assert "https://feishu.cn/base/BIT_OPS" in reply.text
    assert reply.metadata["inserted"] == 2
    assert len(bitable.inserted_calls) == 1
    assert len(bitable.inserted_calls[0]) == 2


async def test_router_table_handles_no_opportunities():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []}, request=request)

    bitable = _StubBitable()
    router = _make_router(handler, bitable_ops=bitable)
    reply = await router.route(BotCommand(kind="table"))
    assert "没有" in reply.text or "无可同步" in reply.text or "先跑" in reply.text
    assert bitable.inserted_calls == []


async def test_router_table_returns_not_configured_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"id": 1, "title": "A", "total_score": 80.0}]},
            request=request,
        )

    bitable = _StubBitable(configured=False, raise_exc=FeishuContentError("not configured"))
    router = _make_router(handler, bitable_ops=bitable)
    reply = await router.route(BotCommand(kind="table"))
    assert "失败" in reply.text or "未配置" in reply.text


# ---------------------------------------------------------------------------
# /daily — Bitable sync upgrade
# ---------------------------------------------------------------------------
async def test_router_daily_attempts_bitable_sync():
    """`/daily` triggers digest AND bulk-insert into bitable_digest."""
    captured_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_paths.append(request.url.path)
        if "/notifications/digest/send" in request.url.path:
            return httpx.Response(200, json={"status": "queued"}, request=request)
        if "/api/opportunities" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": 1, "title": "A", "total_score": 90.0},
                        {"id": 2, "title": "B", "total_score": 80.0},
                    ]
                },
                request=request,
            )
        return httpx.Response(200, json={}, request=request)

    bitable = _StubBitable(app_token="BIT_DI")
    router = _make_router(handler, bitable_digest=bitable)
    reply = await router.route(BotCommand(kind="daily"))
    assert any("/notifications/digest/send" in p for p in captured_paths)
    assert any("/api/opportunities" in p for p in captured_paths)
    assert "Top" in reply.text or "已同步" in reply.text
    assert "https://feishu.cn/base/BIT_DI" in reply.text


async def test_router_daily_silently_swallows_bitable_failure():
    """Bitable sync raises → chat reply still says 'pushed'."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/notifications/digest/send" in request.url.path:
            return httpx.Response(200, json={"status": "ok"}, request=request)
        return httpx.Response(
            200,
            json={"items": [{"id": 1, "title": "A", "total_score": 80.0}]},
            request=request,
        )

    bitable = _StubBitable(raise_exc=FeishuContentError("boom"))
    router = _make_router(handler, bitable_digest=bitable)
    reply = await router.route(BotCommand(kind="daily"))
    assert "已触发日报推送" in reply.text
    # — No Bitable URL on failure.
    assert "feishu.cn/base/" not in reply.text


# ---------------------------------------------------------------------------
# /help — new entries
# ---------------------------------------------------------------------------
async def test_router_help_includes_new_commands():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, request=request)

    router = _make_router(handler)
    reply = await router.route(BotCommand(kind="help"))
    assert "/report" in reply.text
    assert "/doc" in reply.text
    assert "/table" in reply.text


# ---------------------------------------------------------------------------
# Markdown helper
# ---------------------------------------------------------------------------
def test_render_report_markdown_emits_known_sections():
    detail = _completed_report_payload(5)
    report = detail["report"]
    md = _render_report_markdown(detail, report)
    assert "# AI 法律合同审核" in md
    assert "**GO**" in md
    assert "85%" in md
    assert "## 执行摘要" in md
    assert "## 市场分析" in md
    assert "## 来源" in md
    assert "中国法律科技白皮书 2025" in md


def test_render_report_markdown_handles_missing_sections():
    """Only some sections populated → others skipped gracefully."""
    detail = {"opportunity_title": "X"}
    report = {"executive_summary": "Body.", "recommendation": "GO", "confidence": 0.5}
    md = _render_report_markdown(detail, report)
    assert "## 执行摘要" in md
    assert "## 市场分析" not in md


def test_render_report_markdown_handles_invalid_confidence():
    detail = {"opportunity_title": "X"}
    report = {"confidence": "not-a-number", "recommendation": "GO"}
    md = _render_report_markdown(detail, report)
    assert "n/a" in md  # graceful fallback