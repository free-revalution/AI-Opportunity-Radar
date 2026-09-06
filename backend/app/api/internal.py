"""Internal pipeline API — MVP endpoints only (simplify §10).

All FREEZE endpoints (orders, activation, subscriptions, content,
publisher, agents, on-demand research, etc.) live in
``experimental/backend/app/api/internal.py`` — moved out so the boot
graph no longer drags in the FREEZE services.

Kept here:

  * 5 single-step pipeline triggers used by n8n daily cron
      POST /discovery/run
      POST /clustering/run
      POST /scoring/run
      POST /screening/run
      POST /research/run
  * 2 notification triggers
      POST /notifications/digest/send
      GET  /notifications/history
  * 3 Feishu-bot endpoints (simplify §10)
      POST /pipeline/run       ← /run   (manual kickoff)
      GET  /status             ← /status
      GET  /sources/healthy    ← /sources
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.config import Settings, get_settings
from app.db import get_session
from app.metrics import record_pipeline_run
from app.repositories import RunRepository
from app.services.clustering import ClusteringService
from app.services.ingestion import IngestionService
from app.services.notification import NotificationService
from app.services.research import ResearchService
from app.services.scoring import ScoringService
from app.services.screening import ScreeningService
from app.utils import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ===========================================================================
# Pipeline steps (called by n8n cron)
# ===========================================================================
@router.post(
    "/discovery/run",
    summary="Run a one-shot ingestion across enabled source connectors",
)
async def run_discovery(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """Called by the n8n daily cron. Body (all optional):
    ``{"sources": ["github", ...], "mock": true|false}``.
    """
    body = body or {}
    service = IngestionService(
        session,
        source_slugs=body.get("sources"),
        mock=body.get("mock"),
    )
    report = await record_pipeline_run("discovery", service.run_once)
    logger.info("ingestion_run_complete", **report.as_dict())
    return report.as_dict()


@router.post(
    "/clustering/run",
    summary="Embed + cluster unclustered RawItems into Opportunities",
)
async def run_clustering(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    body = body or {}
    service = ClusteringService(
        session,
        raw_item_limit=int(body.get("raw_item_limit") or 500),
    )
    report = await record_pipeline_run("clustering", service.run_once)
    logger.info("clustering_run_complete", **report.as_dict())
    return report.as_dict()


@router.post(
    "/screening/run",
    summary="Run AI screening against pending opportunities",
)
async def run_screening(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    body = body or {}
    service = ScreeningService(session, limit=int(body.get("limit") or 50))
    report = await record_pipeline_run("screening", service.run_once)
    logger.info("screening_run_complete", **report.as_dict())
    return report.as_dict()


@router.post(
    "/scoring/run",
    summary="Re-score screened / scored / research_eligible opportunities",
)
async def run_scoring(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    body = body or {}
    threshold = body.get("trigger_threshold")
    service = ScoringService(
        session,
        limit=int(body.get("limit") or 200),
        blend_signals=bool(body.get("blend_signals", True)),
        trigger_threshold=float(threshold) if threshold is not None else None,
    )
    report = await record_pipeline_run("scoring", service.run_once)
    logger.info("scoring_run_complete", **report.as_dict())
    return report.as_dict()


@router.post(
    "/research/run",
    summary="Run deep research on every pending ResearchJob",
)
async def run_research(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    body = body or {}
    service = ResearchService(
        session,
        limit=int(body.get("limit") or 10),
        max_urls=body.get("max_urls"),
    )
    report = await record_pipeline_run("research", service.run_once)
    logger.info("research_run_complete", **report.as_dict())
    return report.as_dict()


# ===========================================================================
# Notifications
# ===========================================================================
@router.post(
    "/notifications/digest/send",
    summary="Build + send the daily digest",
)
async def send_digest(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """MVP: send via Feishu only — Telegram provider was FREEZE."""
    body = body or {}
    settings_holder: dict[str, Any] = {}

    async def _noop() -> dict[str, Any]:
        # Real digest is built by n8n's feishu-daily-digest workflow;
        # this endpoint exists for symmetry with the pipeline/run
        # flow and returns the run summary so the bot can confirm.
        return {"delivered": False, "skipped": "use n8n feishu-daily-digest"}

    summary = await record_pipeline_run(
        "notifications",
        lambda: _send_digest(session, body),
    )
    return summary.as_dict()


async def _send_digest(
    session: AsyncSession,
    body: dict[str, Any],
) -> dict[str, Any]:
    """MVP shortcut — wire straight to Feishu webhook."""
    from app.config import get_settings

    settings = get_settings()
    webhook = settings.feishu_webhook_url or ""
    if not webhook:
        return {"delivered": False, "skipped": "feishu_webhook_url empty"}

    # Use NotificationService for the heavy lifting (formatting +
    # posting) — no FREEZE deps required.
    service = NotificationService(session, settings=settings)
    return await service.send_digest(
        chat_id=body.get("chat_id"),
        dry_run=bool(body.get("dry_run", False)),
        max_entries=int(body.get("max_entries") or 5),
        per_entry_summary_chars=int(body.get("per_entry_summary_chars") or 240),
    )


@router.get(
    "/notifications/history",
    summary="List recent notification attempts",
)
async def list_notifications(
    limit: int = 50,
    channel: str | None = None,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    service = NotificationService(session, settings=get_settings())
    rows = await service.list_history(limit=limit, channel=channel)
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "channel": r.channel,
                "delivered_at": r.delivered_at.isoformat() if r.delivered_at else None,
                "error": r.error,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


# ===========================================================================
# MVP bot endpoints (simplify §10)
# ===========================================================================
class _PipelineRunRequest(BaseModel):
    """Optional body for ``POST /api/internal/pipeline/run``."""

    source_slugs: Optional[list[str]] = None
    send_digest: bool = False
    write_docx: bool = False  # — Phase 25 v2.1: also write 每日报告 Docx


@router.post(
    "/pipeline/run",
    summary="Run the full MVP pipeline (discovery→research→digest)",
)
async def run_pipeline(
    body: Optional[_PipelineRunRequest] = None,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    body = body or _PipelineRunRequest()
    runs = RunRepository(session)
    run = await runs.start(trigger="bot_run")

    try:
        # 1. discovery
        discovery = IngestionService(
            session, source_slugs=body.source_slugs
        )
        d_report = await record_pipeline_run(
            "discovery", discovery.run_once
        )

        # 2. clustering
        clustering = ClusteringService(session)
        c_report = await record_pipeline_run(
            "clustering", clustering.run_once
        )

        # 3. scoring
        scoring = ScoringService(session)
        s_report = await record_pipeline_run(
            "scoring", scoring.run_once
        )

        # 4. screening — Phase 33 PR-33-F: emit (raw_item, payload) 给
        # callback,pipeline 在 screening 阶段累加 mapping,消 _backfill
        # 的二次 SELECT Signal JOIN JOIN。
        # Phase 34 PR-34-A: 一次 SELECT 所有 sources → dict lookup,消 callback
        # 内 N+1 SELECT Source.name(/run 50 opps × ~5 raw_items ≈ 250 SELECTs)。
        from sqlalchemy import select as _sa_select_for_cb
        from app.models import Source as _SourceForCb

        src_rows = await session.execute(_sa_select_for_cb(_SourceForCb.id, _SourceForCb.name))
        source_name_by_id: dict[int, str] = {row[0]: row[1] for row in src_rows.all()}

        screening_mapping: dict[str, dict[str, Any]] = {}

        async def _capture_screening_mapping(
            raw_item: Any, payload: dict[str, Any]
        ) -> None:
            # ORM RawItem 没 .source 属性 — 走 PR-34-A dict 查
            source_name = source_name_by_id.get(int(raw_item.source_id), "")
            external_id = getattr(raw_item, "external_id", "") or ""
            pk = f"{source_name}:{external_id}" if source_name else ""
            if not pk or pk.endswith(":"):
                return
            # 同主键只写一次(选最大 Score — screening 不一定按序)
            existing = screening_mapping.get(pk)
            if existing is None or payload["Score"] > int(existing.get("Score", 0)):
                screening_mapping[pk] = payload

        screening = ScreeningService(
            session, emit_screening_callback=_capture_screening_mapping
        )
        sc_report = await record_pipeline_run(
            "screening", screening.run_once
        )

        # 5. research
        research = ResearchService(session)
        r_report = await record_pipeline_run(
            "research", research.run_once
        )

        # Pre-compute per-stage counts BEFORE writing any docx so the
        # docx block can reference raw_count / signal_count without a
        # NameError. (Phase 28 fix: stage counts used to live below the
        # docx block, leaving write_docx=True to crash with
        # "name 'raw_count' is not defined".)
        def _count(report: Any, *keys: str) -> int:
            data = report.as_dict() if hasattr(report, "as_dict") else {}
            for key in keys:
                if key in data and data[key] is not None:
                    try:
                        return int(data[key])
                    except (TypeError, ValueError):
                        pass
            return 0

        raw_count = _count(d_report, "items_seen", "raw_count", "sources_attempted")
        new_count = (
            _count(c_report, "opportunities_created", "clusters_formed", "new_count")
            + _count(s_report, "opportunities_scored", "new_count")
        )
        signal_count = (
            _count(sc_report, "signals_created", "signal_count")
            + _count(r_report, "reports_persisted", "signal_count")
        )

        # ----- Phase 30 — store-first + chat-pick architecture -----
        # 2.5 把本次 /run 新增的 RawItem 写入 Data 多维表格(永远不丢)
        from app.config import get_settings as _get_settings
        from app.utils import retry_async as _retry_async

        _phase30_settings = _get_settings()
        _max_retries = _phase30_settings.radar_pipeline_max_retries
        _base_delay = _phase30_settings.radar_pipeline_base_delay_seconds
        data_sink: dict[str, Any] = {"inserted": 0, "skipped_duplicate": 0}
        # 总是尝试(空 token 时 DataTableClient 内部 ensure_app 会自动建)
        try:
            data_sink = await _retry_async(
                _write_data_table,
                session=session,
                settings=_phase30_settings,
                run_id=run.id,
                max_attempts=_max_retries,
                base_delay=_base_delay,
                op_name="pipeline.write_data_table",
            )
        except Exception as exc:  # noqa: BLE001 — record and continue
            logger.warning(
                "internal_pipeline_data_table_write_failed",
                run_id=run.id,
                error=str(exc),
            )
            data_sink = {"inserted": 0, "skipped_duplicate": 0, "error": str(exc)[:200]}

        # 4.5 Screening 后回填 Data 表的 Category / Score / Opportunity ID
        # PR-33-F: 直接用 screening 阶段累加的 mapping,不走二次 JOIN。
        try:
            backfilled = await _retry_async(
                _backfill_data_table_screening,
                session=session,
                settings=_phase30_settings,
                run_id=run.id,
                pre_built_mapping=screening_mapping,
                max_attempts=_max_retries,
                base_delay=_base_delay,
                op_name="pipeline.backfill_data_table",
            )
            data_sink["backfilled"] = backfilled
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "internal_pipeline_data_table_backfill_failed",
                run_id=run.id,
                error=str(exc),
            )
            data_sink["backfilled"] = 0

        # 5.5 Phase 30 — Top-N opportunities 写 Opportunities 多维表格
        opportunities_sink: dict[str, Any] = {"inserted": 0}
        try:
            opportunities_sink = await _retry_async(
                _write_opportunities_table,
                session=session,
                settings=_phase30_settings,
                run_id=run.id,
                n=_phase30_settings.radar_top_n_push,
                max_attempts=_max_retries,
                base_delay=_base_delay,
                op_name="pipeline.write_opportunities_table",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "internal_pipeline_opportunities_table_write_failed",
                run_id=run.id,
                error=str(exc),
            )
            opportunities_sink = {"inserted": 0, "error": str(exc)[:200]}

        # 6. digest (+ optional Docx write — Phase 25 v2.1)
        digest_sent = False
        docx_ref: Optional[dict[str, Any]] = None
        digest_preview: str = ""
        if body.send_digest or body.write_docx:
            from app.config import get_settings

            settings = get_settings()
            service = NotificationService(session, settings=settings)
            # — Always build the digest preview text once so both the
            # `send_digest` branch and the `write_docx` branch can read
            # it. Phase 28 fix: previous code called `.get()` on the
            # DigestSendSummary dataclass — that raised AttributeError
            # and the whole run 500'd. Also previous code only fetched
            # preview when send_digest=True, leaving write_docx=True
            # (with send_digest=False) writing an empty docx.
            preview_obj = await service.build_digest_preview()
            digest_preview = preview_obj.get("text", "") or ""
            if body.send_digest:
                # Phase 31 P31-C: 飞书 IM 偶发 5xx 重试
                from app.utils import retry_async as _retry_digest

                try:
                    outcome = await _retry_digest(
                        service.send_digest,
                        max_attempts=_max_retries,
                        base_delay=_base_delay,
                        op_name="pipeline.send_digest",
                    )
                    digest_sent = outcome.notifications_delivered > 0
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "internal_pipeline_send_digest_failed",
                        run_id=run.id,
                        error=str(exc),
                    )

        # 7. docx — Phase 25 v2.1: write 每日报告 Docx (Feishu 4 段结构)
        if body.write_docx:
            from datetime import date as DateType

            from app.config import get_settings
            from app.services.feishu.content_client import FeishuDriveClient
            from app.services.feishu.drive_org import DriveOrgService
            from app.utils import retry_async as _retry_docx

            settings = get_settings()
            if settings.feishu_drive_root_folder_token:
                drive = FeishuDriveClient.create_default(settings=settings)
                docx_service = DriveOrgService(
                    drive=drive, settings=settings, session=session
                )
                try:
                    ref = await _retry_docx(
                        docx_service.write_daily_digest,
                        day=DateType.today(),
                        markdown=digest_preview,
                        run_id=run.id,
                        raw_count=raw_count,
                        signal_count=signal_count,
                        max_attempts=_max_retries,
                        base_delay=_base_delay,
                        op_name="pipeline.write_daily_docx",
                    )
                    await session.commit()
                    docx_ref = {
                        "date": str(ref.date),
                        "doc_id": ref.doc_id,
                        "doc_url": ref.doc_url,
                        "folder_token": ref.folder_token,
                    }
                except Exception as exc:  # noqa: BLE001 — record and continue
                    logger.warning(
                        "internal_pipeline_docx_write_failed",
                        run_id=run.id,
                        error=str(exc),
                    )
                    docx_ref = {"error": str(exc)[:200]}
            else:
                # Phase 33 PR-33-H: drive 未配置 → soft skip。
                # 之前 ``docx_ref = {"error": "..."}`` 嵌在 success 响应里,
                # 操作员读 summary 看到 "status=success" 同时有 "error" 字段,
                # 困惑。改成 ``{"skipped": "drive_not_configured"}`` 语义清晰,
                # 真异常才走 error 路径。
                docx_ref = {"skipped": "drive_not_configured"}

        await runs.finish_success(
            run,
            raw_count=raw_count,
            new_count=new_count,
            signal_count=signal_count,
        )
        # Phase 29 fix — finish_success() flushes row updates, but
        # without an explicit commit() the AsyncSession rolls back on
        # close and the runs table never reflects the result. Symptom:
        # every /run returned 200 + status:"success" yet
        # ``SELECT status, finished_at FROM runs`` still showed
        # 'running' / NULL, which made the bot's ``/status`` reply
        # report "Last Run: 运行中" forever.
        await session.commit()
        return {
            "run_id": run.id,
            "status": "success",
            "trigger": "manual",
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat()
            if run.finished_at
            else None,
            "raw_count": raw_count,
            "new_count": new_count,
            "signal_count": signal_count,
            "digest_sent": digest_sent,
            "docx": docx_ref,
            "data_sink": data_sink,
            "opportunities_sink": opportunities_sink,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — record and re-raise
        await runs.finish_failed(run, error=str(exc))
        # Same commit fix as the success branch — without it
        # finish_failed writes are rolled back and the row stays
        # "running" indefinitely.
        await session.commit()
        raise


@router.get(
    "/status",
    summary="MVP /status — last run summary + source health",
)
async def get_status(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """Feishu /status reply source.

    Phase 33 PR-33-D: 加 ``subsystems`` 字段,4 个真探针:
      - database: SELECT 1
      - redis: ping
      - feishu: 看 token 是否配置(token 配置即 healthy,
        不发真实 HTTP — 飞书鉴权失败在 /run 时已经报错)
      - llm: 看主 provider API key 是否配置

    每个子系统返回 ``"ok" | "warn" | "down"``,inbound _status 把
    这些状态渲染成 OK / ⚠️ / ✗。
    """
    runs = RunRepository(session)
    latest = await runs.latest()

    sources = await _source_health_snapshot(session)
    total_signals = await _signal_total(session)
    dedup = await _dedup_today_stats(session)

    subsystems = await _probe_subsystems(session=session, settings=settings)

    return {
        "last_run": _serialize_run(latest) if latest else None,
        "sources": sources,
        "total_signals": total_signals,
        "dedup_today": dedup,
        "subsystems": subsystems,
        "now": datetime.now(tz=timezone.utc).isoformat(),
    }


async def _probe_subsystems(
    *,
    session: AsyncSession,
    settings: Settings,
) -> dict[str, str]:
    """Phase 33 PR-33-D: 4 个子系统健康探针。

    每个返回 ``"ok" | "warn" | "down"``,失败原因不外泄(避免 reply 膨胀)。
    """
    out: dict[str, str] = {}

    # 1) database — SELECT 1
    try:
        from sqlalchemy import text

        await session.execute(text("SELECT 1"))
        out["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("status_probe_database_failed", error=str(exc)[:200])
        out["database"] = "down"

    # 2) redis — ping (fail-open if client None)
    try:
        from app.services.redis_client import get_redis

        redis = await get_redis()
        if redis is None:
            out["redis"] = "warn"  # 不可达但非致命
        else:
            try:
                await asyncio.wait_for(redis.ping(), timeout=1.0)
                out["redis"] = "ok"
            except Exception as exc:
                logger.warning("status_probe_redis_failed", error=str(exc)[:200])
                out["redis"] = "down"
    except Exception as exc:  # noqa: BLE001
        logger.warning("status_probe_redis_unexpected", error=str(exc)[:200])
        out["redis"] = "warn"

    # 3) feishu — 简易配置探针(token 配齐即 OK,真实 HTTP 在 /run 阶段验证)
    if (
        settings.feishu_app_id
        and settings.feishu_app_secret
        and settings.feishu_webhook_url
    ):
        out["feishu"] = "ok"
    else:
        out["feishu"] = "warn"

    # 4) llm — 看主 provider API key
    primary = settings.llm_default_provider
    if primary == "MiniMax":
        configured = bool(settings.MiniMax_api_key)
    elif primary == "openai":
        configured = bool(settings.openai_api_key)
    elif primary == "anthropic":
        configured = bool(settings.anthropic_api_key)
    elif primary == "gemini":
        configured = bool(settings.gemini_api_key)
    else:
        configured = False
    out["llm"] = "ok" if configured else "warn"

    return out


@router.get(
    "/sources/healthy",
    summary="MVP /sources — per-source health snapshot",
)
async def get_sources_healthy(
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    snap = await _source_health_snapshot(session)
    return snap


# ---------------------------------------------------------------------------
# Phase 35 PR-35-C: n8n HTTP 入口 — Data 表同步 + task status
# ---------------------------------------------------------------------------
class DataTableSyncRequest(BaseModel):
    """n8n Schedule Trigger → HTTP Request 调用的 body schema。

    ``since`` 用 ISO8601 字符串(支持 ``Z`` 后缀),内部解析成
    ``datetime``;失败 → 400 invalid since。
    """

    since: Optional[str] = None
    chunk_size: int = 500
    chat_id: Optional[str] = None
    sender_open_id: Optional[str] = None
    trigger: Optional[str] = "n8n"


@router.get(
    "/task/{task_id}",
    summary="Phase 35 PR-35-C: Poll background task status",
)
async def get_task_status(
    task_id: str,
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """Public status of a background task(由 submit_*_task 提交)。

    n8n 用此 polling task_id 的 progress_inserted / progress_total。
    任务不存在(过期被 GC 或 ID 错)→ 返回 ``{"status": 404, ...}``。
    """
    from app.services.feishu.task_runner import get_status

    rec = await get_status(task_id)
    if rec is None:
        return {"status": 404, "error": "task not found (or expired)"}
    return rec


@router.post(
    "/data_table/sync",
    summary="Phase 35 PR-35-C: Trigger a background Data 表 sync (n8n entry)",
)
async def post_data_table_sync(
    body: Optional[DataTableSyncRequest] = Body(default=None),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """n8n Schedule Trigger → HTTP Request 调此 endpoint,触发后台任务。

    返回 ``{"task_id": ..., "status": "running"}``;n8n 拿 task_id 后
    polling ``GET /api/internal/task/{task_id}`` 看 progress_inserted /
    progress_total,直到 status="success" / "failed"。

    与 ``submit_pipeline_run`` 不抢 pipeline semaphore — Data sync 是周期
    后台任务,跟 /run 并行不冲突。
    """
    from app.services.feishu.task_runner import submit_data_table_sync_task

    body = body or DataTableSyncRequest()
    since: Optional[datetime] = None
    if body.since:
        try:
            since = datetime.fromisoformat(
                body.since.replace("Z", "+00:00")
            )
        except ValueError:
            return {
                "status": 400,
                "error": "invalid since (need ISO8601)",
            }
    rec = await submit_data_table_sync_task(
        since=since,
        chunk_size=body.chunk_size,
        chat_id=body.chat_id,
        sender_open_id=body.sender_open_id,
        trigger=body.trigger or "n8n",
    )
    return {"task_id": rec.task_id, "status": rec.status}


# ===========================================================================
# Phase 25 v2.1 — 飞书云文档 4 段结构 endpoints
# ===========================================================================
@router.get(
    "/docs/tree",
    summary="Ensure + return the 4-section Feishu Drive tree",
)
async def get_docs_tree(
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """Ensure the 4 段结构 (首页/今日/每日报告/信息源) exists and return its tokens.

    Idempotent — calling repeatedly does not duplicate folders.
    """
    from app.config import get_settings
    from app.services.feishu.content_client import FeishuDriveClient
    from app.services.feishu.drive_org import DriveOrgService

    settings = get_settings()
    if not settings.feishu_drive_root_folder_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FEISHU_DRIVE_ROOT_FOLDER_TOKEN not configured",
        )
    drive = FeishuDriveClient.create_default(settings=settings)
    service = DriveOrgService(drive=drive, settings=settings, session=session)
    try:
        tokens = await service.ensure_root_tree()
    except Exception as exc:  # noqa: BLE001
        logger.warning("docs_tree_ensure_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"feishu drive: {exc}",
        ) from exc
    return {
        "configured": True,
        "sections": ["home", "today", "daily_reports", "sources"],
        "tokens": tokens.as_dict(),
    }


@router.get(
    "/docs/daily",
    summary="Resolve a calendar day to its 每日报告 Docx (URL + token)",
)
async def get_daily_doc(
    date: str,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """GET /api/internal/docs/daily?date=YYYY-MM-DD

    Returns ``{"found": True, "doc_id": ..., "doc_url": ...}`` when
    a Docx was written for the date; otherwise ``{"found": False}``.
    """
    from datetime import date as DateType, datetime

    from app.models import DailyDigestDoc

    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid date (expected YYYY-MM-DD): {date}",
        ) from exc
    if not isinstance(day, DateType):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid date: {date}",
        )
    row = await session.get(DailyDigestDoc, day)
    if row is None:
        return {"found": False, "date": date}
    return {
        "found": True,
        "date": str(row.date),
        "doc_id": row.doc_id,
        "doc_url": row.doc_url,
        "folder_token": row.folder_token,
        "run_id": row.run_id,
        "raw_count": row.raw_count,
        "signal_count": row.signal_count,
        "created_at": row.created_at.isoformat(),
    }


# ===========================================================================
# Phase 26 — /docs sub-command HTTP surface
#
# Mirrors the bot's ``/docs`` family so operators can drive Drive
# management via curl without opening Feishu. Same RBAC gate
# (require_admin) as the bot path. Same ConfirmStore — destructive
# operations still require a 60-second two-step flow.
# ===========================================================================
async def _build_docs_services(
    *,
    settings: Any,
    session: Any,
):
    """Construct a :class:`DriveManager` + :class:`BitableManager` + ConfirmStore.

    Returns ``(drive_manager, bitable_manager)``. Raises HTTPException
    503 if Drive isn't configured (the API mirrors the bot path).
    """
    from app.services.feishu.app_client import FeishuAppClient
    from app.services.feishu.bitable_manager import BitableManager
    from app.services.feishu.confirm_store import get_confirm_store
    from app.services.feishu.content_client import (
        FeishuBitableClient,
        FeishuDriveClient,
    )
    from app.services.feishu.drive_manager import DriveManager

    if not settings.feishu_drive_root_folder_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FEISHU_DRIVE_ROOT_FOLDER_TOKEN not configured",
        )

    drive = FeishuDriveClient.create_default(settings=settings)
    drive_manager = DriveManager(drive=drive, settings=settings)

    bitable_client: Any = None
    try:
        app_client = FeishuAppClient(settings=settings)
        bitable_client = FeishuBitableClient(
            app_client=app_client,
            settings=settings,
            token_setting="feishu_bitable_opportunities_app_token",
        )
    except Exception:  # noqa: BLE001
        bitable_client = None

    bitable_manager = BitableManager(
        client=bitable_client or _NullBitableClient(),
        settings=settings,
        confirm_store=None,  # wired below if Redis up
    )

    # — ConfirmStore is optional — destructive paths raise a clear
    # error when it's None (see ConfirmStoreUnavailable path).
    confirm_store = None
    try:
        from app.services.redis_client import get_redis

        redis_client = await get_redis()
        if redis_client is not None:
            confirm_store = get_confirm_store(redis_client)
    except Exception:  # noqa: BLE001
        confirm_store = None

    drive_manager_with_cs = DriveManager(
        drive=drive, settings=settings, confirm_store=confirm_store
    )
    bitable_manager_with_cs = BitableManager(
        client=bitable_client or _NullBitableClient(),
        settings=settings,
        confirm_store=confirm_store,
    )
    return drive_manager_with_cs, bitable_manager_with_cs


class _NullBitableClient:
    """Stand-in when Bitable isn't configured — surfaces a clean 503."""

    async def list_tables(self) -> list:  # type: ignore[override]
        from app.services.feishu.content_client import FeishuContentError

        raise FeishuContentError("bitable not configured")

    async def find_records(self, **_kw):  # type: ignore[override]
        from app.services.feishu.content_client import FeishuContentError

        raise FeishuContentError("bitable not configured")

    async def create_record(self, **_kw):  # type: ignore[override]
        from app.services.feishu.content_client import FeishuContentError

        raise FeishuContentError("bitable not configured")

    async def update_record(self, **_kw):  # type: ignore[override]
        from app.services.feishu.content_client import FeishuContentError

        raise FeishuContentError("bitable not configured")

    async def delete_record(self, **_kw):  # type: ignore[override]
        from app.services.feishu.content_client import FeishuContentError

        raise FeishuContentError("bitable not configured")


