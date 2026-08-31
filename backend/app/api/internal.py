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

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
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

        # 4. screening
        screening = ScreeningService(session)
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
                outcome = await service.send_digest()
                digest_sent = outcome.notifications_delivered > 0

        # 7. docx — Phase 25 v2.1: write 每日报告 Docx (Feishu 4 段结构)
        if body.write_docx:
            from datetime import date as DateType

            from app.config import get_settings
            from app.services.feishu.content_client import FeishuDriveClient
            from app.services.feishu.drive_org import DriveOrgService

            settings = get_settings()
            if settings.feishu_drive_root_folder_token:
                drive = FeishuDriveClient.create_default(settings=settings)
                docx_service = DriveOrgService(
                    drive=drive, settings=settings, session=session
                )
                try:
                    ref = await docx_service.write_daily_digest(
                        day=DateType.today(),
                        markdown=digest_preview,
                        run_id=run.id,
                        raw_count=raw_count,
                        signal_count=signal_count,
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
                docx_ref = {
                    "error": "FEISHU_DRIVE_ROOT_FOLDER_TOKEN not configured"
                }

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
    _actor: str = Depends(require_admin),
) -> dict[str, Any]:
    """Feishu /status reply source."""
    runs = RunRepository(session)
    latest = await runs.latest()

    sources = await _source_health_snapshot(session)
    total_signals = await _signal_total(session)
    dedup = await _dedup_today_stats(session)

    return {
        "last_run": _serialize_run(latest) if latest else None,
        "sources": sources,
        "total_signals": total_signals,
        "dedup_today": dedup,
        "now": datetime.now(tz=timezone.utc).isoformat(),
    }


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
    section: str = "📅 今日",
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
    section = (body.get("section") or "📅 今日").strip()
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


async def _source_health_snapshot(session: AsyncSession) -> dict[str, Any]:
    from sqlalchemy import select

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
    return {
        "total": len(items),
        "healthy": sum(1 for i in items if i["healthy"]),
        "items": items,
    }


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