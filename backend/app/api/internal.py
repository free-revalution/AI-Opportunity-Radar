"""Internal endpoints used by n8n / cron workers.

These routes are NOT mounted under the public `/api` tree — they live
under `/api/internal` and (in production) are protected by a shared
secret header (`X-Radar-Webhook`).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.metrics import record_pipeline_run
from app.services.clustering import ClusteringService
from app.services.content_generator import ContentGeneratorService
from app.services.ingestion import IngestionService
from app.services.llm import build_llm_provider
from app.services.notification import NotificationService
from app.services.research import ResearchService
from app.services.scoring import ScoringService
from app.services.screening import ScreeningService
from app.utils import get_logger

router = APIRouter()
logger = get_logger(__name__)


def _check_webhook_secret(
    provided: str | None = Header(default=None, alias="X-Radar-Webhook"),
) -> None:
    settings = get_settings()
    expected = (
        settings.app_secret_key  # use the app secret as a stand-in
        or os.environ.get("RADAR_WEBHOOK_SECRET", "")
    )
    if not expected:
        return  # dev / local — accept all
    if not provided:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing webhook secret header",
        )
    if not hmac.compare_digest(
        hashlib.sha256(provided.encode()).hexdigest(),
        hashlib.sha256(expected.encode()).hexdigest(),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook secret"
        )


@router.post(
    "/discovery/run",
    summary="Run a one-shot ingestion across enabled source connectors",
)
async def run_discovery(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Called by the n8n daily cron (and the worker in Phase 4).

    Body:
        {
          "sources": ["github", "reddit", ...]   # optional, defaults to settings.enabled_sources
          "mock": true | false                    # optional override
        }
    """
    body = body or {}
    sources = body.get("sources")
    mock = body.get("mock")

    service = IngestionService(session, source_slugs=sources, mock=mock)
    report = await record_pipeline_run("discovery", service.run_once)

    logger.info(
        "ingestion_run_complete",
        **report.as_dict(),
    )
    return report.as_dict()


@router.post(
    "/digest/build",
    summary="Build a daily digest payload (no Telegram push yet)",
)
async def build_digest(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 3 stub. Phase 8 wires this to the Telegram sender."""
    from app.repositories import OpportunityRepository

    repo = OpportunityRepository(session)
    rows, total = await repo.list_paginated(limit=5, offset=0)
    return {
        "generated_at": None,
        "top_opportunities": [
            {
                "id": r.id,
                "title": r.title,
                "score": r.total_score,
            }
            for r in rows
        ],
        "total_opportunities": total,
    }


@router.post(
    "/clustering/run",
    summary="Embed + cluster unclustered RawItems into Opportunities",
)
async def run_clustering(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 4 endpoint — called by n8n after `discovery/run`.

    Body (all optional):
        {
          "raw_item_limit": 500,    # cap items per pass
          "threshold": 0.82          # cosine threshold override
        }
    """
    body = body or {}
    raw_item_limit = int(body.get("raw_item_limit") or 500)
    threshold_override = body.get("threshold")

    service = ClusteringService(
        session,
        raw_item_limit=raw_item_limit,
    )
    if threshold_override is not None:
        try:
            from app.services.clustering import Clusterer

            service.clusterer = Clusterer(threshold=float(threshold_override))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid threshold: {exc}",
            ) from exc

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
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 5 endpoint — called by n8n after `clustering/run`.

    Body (all optional):
        {
          "limit": 50,            # max opportunities per pass
          "use_mock": true | false
        }
    """
    body = body or {}
    limit = int(body.get("limit") or 50)
    use_mock = bool(body.get("use_mock"))

    service = ScreeningService(session, limit=limit)
    if use_mock:
        from app.services.llm import MockLLMProvider

        service.provider = MockLLMProvider()

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
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 6 endpoint — called by n8n after `screening/run`.

    Body (all optional):
        {
          "limit": 200,             # max opportunities per pass
          "trigger_threshold": 70,  # cosine threshold override
          "blend_signals": true
        }
    """
    body = body or {}
    limit = int(body.get("limit") or 200)
    threshold = body.get("trigger_threshold")
    blend_signals = bool(body.get("blend_signals", True))

    service = ScoringService(
        session,
        limit=limit,
        blend_signals=blend_signals,
        trigger_threshold=float(threshold) if threshold is not None else None,
    )
    report = await record_pipeline_run("scoring", service.run_once)
    logger.info("scoring_run_complete", **report.as_dict())
    return report.as_dict()