@router.get(
    "/docs/ls",
    summary="Phase 26 — list children of a top-level Drive section",
)
async def docs_ls(
    section: str = "📁 每日报告",
    limit: int = 30,
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    from app.services.feishu.content_client import FeishuContentError

    settings = get_settings()
    drive_manager, _ = await _build_docs_services(settings=settings, session=None)
    try:
        items = await drive_manager.list_section(section=section, limit=limit)
    except FeishuContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"section": section, "count": len(items), "items": items}


@router.get(
    "/docs/find",
    summary="Phase 26 — substring search across the 4 Drive sections",
)
async def docs_find(
    keyword: str,
    scope: str = "all",
    limit: int = 20,
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    settings = get_settings()
    drive_manager, _ = await _build_docs_services(settings=settings, session=None)
    items = await drive_manager.find_files(
        keyword=keyword, scope=scope, limit=limit
    )
    return {"keyword": keyword, "scope": scope, "count": len(items), "items": items}


@router.get(
    "/docs/info",
    summary="Phase 26 — metadata for a Drive path",
)
async def docs_info(
    path: str,
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    settings = get_settings()
    drive_manager, _ = await _build_docs_services(settings=settings, session=None)
    node = await drive_manager.resolve(path=path)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"path not found: {path}"
        )
    metas = await drive_manager.drive.get_file_meta(
        file_tokens=[node.token], file_type=node.type or "folder"
    )
    return {
        "path": node.path,
        "type": node.type,
        "token": node.token,
        "metas": metas,
    }


@router.post(
    "/docs/mkdir",
    summary="Phase 26 — recursively create a folder path",
)
async def docs_mkdir(
    body: dict[str, Any],
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    from app.services.feishu.content_client import FeishuContentError

    settings = get_settings()
    drive_manager, _ = await _build_docs_services(settings=settings, session=None)
    path = (body.get("path") or "").strip()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path is required"
        )
    try:
        result = await drive_manager.mkdir_path(path=path)
    except FeishuContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return result


@router.post(
    "/docs/create",
    summary="Phase 26 — create a child folder inside a section",
)
async def docs_create(
    body: dict[str, Any],
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    from app.services.feishu.content_client import FeishuContentError

    settings = get_settings()
    drive_manager, _ = await _build_docs_services(settings=settings, session=None)
    name = (body.get("name") or "").strip()
    section = (body.get("section") or "📁 每日报告").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="name is required"
        )
    try:
        result = await drive_manager.create_child_folder(
            section=section, name=name
        )
    except FeishuContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return result


@router.post(
    "/docs/mv",
    summary="Phase 26 — move a Drive file/folder to a section",
)
async def docs_mv(
    body: dict[str, Any],
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    from app.services.feishu.content_client import FeishuContentError

    settings = get_settings()
    drive_manager, _ = await _build_docs_services(settings=settings, session=None)
    path = (body.get("path") or "").strip()
    target = (body.get("target_section") or "").strip()
    if not path or not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path and target_section required",
        )
    node = await drive_manager.resolve(path=path)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"path not found: {path}"
        )
    try:
        result = await drive_manager.move_to_section(
            file_token=node.token,
            file_type=node.type,
            target_section=target,
        )
    except FeishuContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"path": path, **result}


