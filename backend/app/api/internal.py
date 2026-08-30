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

        # 6. digest (+ optional Docx write — Phase 25 v2.1)
        digest_sent = False
        docx_ref: Optional[dict[str, Any]] = None
        if body.send_digest:
            from app.config import get_settings

            service = NotificationService(session, settings=get_settings())
            outcome = await service.send_digest()
            digest_sent = bool(outcome.get("delivered"))

        # 7. docx — Phase 25 v2.1: write 每日报告 Docx (Feishu 4 段结构)
        if body.write_docx:
            from datetime import date as DateType

            from app.config import get_settings
            from app.services.feishu.content_client import FeishuDriveClient
            from app.services.feishu.drive_org import DriveOrgService

            settings = get_settings()
            if settings.feishu_drive_root_folder_token:
                drive = FeishuDriveClient(settings=settings)
                docx_service = DriveOrgService(
                    drive=drive, settings=settings, session=session
                )
                try:
                    ref = await docx_service.write_daily_digest(
                        day=DateType.today(),
                        markdown=outcome.get("preview", "")
                        if isinstance(outcome, dict)
                        else "",
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

        # Extract per-stage counts from each report's ``as_dict()``
        # (different services use different field names).
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

        await runs.finish_success(
            run,
            raw_count=raw_count,
            new_count=new_count,
            signal_count=signal_count,
        )
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
    drive = FeishuDriveClient(settings=settings)
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