@router.post(
    "/scoring/score/{opportunity_id}",
    summary="Re-score a single opportunity explicitly",
)
async def score_one(
    opportunity_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 6 endpoint — explicit single-opportunity scoring.

    Body (all optional):
        {"blend_signals": true}
    """
    body = body or {}
    blend_signals = bool(body.get("blend_signals", True))
    service = ScoringService(session, blend_signals=blend_signals)
    try:
        outcome = await service.score_one(opportunity_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return {
        "opportunity_id": outcome.opportunity_id,
        "total_score": outcome.total_score,
        "recommendation": outcome.recommendation,
        "status": outcome.status,
        "research_job_id": outcome.research_job_id,
        "changed": outcome.changed,
    }


# ---------------------------------------------------------------------------
# Phase 7 — Deep Research
# ---------------------------------------------------------------------------
@router.post(
    "/research/run",
    summary="Run deep research on every pending ResearchJob",
)
async def run_research(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 7 endpoint — called by n8n after `scoring/run`.

    Body (all optional):
        {
          "limit": 10,             # max jobs per pass
          "max_urls": 20,          # cap on URLs scraped per job
          "use_mock_web": true,    # force MockWebDataProvider
          "use_mock_llm": true     # force MockResearchLLMProvider
        }
    """
    body = body or {}
    limit = int(body.get("limit") or 10)
    max_urls = body.get("max_urls")
    use_mock_web = bool(body.get("use_mock_web"))
    use_mock_llm = bool(body.get("use_mock_llm"))
    web_prefer = body.get("web_prefer")

    service = ResearchService(
        session,
        limit=limit,
        max_urls=int(max_urls) if max_urls is not None else None,
    )
    if use_mock_web:
        from app.services.research.mock_web_data import MockWebDataProvider

        service.web = MockWebDataProvider()
    elif web_prefer:
        from app.services.research.web_data import build_web_data_provider

        service.web = build_web_data_provider(
            get_settings(), prefer=str(web_prefer)
        )
    if use_mock_llm:
        from app.services.research.mock_llm import MockResearchLLMProvider

        service.llm = MockResearchLLMProvider()

    report = await record_pipeline_run("research", service.run_once)
    logger.info("research_run_complete", **report.as_dict())
    return report.as_dict()


@router.post(
    "/research/run/{job_id}",
    summary="Run deep research for one specific ResearchJob",
)
async def run_one_research_job(
    job_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 7 endpoint — explicit single-job research trigger.

    Body (all optional):
        {
          "use_mock_web": true,
          "use_mock_llm": true
        }
    """
    body = body or {}
    use_mock_web = bool(body.get("use_mock_web"))
    use_mock_llm = bool(body.get("use_mock_llm"))
    web_prefer = body.get("web_prefer")

    service = ResearchService(session)
    if web_prefer and not use_mock_web:
        from app.services.research.web_data import build_web_data_provider

        service.web = build_web_data_provider(
            get_settings(), prefer=str(web_prefer)
        )
    if use_mock_llm:
        from app.services.research.mock_llm import MockResearchLLMProvider

        service.llm = MockResearchLLMProvider()

    try:
        outcome = await service.process_job(job_id, use_mock_web=use_mock_web)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return {
        "job_id": outcome.job_id,
        "opportunity_id": outcome.opportunity_id,
        "status": outcome.status,
        "recommendation": outcome.recommendation,
        "confidence": outcome.confidence,
        "sources_count": outcome.sources_count,
        "warnings": outcome.warnings,
        "error": outcome.error,
    }


@router.post(
    "/research/cancel/{job_id}",
    summary="Cancel a pending or running ResearchJob",
)
async def cancel_research_job(
    job_id: int,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 7 endpoint — marks a non-terminal job as `cancelled`.

    Returns `{"cancelled": true|false}`. Idempotent — re-cancelling a
    completed/failed job returns `false` instead of erroring.
    """
    service = ResearchService(session)
    cancelled = await service.cancel(job_id)
    return {"job_id": job_id, "cancelled": cancelled}


# ---------------------------------------------------------------------------
# Phase 8 — Telegram Notifications
# ---------------------------------------------------------------------------
def _build_notification_service(session: AsyncSession) -> NotificationService:
    """Inject the mock provider by default — tests + local dev.

    Production callers should set `TELEGRAM_BOT_TOKEN` /
    `TELEGRAM_CHAT_ID` and `MOCK_EXTERNAL_SERVICES=false` so the real
    httpx provider is selected by the factory.
    """
    settings = get_settings()
    if getattr(settings, "mock_external_services", False):
        from app.services.notification.mock_telegram import MockTelegramProvider

        return NotificationService(
            session, settings=settings, provider=MockTelegramProvider()
        )
    return NotificationService(session, settings=settings)


@router.post(
    "/notifications/digest/preview",
    summary="Preview the daily digest without sending",
)
async def preview_digest(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 8 endpoint — returns the MarkdownV2 text + a warnings list.

    Body (all optional):
        {
          "max_entries": 5,
          "per_entry_summary_chars": 240,
          "min_score": 70.0
        }
    """
    body = body or {}
    max_entries = int(body.get("max_entries") or 5)
    per_entry_summary_chars = int(body.get("per_entry_summary_chars") or 240)
    min_score = body.get("min_score")
    try:
        min_score_f = float(min_score) if min_score is not None else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid min_score: {exc}",
        ) from exc

    service = _build_notification_service(session)
    return await service.build_digest_preview(
        max_entries=max_entries,
        per_entry_summary_chars=per_entry_summary_chars,
        min_score=min_score_f,
    )


@router.post(
    "/notifications/digest/send",
    summary="Build + send the daily digest",
)
async def send_digest(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 8 endpoint — sends the digest to the configured chat.

    Body (all optional):
        {
          "chat_id": "12345",
          "dry_run": false,
          "max_entries": 5,
          "per_entry_summary_chars": 240,
          "min_score": 70.0
        }
    """
    body = body or {}
    chat_id = body.get("chat_id")
    dry_run = bool(body.get("dry_run", False))
    max_entries = int(body.get("max_entries") or 5)
    per_entry_summary_chars = int(body.get("per_entry_summary_chars") or 240)
    min_score = body.get("min_score")
    try:
        min_score_f = float(min_score) if min_score is not None else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid min_score: {exc}",
        ) from exc

    service = _build_notification_service(session)
    summary = await record_pipeline_run(
        "notifications",
        lambda: service.send_digest(
            chat_id=chat_id,
            dry_run=dry_run,
            max_entries=max_entries,
            per_entry_summary_chars=per_entry_summary_chars,
            min_score=min_score_f,
        ),
    )
    logger.info("notifications_digest_dispatched", **summary.as_dict())
    return summary.as_dict()


@router.post(
    "/notifications/opportunity/{opportunity_id}/preview",
    summary="Preview a single-opportunity alert without sending",
)
async def preview_opportunity_alert(
    opportunity_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 8 endpoint — returns the MarkdownV2 text for one alert."""
    body = body or {}
    max_summary_chars = int(body.get("max_summary_chars") or 600)
    service = _build_notification_service(session)
    try:
        return await service.build_opportunity_preview(
            opportunity_id, max_summary_chars=max_summary_chars
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.post(
    "/notifications/opportunity/{opportunity_id}/send",
    summary="Send a single-opportunity alert to the configured chat",
)
async def send_opportunity_alert(
    opportunity_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 8 endpoint — sends one alert to the chat.

    Body (all optional):
        {
          "chat_id": "12345",
          "dry_run": false,
          "extra_note": "Launches tomorrow",
          "max_summary_chars": 600
        }
    """
    body = body or {}
    chat_id = body.get("chat_id")
    dry_run = bool(body.get("dry_run", False))
    extra_note = body.get("extra_note")
    max_summary_chars = int(body.get("max_summary_chars") or 600)

    service = _build_notification_service(session)
    outcome = await service.send_opportunity_alert(
        opportunity_id,
        chat_id=chat_id,
        dry_run=dry_run,
        extra_note=extra_note,
        max_summary_chars=max_summary_chars,
    )
    return {
        "notification_id": outcome.notification_id,
        "channel": outcome.channel,
        "chat_id": outcome.chat_id,
        "delivered": outcome.delivered,
        "text_chars": outcome.text_chars,
        "provider": outcome.provider,
        "message_id": outcome.message_id,
        "error": outcome.error,
    }


@router.get(
    "/notifications/history",
    summary="List recent notification attempts",
)
async def list_notifications(
    limit: int = 50,
    channel: str | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 8 endpoint — recent `Notification` rows for the dashboard."""
    service = _build_notification_service(session)
    rows = await service.list_history(limit=limit, channel=channel)
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "channel": r.channel,
                "payload": r.payload,
                "delivered_at": r.delivered_at.isoformat() if r.delivered_at else None,
                "error": r.error,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# v2.0 — content generation pipeline
# ---------------------------------------------------------------------------
@router.post(
    "/content/generate",
    summary="Run all content generators on the top opportunities",
)
async def run_content_generation(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 1 (v2.0) — fan-out content production.

    Body (all optional):
        {
          "limit": 5,                              # top N opportunities to process
          "only_qualified": true,                  # skip 'unqualified' rows
          "opportunity_ids": [1, 2, 3],            # OR explicit IDs
          "generators": ["daily_report", ...]      # OR restrict to subset
        }

    Returns a summary; per-opportunity errors are surfaced inline so
    one failure doesn't abort the rest.
    """
    body = body or {}
    llm = build_llm_provider(settings=get_settings())
    service = ContentGeneratorService(session=session, llm=llm)

    if body.get("opportunity_ids"):
        result = await service.run_for_ids([int(x) for x in body["opportunity_ids"]])
    else:
        result = await service.run_for_top_opportunities(
            limit=int(body.get("limit", 5)),
            only_qualified=bool(body.get("only_qualified", True)),
        )
    return result.as_dict()


@router.post(
    "/feishu/digest/send",
    summary="Send the daily AI-opportunity digest to a Feishu custom robot",
)
async def send_feishu_digest(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 2 (v2.0) — push the top opportunities to a Feishu group.

    Body (all optional):
        {
          "limit": 5,                          # top N by score
          "only_qualified": true,              # skip 'unqualified' rows
          "window_hours": 24,                  # only opps from the last N hours; null = all-time
          "auto_generate_content": false,      # run content_generator first
          "title_prefix": "AI 机会雷达日报"     # override header
        }

    Returns a `FeishuDigestSummary`. If no Feishu webhook is configured
    the underlying provider falls back to the mock and the request still
    succeeds — useful for local dev.
    """
    body = body or {}
    from app.services.feishu import FeishuBot, build_feishu_provider
    from app.config import get_settings

    settings = get_settings()
    provider = build_feishu_provider(settings)
    bot = FeishuBot(
        session=session,
        provider=provider,
        cta_base_url="http://localhost:3000/opportunities",
    )

    summary = await bot.send_digest(
        limit=int(body.get("limit", 5)),
        only_qualified=bool(body.get("only_qualified", True)),
        window_hours=(
            int(body["window_hours"])
            if body.get("window_hours") is not None
            else None
        ),
        auto_generate_content=bool(body.get("auto_generate_content", False)),
        title_prefix=str(body["title_prefix"]) if body.get("title_prefix") else None,
    )

    # Close the provider's httpx client if it owns one — avoids resource leaks.
    close = getattr(provider, "aclose", None)
    if callable(close):
        try:
            await close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass

    return summary.as_dict()