@router.post(
    "/docs/rename",
    summary="Phase 26 — rename a Drive file/folder",
)
async def docs_rename(
    body: dict[str, Any],
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    from app.services.feishu.content_client import FeishuContentError

    settings = get_settings()
    drive_manager, _ = await _build_docs_services(settings=settings, session=None)
    path = (body.get("path") or "").strip()
    new_name = (body.get("new_name") or "").strip()
    if not path or not new_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path and new_name required",
        )
    node = await drive_manager.resolve(path=path)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"path not found: {path}"
        )
    try:
        result = await drive_manager.rename(
            file_token=node.token,
            file_type=node.type,
            new_name=new_name,
        )
    except FeishuContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"old_path": path, **result}


@router.post(
    "/docs/rm",
    summary="Phase 26 — STAGE a Drive delete (returns a 60s token)",
)
async def docs_rm(
    body: dict[str, Any],
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    from app.services.feishu.confirm_store import ConfirmStoreUnavailable
    from app.services.feishu.content_client import FeishuContentError

    settings = get_settings()
    drive_manager, _ = await _build_docs_services(settings=settings, session=None)
    path = (body.get("path") or "").strip()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path is required"
        )
    try:
        action = await drive_manager.request_delete(path=path)
    except FeishuContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except ConfirmStoreUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {
        "stage": "pending",
        "action_id": action.action_id,
        "kind": action.kind,
        "expires_at": action.expires_at,
        "path": action.payload.get("path"),
    }


