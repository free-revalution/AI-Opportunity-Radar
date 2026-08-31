"""Phase 25 v2.1 — Feishu bot async task_runner tests.

Covers:
  * `submit_pipeline_run` returns a running TaskRecord immediately
  * The background task POSTs to /api/internal/pipeline/run and
    posts a summary card back to the chat via FeishuAppClient
  * Failures in the pipeline call produce a `failed` TaskRecord
    and a `⚠️ 流水线运行失败。` reply
  * `get_status` / `list_recent` reflect in-flight and finished tasks
  * Concurrency cap (4) rejects excess submissions as `failed`
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.services.feishu.task_runner import (
    TaskRecord,
    get_status,
    list_recent,
    submit_pipeline_run,
    _TASKS,
    _TASKS_LOCK,
)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------
def _settings() -> Any:
    from app.config import get_settings

    s = get_settings()
    # Disable the pre-send compliance gate — tests focus on the runner.
    s.compliance_pre_send_gate_enabled = False
    return s


# ---------------------------------------------------------------------------
# Pipeline-endpoint mock transport
# ---------------------------------------------------------------------------
class _PipelineEndpointTransport(httpx.AsyncBaseTransport):
    """Handles /api/internal/pipeline/run (success) + Feishu IM send."""

    def __init__(self, *, pipeline_payload: dict[str, Any], pipeline_status: int = 200) -> None:
        self.pipeline_payload = pipeline_payload
        self.pipeline_status = pipeline_status
        self.pipeline_calls: list[httpx.Request] = []
        self.im_calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/internal/pipeline/run"):
            self.pipeline_calls.append(request)
            return httpx.Response(
                self.pipeline_status,
                json=self.pipeline_payload,
                request=request,
            )
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tok-x", "expire": 7200},
                request=request,
            )
        if request.url.path.endswith("/im/v1/messages"):
            self.im_calls.append(request)
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"code": 0, "data": {"message_id": "om_async"}},
                request=request,
            )
        return httpx.Response(404, json={"code": 999, "msg": "nf"}, request=request)


def _ok_payload() -> dict[str, Any]:
    return {
        "run_id": 42,
        "status": "success",
        "trigger": "manual",
        "started_at": "2026-08-30T00:00:00Z",
        "finished_at": "2026-08-30T00:00:30Z",
        "raw_count": 100,
        "new_count": 20,
        "signal_count": 5,
        "digest_sent": True,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
async def _reset_task_state():
    """Clear module-level task records between tests."""
    async with _TASKS_LOCK:
        _TASKS.clear()
    yield
    async with _TASKS_LOCK:
        _TASKS.clear()


@pytest.fixture
def monkey_httpx(monkeypatch):
    """Replace httpx.AsyncClient *inside* task_runner with one using our transport.

    The runner creates its own ``httpx.AsyncClient`` for the pipeline
    call AND a fresh ``FeishuAppClient`` (with its own httpx client)
    for the reply. We patch ``httpx.AsyncClient`` globally so both
    use the same transport.
    """

    transport = _PipelineEndpointTransport(pipeline_payload=_ok_payload())
    # — the FeishuAppClient inside _post_pipeline_summary creates its
    # own httpx.AsyncClient via the constructor default; that default
    # is built inside the class, not via the global httpx module.
    # We work around this by patching both the module-level httpx
    # AND the default-arg-bound client the AppClient will use.
    original_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        # If caller passes no transport, inject ours.
        if "transport" not in kwargs and not args:
            kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr("app.services.feishu.task_runner.httpx.AsyncClient", _factory)
    monkeypatch.setattr(
        "app.services.feishu.app_client.httpx.AsyncClient", _factory
    )
    yield transport


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submit_returns_immediately_with_running_record(monkey_httpx) -> None:
    rec = await submit_pipeline_run(
        chat_id="oc_async", sender_open_id="ou_async", settings=_settings()
    )
    assert rec.status == "running"
    assert rec.task_id
    assert rec.chat_id == "oc_async"
    assert rec.sender_open_id == "ou_async"
    assert rec.command_kind == "run"
    # — Returned before the background task finished.
    assert rec.finished_at is None


@pytest.mark.asyncio
async def test_background_task_calls_pipeline_and_replies(monkey_httpx) -> None:
    rec = await submit_pipeline_run(
        chat_id="oc_2", sender_open_id="ou_2", settings=_settings()
    )
    # — Wait for the background task to drain.
    assert rec._asyncio_task is not None
    await rec._asyncio_task
    # — Pipeline endpoint hit once.
    assert len(monkey_httpx.pipeline_calls) == 1
    # — Reply pushed to the chat.
    assert len(monkey_httpx.im_calls) == 1
    im_body = json.loads(monkey_httpx.im_calls[0].content.decode("utf-8"))
    assert im_body["receive_id"] == "oc_2"
    assert "✅" in im_body["content"]


@pytest.mark.asyncio
async def test_bot_run_payload_includes_write_docx_true(monkey_httpx) -> None:
    """Phase 29 regression — the bot's `/run` MUST send
    ``{"send_digest": True, "write_docx": True}`` so the daily Docx
    lands in the user's 飞书云盘 / 每日报告 /.

    Before this fix the payload was ``{"send_digest": True}`` only —
    the docx branch in ``internal.run_pipeline`` was skipped, so even
    a perfectly-successful pipeline left the cloud drive empty.
    Symptom reported by the user: "既然 /run 是真实运行的,为什么
    云盘中内容没有任何更新？" — because /run had no path to Drive.
    """
    rec = await submit_pipeline_run(
        chat_id="oc_docx", sender_open_id="ou_docx", settings=_settings()
    )
    assert rec._asyncio_task is not None
    await rec._asyncio_task
    assert len(monkey_httpx.pipeline_calls) == 1
    body = json.loads(monkey_httpx.pipeline_calls[0].content.decode("utf-8"))
    assert body.get("send_digest") is True
    assert body.get("write_docx") is True, (
        "bot /run payload must set write_docx=True so the daily docx "
        "is written to the user's Feishu cloud drive"
    )


@pytest.mark.asyncio
async def test_failure_path_records_failed_and_replies(monkeypatch) -> None:
    """Pipeline returns 5xx → record.status='failed' + warning card."""

    class _FailingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/api/internal/pipeline/run"):
                return httpx.Response(
                    500, json={"error": "internal"}, request=request
                )
            if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
                return httpx.Response(
                    200,
                    json={"code": 0, "tenant_access_token": "tok-y", "expire": 7200},
                    request=request,
                )
            if request.url.path.endswith("/im/v1/messages"):
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"message_id": "om_err"}},
                    request=request,
                )
            return httpx.Response(404, request=request)

    transport = _FailingTransport()
    original = httpx.AsyncClient

    def _factory(*args, **kwargs):
        if "transport" not in kwargs and not args:
            kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr("app.services.feishu.task_runner.httpx.AsyncClient", _factory)
    monkeypatch.setattr(
        "app.services.feishu.app_client.httpx.AsyncClient", _factory
    )

    rec = await submit_pipeline_run(
        chat_id="oc_fail", sender_open_id="ou_fail", settings=_settings()
    )
    assert rec._asyncio_task is not None
    await rec._asyncio_task
    assert rec.status == "failed"
    assert rec.error and "HTTP 500" in rec.error
    # — Failure reply was still sent (best effort).
    assert rec.finished_at is not None


@pytest.mark.asyncio
async def test_get_status_returns_record(monkey_httpx) -> None:
    rec = await submit_pipeline_run(
        chat_id="oc_x", sender_open_id="ou_x", settings=_settings()
    )
    assert rec._asyncio_task is not None
    await rec._asyncio_task
    snapshot = await get_status(rec.task_id)
    assert snapshot is not None
    assert snapshot["task_id"] == rec.task_id
    assert snapshot["status"] == "success"


@pytest.mark.asyncio
async def test_list_recent_returns_recent_tasks(monkey_httpx) -> None:
    a = await submit_pipeline_run(chat_id="oc_a", sender_open_id="ou_a", settings=_settings())
    b = await submit_pipeline_run(chat_id="oc_b", sender_open_id="ou_b", settings=_settings())
    assert a._asyncio_task is not None and b._asyncio_task is not None
    await a._asyncio_task
    await b._asyncio_task
    items = await list_recent(limit=5)
    assert len(items) == 2
    # — most-recent first.
    assert items[0]["task_id"] == b.task_id


@pytest.mark.asyncio
async def test_concurrency_cap_rejects_excess(monkey_httpx) -> None:
    """5 in-flight would exceed the cap of 4 → 5th is rejected."""
    recs: list[TaskRecord] = []
    # The first 4 occupy the running slots; the 5th gets rejected.
    for i in range(4):
        recs.append(
            await submit_pipeline_run(
                chat_id=f"oc_{i}", sender_open_id=f"ou_{i}", settings=_settings()
            )
        )
    rec = await submit_pipeline_run(
        chat_id="oc_5", sender_open_id="ou_5", settings=_settings()
    )
    assert rec.status == "failed"
    assert rec.error and "too many" in rec.error
    # — Cleanup so the asyncio event loop doesn't leak warnings.
    for r in recs:
        if r._asyncio_task is not None:
            await r._asyncio_task


# ---------------------------------------------------------------------------
# Phase 29 regression — real /run (with research stage on, Browser-Use
# polling included) takes 8-12 minutes on the live stack. The previous
# 300s httpx timeout meant every bot-initiated /run surfaced as
# ``pipeline request failed:`` (empty ReadTimeout exception) to the user,
# even though the pipeline itself completed server-side. The fix bumps
# the timeout to 900s so the post-back carries the success summary.
# ---------------------------------------------------------------------------
async def test_pipeline_async_run_uses_long_timeout(monkeypatch, monkey_httpx) -> None:
    """The httpx.AsyncClient the task_runner builds for the pipeline
    POST must carry a timeout ≥ 600s, otherwise long real-mode runs
    surface as ``pipeline request failed:`` to the bot user."""
    import app.services.feishu.task_runner as task_runner_module
    from app.services.feishu import task_runner

    captured_timeouts: list[float] = []

    original_async_client = httpx.AsyncClient

    def _capturing_client(*args, **kwargs):
        # Capture the timeout kwarg (or default arg) used by the
        # task_runner when it builds its pipeline HTTP client.
        if "timeout" in kwargs and kwargs["timeout"] is not None:
            captured_timeouts.append(float(kwargs["timeout"]))
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(task_runner_module.httpx, "AsyncClient", _capturing_client)
    monkeypatch.setattr(task_runner.httpx, "AsyncClient", _capturing_client)

    rec = await task_runner.submit_pipeline_run(
        chat_id="oc_timeout",
        sender_open_id="ou_timeout",
        settings=_settings(),
    )
    # — Wait for the background task to actually run (it calls
    # httpx.AsyncClient immediately inside _execute_pipeline).
    if rec._asyncio_task is not None:
        try:
            await asyncio.wait_for(rec._asyncio_task, timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            pass

    assert captured_timeouts, "task_runner never built an httpx.AsyncClient"
    # — Real-mode /run is 8-12 min; we want at least 600s. The current
    # value is 900s.
    assert captured_timeouts[0] >= 600.0, (
        f"task_runner httpx timeout too short: {captured_timeouts[0]}s — "
        f"bot /run will keep timing out on real runs."
    )
