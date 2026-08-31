"""Phase 25 v2.1 — Feishu bot async task runner.

The Feishu per-event reply window is ~30 s. A full MVP pipeline
(discovery → clustering → scoring → screening → research → digest)
takes longer than that on a cold start (deep-research alone can
take ~20 s). Running it synchronously inside the inbound event
handler means the bot reply exceeds the IM timeout and Feishu
retries — which the F.2 idempotency layer then has to dedup.

This module solves that by **submitting** the work to an asyncio
background task and returning a ``task_id`` immediately. The bot
replies "task submitted (id=xxx)" within Feishu's window, and the
background task posts the result back to the original chat once
it finishes (success or failure).

Lifecycle:

  1. ``submit_pipeline_run()``  — schedules the asyncio task and
                                  returns ``task_id`` synchronously.
  2. ``_execute_pipeline()``    — runs the 6-stage pipeline against
                                  the internal HTTP API (the same
                                  path n8n's daily cron uses) and
                                  pushes a summary card back to the
                                  originating chat via
                                  :class:`FeishuAppClient`.
  3. ``get_status(task_id)``    — read-only probe so the inbound
                                  layer / /status can mention
                                  "task #N is running / done".

Concurrency: tasks share a process-level ``asyncio.Task`` set so
the event loop can track them across requests. Failed tasks are
retained long enough for `/status` to surface the error, then
garbage-collected after ``_TASK_RETENTION_SEC`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.utils import get_logger

logger = get_logger(__name__)


_TASK_RETENTION_SEC = 300  # keep finished task records for 5 min so /status can show them
_MAX_CONCURRENT_TASKS = 4  # bound resource use; 4 in-flight pipelines is plenty for /run spam


@dataclass(slots=True)
class TaskRecord:
    """In-memory bookkeeping for one submitted pipeline run.

    Stored in ``_TASKS`` so the inbound handler can read the state
    (e.g. for the reply text) and so /status can mention recent ones.
    """

    task_id: str
    submitted_at: float
    chat_id: str
    sender_open_id: str
    command_kind: str
    receive_id_type: str
    finished_at: Optional[float] = None
    status: str = "running"  # running | success | failed
    result_summary: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    _asyncio_task: Optional[asyncio.Task[Any]] = None

    def to_public_dict(self) -> dict[str, Any]:
        """Strip the asyncio handle before exposing to /status."""
        return {
            "task_id": self.task_id,
            "submitted_at": self.submitted_at,
            "chat_id": self.chat_id,
            "sender_open_id": self.sender_open_id,
            "command_kind": self.command_kind,
            "receive_id_type": self.receive_id_type,
            "finished_at": self.finished_at,
            "status": self.status,
            "result_summary": self.result_summary,
            "error": self.error,
        }


_TASKS: dict[str, TaskRecord] = {}
_TASKS_LOCK = asyncio.Lock()


def _now() -> float:
    return time.time()


async def _gc_old_tasks() -> None:
    """Drop finished task records older than ``_TASK_RETENTION_SEC``."""
    cutoff = _now() - _TASK_RETENTION_SEC
    async with _TASKS_LOCK:
        stale = [
            tid
            for tid, rec in _TASKS.items()
            if rec.finished_at is not None and rec.finished_at < cutoff
        ]
        for tid in stale:
            _TASKS.pop(tid, None)


async def _count_running() -> int:
    async with _TASKS_LOCK:
        return sum(1 for r in _TASKS.values() if r.status == "running")


async def submit_pipeline_run(
    *,
    chat_id: str,
    sender_open_id: str,
    command_kind: str = "run",
    receive_id_type: str = "chat_id",
    settings: Optional[Settings] = None,
) -> TaskRecord:
    """Schedule a background pipeline run; return immediately with a task_id.

    The returned :class:`TaskRecord` is *running* — read
    ``record.finished_at`` later or call :func:`get_status` to see
    whether it succeeded.
    """
    settings = settings or get_settings()
    task_id = uuid.uuid4().hex[:12]
    record = TaskRecord(
        task_id=task_id,
        submitted_at=_now(),
        chat_id=chat_id,
        sender_open_id=sender_open_id,
        command_kind=command_kind,
        receive_id_type=receive_id_type,
    )

    running = await _count_running()
    if running >= _MAX_CONCURRENT_TASKS:
        record.status = "failed"
        record.finished_at = _now()
        record.error = (
            f"too many concurrent pipeline runs ({running}/{_MAX_CONCURRENT_TASKS})"
        )
        async with _TASKS_LOCK:
            _TASKS[task_id] = record
        logger.warning(
            "feishu_async_run_rejected",
            chat_id=chat_id,
            running=running,
        )
        return record

    async with _TASKS_LOCK:
        _TASKS[task_id] = record

    record._asyncio_task = asyncio.create_task(
        _execute_pipeline(record=record, settings=settings),
        name=f"feishu-pipeline-{task_id}",
    )
    # Surface uncaught exceptions (defensive — _execute_pipeline should
    # always finish_success/failed itself, but if asyncio's GC beats
    # us to a cancellation we still want a stack trace).
    record._asyncio_task.add_done_callback(_log_task_done)

    # Best-effort GC of older records.
    asyncio.create_task(_gc_old_tasks())

    logger.info(
        "feishu_async_run_submitted",
        task_id=task_id,
        chat_id=chat_id,
        sender=sender_open_id,
        command=command_kind,
    )
    return record


def _log_task_done(asyncio_task: asyncio.Task[Any]) -> None:
    """Done callback — only logs unexpected exceptions."""
    if asyncio_task.cancelled():
        return
    exc = asyncio_task.exception()
    if exc is not None:
        logger.error(
            "feishu_async_run_unhandled_exception",
            error=str(exc),
            exc_info=exc,
        )


async def get_status(task_id: str) -> Optional[dict[str, Any]]:
    """Public read-only probe for /status."""
    async with _TASKS_LOCK:
        rec = _TASKS.get(task_id)
        return rec.to_public_dict() if rec else None


async def list_recent(*, limit: int = 5) -> list[dict[str, Any]]:
    """Most-recent N task records (for /status)."""
    async with _TASKS_LOCK:
        items = sorted(
            _TASKS.values(), key=lambda r: r.submitted_at, reverse=True
        )[:limit]
        return [r.to_public_dict() for r in items]


# ---------------------------------------------------------------------------
# Phase 26 — /docs tree async task
# ---------------------------------------------------------------------------
async def submit_docs_tree_task(
    *,
    chat_id: str,
    sender_open_id: str,
    settings: Optional[Settings] = None,
    redis_client: Optional[Any] = None,
    receive_id_type: str = "chat_id",
) -> TaskRecord:
    """Schedule a background tree-walk for ``/docs tree``.

    Reuses the same ``_TASKS`` dict / asyncio.Task pattern as
    :func:`submit_pipeline_run` so ``/status`` can surface the
    in-flight task. Walks the 4-section Drive Org tree + a Bitable
    table snapshot, then posts a multi-card reply (split at 3 500
    chars each) back to ``chat_id``.

    Phase 27 fix — the background task constructs its OWN
    :class:`FeishuAppClient` (and its own Drive/Bitable/ConfirmStore
    stack) so the inbound event handler's request-scoped httpx
    client — which is ``aclose()``'d in the handler's ``finally``
    block before this background task finishes — does NOT corrupt
    the async walk. Before this fix the background task reused the
    inbound handler's DriveClient + shared httpx and crashed with
    ``Cannot send a request, as the client has been closed`` for
    every ``/docs tree`` invocation.
    """
    settings = settings or get_settings()
    task_id = uuid.uuid4().hex[:12]
    record = TaskRecord(
        task_id=task_id,
        submitted_at=_now(),
        chat_id=chat_id,
        sender_open_id=sender_open_id,
        command_kind="docs_tree",
        receive_id_type=receive_id_type,
    )

    running = await _count_running()
    if running >= _MAX_CONCURRENT_TASKS:
        record.status = "failed"
        record.finished_at = _now()
        record.error = (
            f"too many concurrent background tasks "
            f"({running}/{_MAX_CONCURRENT_TASKS})"
        )
        async with _TASKS_LOCK:
            _TASKS[task_id] = record
        logger.warning(
            "feishu_docs_tree_rejected",
            chat_id=chat_id,
            running=running,
        )
        return record

    async with _TASKS_LOCK:
        _TASKS[task_id] = record

    record._asyncio_task = asyncio.create_task(
        _execute_docs_tree(
            record=record,
            settings=settings,
            redis_client=redis_client,
        ),
        name=f"feishu-docs-tree-{task_id}",
    )
    record._asyncio_task.add_done_callback(_log_task_done)
    asyncio.create_task(_gc_old_tasks())
    logger.info(
        "feishu_docs_tree_submitted",
        task_id=task_id,
        chat_id=chat_id,
        sender=sender_open_id,
    )
    return record


async def _execute_docs_tree(
    *,
    record: TaskRecord,
    settings: Settings,
    redis_client: Optional[Any] = None,
) -> None:
    """Run walk_tree + Bitable scan, then push a multi-card reply.

    Builds an independent FeishuAppClient + Drive + Bitable +
    ConfirmStore stack so the background task does NOT depend on
    any client owned by the inbound event handler (whose httpx
    AsyncClient is aclose()'d in the handler's finally block before
    this task finishes).
    """
    from app.services.feishu.app_client import FeishuAppClient

    summary: dict[str, Any] = {}
    error_text: Optional[str] = None
    cards: list[str] = []

    app_client = FeishuAppClient(settings=settings)
    try:
        # — Build an independent Drive/Bitable/Confirm stack.
        from app.services.feishu.bitable_manager import BitableManager
        from app.services.feishu.confirm_store import get_confirm_store
        from app.services.feishu.content_client import (
            FeishuBitableClient,
            FeishuDriveClient,
        )
        from app.services.feishu.drive_manager import DriveManager

        drive = FeishuDriveClient(app_client=app_client, settings=settings)
        bitable_client = FeishuBitableClient(
            app_client=app_client,
            settings=settings,
            token_setting="feishu_bitable_opportunities_app_token",
        )
        confirm_store = (
            get_confirm_store(redis_client) if redis_client is not None else None
        )
        drive_manager = DriveManager(
            drive=drive, settings=settings, confirm_store=confirm_store
        )
        bitable_manager = BitableManager(
            client=bitable_client, settings=settings, confirm_store=confirm_store
        )

        tree = await drive_manager.walk(max_depth=3)
        cards.append(_render_tree_card(tree))

        bitable_summary = await _bitable_snapshot(bitable_manager=bitable_manager)
        cards.append(_render_bitable_card(bitable_summary))

        summary = {
            "tree": _summarise_tree(tree),
            "bitable": bitable_summary,
        }
    except Exception as exc:  # noqa: BLE001 — log + surface as friendly reply
        error_text = str(exc)
        logger.error(
            "feishu_docs_tree_execute_failed",
            task_id=record.task_id,
            error=str(exc),
            exc_info=True,
        )
    finally:
        await app_client.aclose()

    record.finished_at = _now()
    if error_text is None:
        record.status = "success"
        record.result_summary = summary
    else:
        record.status = "failed"
        record.error = error_text
        record.result_summary = summary

    try:
        await _post_docs_tree_reply(
            record=record,
            settings=settings,
            cards=cards,
            error_text=error_text,
        )
    except Exception as exc:  # noqa: BLE001 — never let the reply blow up the task
        logger.error(
            "feishu_docs_tree_reply_failed",
            task_id=record.task_id,
            error=str(exc),
            exc_info=True,
        )


async def _post_docs_tree_reply(
    *,
    record: TaskRecord,
    settings: Settings,
    cards: list[str],
    error_text: Optional[str],
) -> None:
    """Push the rendered tree cards back to the originating chat.

    Builds a Feishu ``interactive`` message per card so a long
    tree is delivered as a stacked thread instead of one
    truncated message.
    """
    from app.services.feishu.app_client import FeishuAppClient

    client = FeishuAppClient(settings=settings)
    try:
        if error_text is not None:
            await client.send_message(
                receive_id=record.chat_id,
                receive_id_type=record.receive_id_type,
                msg_type="text",
                content={
                    "text": (
                        f"⚠️ /docs tree 失败\n"
                        f"task_id: {record.task_id}\n"
                        f"错误: {error_text[:240]}"
                    )
                },
                compliance_context="feishu_docs_tree_failure",
            )
            return
        if not cards:
            await client.send_message(
                receive_id=record.chat_id,
                receive_id_type=record.receive_id_type,
                msg_type="text",
                content={"text": "🌳 树状结构为空。"},
                compliance_context="feishu_docs_tree_empty",
            )
            return
        for idx, body in enumerate(cards, start=1):
            await client.send_message(
                receive_id=record.chat_id,
                receive_id_type=record.receive_id_type,
                msg_type="interactive",
                content={
                    "config": {"wide_screen_mode": True},
                    "elements": [
                        {
                            "tag": "div",
                            "text": {"tag": "lark_md", "content": body},
                        }
                    ],
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": (
                                f"🌳 /docs tree ({idx}/{len(cards)})"
                            ),
                        },
                        "template": "blue",
                    },
                },
                compliance_context="feishu_docs_tree_success",
            )
    finally:
        await client.aclose()


def _render_tree_card(tree: dict[str, Any]) -> str:
    """Render the walk_tree() result as a Feishu lark_md card body."""
    lines: list[str] = ["**📁 飞书云盘 4 段结构**", ""]

    def _walk(node: dict[str, Any], depth: int = 0) -> None:
        indent = "  " * depth
        name = node.get("name") or "(未命名)"
        kind = node.get("type") or "folder"
        icon = "📁" if kind == "folder" else "📄"
        children = node.get("children") or []
        if depth == 0:
            lines.append(f"{icon} **{name}** ({len(children)} 段)")
        else:
            lines.append(f"{indent}{icon} {name}  ({len(children)} 项)")
        for child in children[:20]:
            _walk(child, depth + 1)
        if len(children) > 20:
            lines.append(f"{indent}  …还有 {len(children) - 20} 项")

    _walk(tree, depth=0)
    return "\n".join(lines)[:3500]


def _render_bitable_card(snapshot: dict[str, Any]) -> str:
    """Render a Bitable snapshot as a single card body."""
    tables = snapshot.get("tables") or []
    if not tables:
        return "**📋 Bitable 暂无表格**（请设置 `FEISHU_BITABLE_OPPORTUNITIES_APP_TOKEN`）"
    lines: list[str] = [f"**📋 Bitable ({len(tables)} 个表格)**", ""]
    for t in tables[:20]:
        name = t.get("name") or "(未命名)"
        tid = t.get("table_id") or ""
        rows = t.get("sample_rows")
        lines.append(f"📄 **{name}**  `table_id={tid[:12]}…`")
        if rows:
            lines.append(f"  示例行: {rows}")
    return "\n".join(lines)[:3500]


def _summarise_tree(tree: dict[str, Any]) -> dict[str, Any]:
    """Compact summary used for the in-memory ``result_summary``."""
    def _count(node: dict[str, Any]) -> int:
        children = node.get("children") or []
        return 1 + sum(_count(c) for c in children)

    return {
        "root_name": tree.get("name"),
        "total_nodes": _count(tree),
        "top_level": [
            c.get("name") for c in (tree.get("children") or [])
        ],
    }


async def _bitable_snapshot(*, bitable_manager: Any) -> dict[str, Any]:
    """Best-effort snapshot of the configured Bitable app."""
    try:
        tables = await bitable_manager.list_tables()
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        logger.warning(
            "feishu_docs_tree_bitable_snapshot_failed",
            error=str(exc)[:200],
        )
        return {"tables": [], "error": str(exc)[:200]}
    out: list[dict[str, Any]] = []
    for t in tables:
        item = {
            "table_id": t.get("table_id") or "",
            "name": t.get("name") or "(未命名)",
        }
        try:
            rows = await bitable_manager.find_records(
                table_id=item["table_id"], keyword="", limit=1
            )
            if rows:
                item["sample_rows"] = rows[0].get("record_id")
        except Exception:  # noqa: BLE001 — sample is optional
            pass
        out.append(item)
    return {"tables": out}


# ---------------------------------------------------------------------------
# Internal — the actual pipeline execution + reply posting
# ---------------------------------------------------------------------------
async def _execute_pipeline(*, record: TaskRecord, settings: Settings) -> None:
    """Run the pipeline via the internal HTTP API, post a summary reply.

    We reuse the same ``POST /api/internal/pipeline/run`` endpoint the
    n8n cron uses so we exercise the exact same code path — only the
    trigger label and the post-success IM delivery differ.
    """
    from app.services.feishu.app_client import FeishuAppClient

    base_url = (
        settings.feishu_internal_api_url
        or "http://localhost:8000"
    ).rstrip("/")
    webhook_secret = (
        settings.app_secret_key
        or settings.feishu_webhook_secret
        or ""
    )
    headers = {"Content-Type": "application/json"}
    if webhook_secret:
        headers["X-Radar-Webhook"] = webhook_secret

    pipeline_url = f"{base_url}/api/internal/pipeline/run"
    # Phase 29 fix — bot-initiated /run MUST write the daily docx to
    # Feishu Drive. The previous payload only carried ``send_digest``,
    # so the docx branch in ``internal.run_pipeline`` was SKIPPED and
    # the user's 云盘 / 每日报告 stayed empty even when the pipeline
    # itself ran cleanly (raw_count=200+, signal_count=27+). This was
    # the root cause of "为什么云盘中内容没有任何更新？" — the bot's
    # /run had no path to Drive.
    payload = {"send_digest": True, "write_docx": True}
    logger.info(
        "feishu_async_run_pipeline_request",
        task_id=record.task_id,
        pipeline_url=pipeline_url,
        write_docx=True,
        send_digest=True,
    )

    summary: dict[str, Any] = {}
    error_text: Optional[str] = None
    try:
        # Phase 29 fix — real /run (with research stage on, Browser-Use
        # polling included) takes 8-12 minutes on the live stack. The
        # previous 300s timeout meant every bot-initiated /run surfaced
        # as ``pipeline request failed:`` (empty exception) to the user,
        # even though the pipeline itself completed server-side. We
        # give 15 minutes here so the post-back carries the success
        # summary instead of the timeout.
        async with httpx.AsyncClient(timeout=900.0) as client:
            response = await client.post(
                pipeline_url, json=payload, headers=headers
            )
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = {"error": response.text[:200]}
            error_text = (
                f"pipeline HTTP {response.status_code}: "
                f"{str(detail.get('error') or detail)[:200]}"
            )
        else:
            summary = response.json() if response.content else {}
    except httpx.HTTPError as exc:
        error_text = f"pipeline request failed: {exc}"
    except Exception as exc:  # noqa: BLE001 — capture, then send a friendly reply
        error_text = f"pipeline unexpected error: {exc}"
        logger.error(
            "feishu_async_run_pipeline_error",
            task_id=record.task_id,
            error=str(exc),
            exc_info=True,
        )

    # Phase 29 fix — surface what the pipeline actually returned so
    # future "why no /run reply / why no docx" investigations have a
    # direct log to grep. Before this, the only signal was the eventual
    # ``feishu_app_message_sent`` line, which was absent if the post-back
    # failed silently.
    logger.info(
        "feishu_async_run_pipeline_response",
        task_id=record.task_id,
        http_status=getattr(response, "status_code", None),
        run_id=summary.get("run_id"),
        raw_count=summary.get("raw_count"),
        new_count=summary.get("new_count"),
        signal_count=summary.get("signal_count"),
        digest_sent=summary.get("digest_sent"),
        docx=summary.get("docx"),
        error=summary.get("error") or error_text,
    )

    record.finished_at = _now()
    if error_text is None:
        record.status = "success"
        record.result_summary = summary
    else:
        record.status = "failed"
        record.error = error_text
        record.result_summary = summary

    # — Push the summary back to the chat. Use a fresh FeishuAppClient
    # so its httpx lifetime is independent of the request scope that
    # submitted us.
    try:
        await _post_pipeline_summary(
            record=record,
            settings=settings,
            error_text=error_text,
            summary=summary,
        )
    except Exception as exc:  # noqa: BLE001 — never let the reply blow up the task
        logger.error(
            "feishu_async_run_reply_failed",
            task_id=record.task_id,
            error=str(exc),
            exc_info=True,
        )


async def _post_pipeline_summary(
    *,
    record: TaskRecord,
    settings: Settings,
    error_text: Optional[str],
    summary: dict[str, Any],
) -> None:
    """Send the pipeline result back to the originating chat."""
    from app.services.feishu.app_client import FeishuAppClient

    if error_text is not None:
        text = (
            "⚠️ 流水线运行失败。\n"
            f"task_id: {record.task_id}\n"
            f"错误: {error_text[:240]}\n"
            "请查看后端日志或重试 `/run`。"
        )
        client = FeishuAppClient(settings=settings)
        try:
            await client.send_message(
                receive_id=record.chat_id,
                receive_id_type=record.receive_id_type,
                msg_type="text",
                content={"text": text},
                compliance_context="feishu_async_run_failure",
            )
        finally:
            await client.aclose()
        return

    # — success branch — format the result summary as a card.
    run_id = summary.get("run_id", "?")
    raw_count = summary.get("raw_count", 0)
    new_count = summary.get("new_count", 0)
    signal_count = summary.get("signal_count", 0)
    digest_sent = summary.get("digest_sent", False)
    started = summary.get("started_at", "")
    finished = summary.get("finished_at", "")
    text = (
        f"✅ 流水线完成（task_id={record.task_id}, run_id={run_id}）。\n"
        f"采集: {raw_count} 条\n"
        f"新增: {new_count} 条\n"
        f"信号: {signal_count} 条\n"
        f"日报已发送: {'是' if digest_sent else '否'}\n"
        f"耗时: {started} → {finished or '运行中'}"
    )
    client = FeishuAppClient(settings=settings)
    try:
        await client.send_message(
            receive_id=record.chat_id,
            receive_id_type=record.receive_id_type,
            msg_type="interactive",
            content={
                "config": {"wide_screen_mode": True},
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": text},
                    }
                ],
                "header": {
                    "title": {"tag": "plain_text", "content": "AI 机会雷达 · /run"},
                    "template": "green" if not error_text else "red",
                },
            },
            compliance_context="feishu_async_run_success",
        )
    finally:
        await client.aclose()


__all__ = [
    "TaskRecord",
    "submit_pipeline_run",
    "get_status",
    "list_recent",
]