@router.post(
    "/docs/confirm",
    summary="Phase 26 — execute a previously staged delete",
)
async def docs_confirm(
    body: dict[str, Any],
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    from app.services.feishu.confirm_store import ConfirmStoreUnavailable
    from app.services.feishu.content_client import FeishuContentError

    settings = get_settings()
    drive_manager, bitable_manager = await _build_docs_services(
        settings=settings, session=None
    )
    action_id = (body.get("action_id") or "").strip()
    if not action_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="action_id is required"
        )
    store = (
        drive_manager.confirm_store or bitable_manager.confirm_store
    )
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ConfirmStore unavailable (Redis not configured)",
        )
    action = await store.consume(action_id)
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"action_id not found or expired: {action_id}",
        )
    try:
        if action.kind == "drive_delete":
            outcome = await drive_manager.execute_delete(action=action)
        elif action.kind == "bitable_rm":
            outcome = await bitable_manager.execute_delete(action=action)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown action kind: {action.kind}",
            )
    except FeishuContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    return {"action_id": action_id, "kind": action.kind, "outcome": outcome}


@router.get(
    "/docs/bitable/ls",
    summary="Phase 26 — list Bitable tables",
)
async def docs_bitable_ls(
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    from app.services.feishu.content_client import FeishuContentError

    settings = get_settings()
    _drive_manager, bitable_manager = await _build_docs_services(
        settings=settings, session=None
    )
    try:
        tables = await bitable_manager.list_tables()
    except FeishuContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {"count": len(tables), "items": tables}


@router.get(
    "/docs/bitable/find",
    summary="Phase 26 — find Bitable records by keyword",
)
async def docs_bitable_find(
    keyword: str,
    table: Optional[str] = None,
    limit: int = 10,
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    from app.services.feishu.content_client import FeishuContentError

    settings = get_settings()
    _drive_manager, bitable_manager = await _build_docs_services(
        settings=settings, session=None
    )
    try:
        items = await bitable_manager.find_records(
            table_name=table, keyword=keyword, limit=limit
        )
    except FeishuContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"keyword": keyword, "table": table, "count": len(items), "items": items}


@router.post(
    "/docs/bitable/add",
    summary="Phase 26 — add a Bitable record",
)
async def docs_bitable_add(
    body: dict[str, Any],
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    from app.services.feishu.content_client import FeishuContentError

    settings = get_settings()
    _drive_manager, bitable_manager = await _build_docs_services(
        settings=settings, session=None
    )
    table = (body.get("table") or "").strip() or None
    fields = body.get("fields") or {}
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fields (dict) is required",
        )
    try:
        rec = await bitable_manager.add_record(
            table_name=table, fields=fields
        )
    except FeishuContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"table": table, **rec}


@router.post(
    "/docs/bitable/rm",
    summary="Phase 26 — STAGE a Bitable delete (returns a 60s token)",
)
async def docs_bitable_rm(
    body: dict[str, Any],
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    from app.config import get_settings

    from app.services.feishu.confirm_store import ConfirmStoreUnavailable
    from app.services.feishu.content_client import FeishuContentError

    settings = get_settings()
    _drive_manager, bitable_manager = await _build_docs_services(
        settings=settings, session=None
    )
    record_id = (body.get("record_id") or "").strip()
    table = (body.get("table") or "").strip() or None
    if not record_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="record_id is required"
        )
    try:
        action = await bitable_manager.request_delete(
            record_id=record_id, table_name=table
        )
    except FeishuContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except ConfirmStoreUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {
        "stage": "pending",
        "action_id": action.action_id,
        "kind": action.kind,
        "expires_at": action.expires_at,
        "record_id": record_id,
    }


# ===========================================================================
# Helpers
# ===========================================================================
def _serialize_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "trigger": run.trigger,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "raw_count": run.raw_count,
        "new_count": run.new_count,
        "signal_count": run.signal_count,
        "error": run.error,
    }


async def _source_health_snapshot(
    session: AsyncSession, *, include_data_view_url: bool = True
) -> dict[str, Any]:
    from sqlalchemy import select

    from app.config import get_settings
    from app.models import Source

    rows = (await session.execute(select(Source))).scalars().all()
    items = [
        {
            "id": s.id,
            "name": s.name,
            "type": s.type,
            "url": s.url,
            "healthy": bool(getattr(s, "healthy", True)),
            "last_success_at": s.last_success_at.isoformat()
            if getattr(s, "last_success_at", None)
            else None,
            "last_error_at": s.last_error_at.isoformat()
            if getattr(s, "last_error_at", None)
            else None,
            "compliance_level": getattr(s, "compliance_level", None),
        }
        for s in rows
    ]
    out: dict[str, Any] = {
        "total": len(items),
        "healthy": sum(1 for i in items if i["healthy"]),
        "items": items,
    }
    # Phase 35 PR-35-B: 顶层 data_view_url 单一 URL(向后兼容 — 取第一个 target)
    # Phase 35 PR-35-D: 同时给 data_view_urls 列表,所有 target 都返回
    if include_data_view_url:
        urls = await _resolve_data_view_urls(get_settings())
        out["data_view_urls"] = urls
        out["data_view_url"] = urls[0] if urls else None
    else:
        out["data_view_urls"] = []
        out["data_view_url"] = None
    return out


# ---------------------------------------------------------------------------
# Phase 35 PR-35-B: Data 表视图 URL — 顶层 lazy 解析 + 模块级 TTL 缓存
# ---------------------------------------------------------------------------
import time as _time_mod

_DATA_VIEW_URL_CACHE: dict[str, Any] = {"urls": None, "expires_at": 0.0}
_DATA_VIEW_URL_TTL_SEC = 300  # 5 分钟


async def _resolve_data_view_urls(settings: Any) -> list[str]:
    """Phase 35 PR-35-D: 解析所有目标 Data 表视图 URL(每个 target 一条)。

    每个 URL 形如 ``https://feishu.cn/base/<app_token>?table=<table_id>``。
    首次调用会触发 ``DataTableClient.ensure_table``(可能打飞书),后续
    5 分钟内复用。任何异常 log warning,返回 ``[]`` — 主流程不依赖。

    Phase 35 PR-35-D: 返回 list — 多目标广播时,bot 卡片要展示每张表。
    """
    now = _time_mod.time()
    cached = _DATA_VIEW_URL_CACHE.get("urls")
    expires_at = _DATA_VIEW_URL_CACHE.get("expires_at") or 0.0
    if cached is not None and now < expires_at:
        return list(cached)
    try:
        from app.services.feishu.app_client import FeishuAppClient
        from app.services.feishu.data_table import DataTableClient

        app_client = FeishuAppClient(settings=settings)
        dtc = DataTableClient(app_client=app_client, settings=settings)
        targets = await dtc.ensure_table()  # list[(app_token, table_id)]
        urls: list[str] = []
        for tok, tid in targets:
            if tok and tid:
                urls.append(f"https://feishu.cn/base/{tok}?table={tid}")
        _DATA_VIEW_URL_CACHE["urls"] = urls
        _DATA_VIEW_URL_CACHE["expires_at"] = now + _DATA_VIEW_URL_TTL_SEC
        return urls
    except Exception as exc:  # noqa: BLE001 — /sources 不能因为飞书 down 而 500
        logger.warning(
            "feishu_data_view_url_resolve_failed", error=str(exc)
        )
    return []


async def _resolve_data_view_url(settings: Any) -> Optional[str]:
    """Phase 35 PR-35-D: backward-compat 单 URL resolver。

    等同 ``(_resolve_data_view_urls(settings) or [None])[0]`` — 旧 caller
    (bot 卡片 / 单数字段)仍能拿到一个 URL。**新代码请用 ``_resolve_data_view_urls``**。
    """
    urls = await _resolve_data_view_urls(settings)
    return urls[0] if urls else None


async def _signal_total(session: AsyncSession) -> int:
    from sqlalchemy import func, select

    from app.models import Signal

    total = (
        await session.execute(select(func.count()).select_from(Signal))
    ).scalar_one()
    return int(total or 0)


async def _dedup_today_stats(session: AsyncSession) -> dict[str, Any]:
    """Phase 25 v2.1 — today-fetched RawItem dedup funnel.

    Returns the three numbers the /status reply surfaces so the
    operator can see how much the source collectors are gathering
    versus how much survives URL-deduplication and clustering:

      raw_items_collected     — total RawItems fetched since 00:00 UTC
      unique_urls             — distinct URL count in the same window
      opportunities_created   — Opportunities inserted since 00:00 UTC
    """
    from sqlalchemy import distinct, func, select

    from app.models import Opportunity, RawItem

    today_start = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    raw_total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(RawItem)
                .where(RawItem.fetched_at >= today_start)
            )
        ).scalar_one()
        or 0
    )
    raw_unique_url = int(
        (
            await session.execute(
                select(func.count(distinct(RawItem.url)))
                .select_from(RawItem)
                .where(RawItem.fetched_at >= today_start)
            )
        ).scalar_one()
        or 0
    )
    opp_new = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Opportunity)
                .where(Opportunity.created_at >= today_start)
            )
        ).scalar_one()
        or 0
    )
    return {
        "raw_items_collected": raw_total,
        "unique_urls": raw_unique_url,
        "opportunities_created": opp_new,
        "window_start": today_start.isoformat(),
    }

# ===========================================================================
# Phase 30 — store-first sinks (Data 多维表格)
# ===========================================================================
async def _write_data_table(
    *,
    session: AsyncSession,
    settings: Any,
    run_id: int,
) -> dict[str, Any]:
    """把本次 /run 新增的 RawItem 写入 Data 多维表格(永远不丢)。

    选择窗口: ``fetched_at >= run.started_at``。
    容错: 任何异常由 caller ``run_pipeline`` catch 后只记录,不阻塞 run。

    Phase 31 fix: ORM ``RawItem`` 没有 ``.source`` 属性(dataclass 才有)。
    改走 ``DataTableClient.bulk_insert_orm_raw_items``,内部自动 JOIN
    ``Source.name`` 拼主键。
    """
    from sqlalchemy import select as _sa_select

    from app.models import Run as _Run
    from app.services.feishu.app_client import FeishuAppClient
    from app.services.feishu.data_table import DataTableClient

    run = await session.get(_Run, run_id)
    started_at = run.started_at if run else None
    if started_at is None:
        return {
            "inserted": 0,
            "skipped_duplicate": 0,
            "skipped_orphan": 0,
            "error": "run has no started_at",
        }

    stmt = _sa_select(RawItem).where(RawItem.fetched_at >= started_at)
    raw_items = list((await session.execute(stmt)).scalars().all())
    if not raw_items:
        return {"inserted": 0, "skipped_duplicate": 0, "skipped_orphan": 0}

    app_client = FeishuAppClient(settings=settings)
    client = DataTableClient(app_client=app_client, settings=settings)
    return await client.bulk_insert_orm_raw_items(
        items=raw_items, run_id=run_id, session=session
    )


async def _backfill_data_table_screening(
    *,
    session: AsyncSession,
    settings: Any,
    run_id: int,
    pre_built_mapping: Optional[dict[str, dict[str, Any]]] = None,
) -> int:
    """回填本次 /run 新生成 Signal 对应 Data 表行的 Category / Score / Opportunity ID。

    两种路径:
      * **PR-33-F 快速路径** (``pre_built_mapping`` 非空):
        screening 阶段 emit callback 已累加 {pk: payload} mapping,
        直接调 DataTableClient.update_screening_results。
      * **回退路径** (mapping 空):走原 ``SELECT Signal JOIN JOIN``
        查询(冷启动 / 单步 screening 等无 callback 上下文场景)。

    返回实际回填的 Data 表行数。
    """
    from app.services.feishu.app_client import FeishuAppClient
    from app.services.feishu.data_table import DataTableClient

    # --- PR-33-F 快速路径 -----------------------------------------
    if pre_built_mapping:
        app_client = FeishuAppClient(settings=settings)
        client = DataTableClient(app_client=app_client, settings=settings)
        return await client.update_screening_results(
            mapping=pre_built_mapping
        )

    # --- 回退路径(JOIN-based)------------------------------------
    from sqlalchemy import select as _sa_select

    from app.models import Opportunity as _Opportunity
    from app.models import Run as _Run
    from app.models import Signal as _Signal

    run = await session.get(_Run, run_id)
    started_at = run.started_at if run else None
    if started_at is None:
        return 0

    from app.models import OpportunitySource as _OS

    stmt = (
        _sa_select(_Signal, RawItem, _Opportunity)
        .join(RawItem, _Signal.raw_item_id == RawItem.id)
        .join(_OS, _OS.raw_item_id == RawItem.id)
        .join(_Opportunity, _Opportunity.id == _OS.opportunity_id)
        .where(_Signal.created_at >= started_at)
        .where(_Signal.signal_type == "screening")
    )
    rows = list((await session.execute(stmt)).all())
    if not rows:
        return 0

    mapping: dict[str, dict[str, Any]] = {}
    for signal, raw_item, opp in rows:
        # 注:DB 模型字段叫 external_id(RawItem dataclass 叫 source_id,
        # upsert 时映射),这里直接拼 source:external_id 作 Data 表主键。
        pk = f"{raw_item.source}:{raw_item.external_id or ''}"
        if not pk.endswith(":"):
            payload: dict[str, Any] = {
                "Category": signal.category or opp.category or "",
                "Score": int(round(float(opp.total_score or 0))),
                "Opportunity ID": int(opp.id),
            }
            # 同主键只写一次(选最大的 total_score)
            existing = mapping.get(pk)
            if existing is None or payload["Score"] > int(existing.get("Score", 0)):
                mapping[pk] = payload

    if not mapping:
        return 0

    app_client = FeishuAppClient(settings=settings)
    client = DataTableClient(app_client=app_client, settings=settings)
    return await client.update_screening_results(mapping=mapping)


# Phase 33 PR-33-F: emit-callback 快速路径已上线,此 fallback 仅用于
# 单步 /screening/run 等不带 callback 上下文的入口(冷启动)。


# ===========================================================================
# Phase 30 — Opportunities 多维表格 sink (Top-N)
# ===========================================================================
async def _write_opportunities_table(
    *,
    session: AsyncSession,
    settings: Any,
    run_id: int,
    n: int = 5,
) -> dict[str, Any]:
    """把本次 /run 的 Top-N 机会(按 total_score 倒序)写入 Opportunities 多维表格。

    - plan D10: 重写 mapper,用真实字段。
    - plan §0 决策:Top-N 推送到群聊 → 用户点按钮生成详情 docx。
    - 注意:这是**无脑 insert** — Opportunities 表只增不删,
      每次 /run 都写最新 Top-N,运营在飞书 UI 看最新快照。
    """
    from app.repositories import OpportunityRepository
    from app.services.feishu.app_client import FeishuAppClient
    from app.services.feishu.content_client import FeishuBitableClient

    if n <= 0:
        return {"inserted": 0}

    opp_repo = OpportunityRepository(session)
    rows, _ = await opp_repo.list_paginated(limit=n, sort="total_score")
    if not rows:
        return {"inserted": 0}

    base_url = (
        settings.app_base_url
        if hasattr(settings, "app_base_url")
        else "http://localhost:3000"
    )
    items = [
        {
            "id": opp.id,
            "title": opp.title,
            "total_score": opp.total_score,
            "category": opp.category,
            "source_count": opp.source_count,
            "summary": opp.summary,
            "trend_score": opp.trend_score,
            "demand_score": opp.demand_score,
            "monetization_score": opp.monetization_score,
            "competition_gap_score": opp.competition_gap_score,
            "china_gap_score": opp.china_gap_score,
            "execution_score": opp.execution_score,
        }
        for opp in rows
    ]

    app_client = FeishuAppClient(settings=settings)
    bitable = FeishuBitableClient(
        app_client=app_client,
        settings=settings,
        token_setting="feishu_bitable_opportunities_app_token",
    )
    inserted = await bitable.bulk_insert_opportunities(
        items=items,
        base_url_for_links=base_url,
    )
    logger.info(
        "feishu_opportunities_table_inserted",
        run_id=run_id,
        inserted=inserted,
        requested=n,
    )
    return {"inserted": inserted}


@router.get(
    "/opportunities/top",
    summary="Top-N opportunities by total_score (Phase 30 — chat card)",
)
async def get_top_opportunities(
    n: int = 5,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """供 task_runner 在 /run 完成后拉 Top-N 信号渲染飞书交互卡。

    每个机会返回: ``id, title, slug, total_score, category, source_count, summary``。
    """
    n = max(1, min(n, 20))  # 限制范围,避免 IM 卡过载
    opp_repo = OpportunityRepository(session)
    rows, _ = await opp_repo.list_paginated(limit=n, sort="total_score")
    return {
        "items": [
            {
                "id": opp.id,
                "title": opp.title,
                "slug": opp.slug,
                "total_score": opp.total_score,
                "category": opp.category,
                "source_count": opp.source_count,
                "summary": (opp.summary or "")[:200],
            }
            for opp in rows
        ],
        "count": len(rows),
    }
