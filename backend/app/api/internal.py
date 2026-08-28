"""Internal endpoints used by n8n / cron workers.

These routes are NOT mounted under the public `/api` tree — they live
under `/api/internal` and (in production) are protected by a shared
secret header (`X-Radar-Webhook`).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.metrics import record_pipeline_run
from app.schemas.on_demand import OnDemandResearchRequest
from app.schemas.order import (
    OrderCreateRequest,
    OrderListResponse,
    OrderResponse,
    OrderStatsResponse,
    OrderStatusUpdateRequest,
)
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
# Phase 8 / Phase 6 — Notifications (channel-agnostic)
# ---------------------------------------------------------------------------
def _build_notification_service(
    session: AsyncSession, *, channel: str | None = None
) -> NotificationService:
    """Build a `NotificationService` for the requested channel.

    When `channel` is omitted, picks the settings default (Feishu per
    Phase 6 product decision; Telegram remains the configured fallback
    via `NOTIFICATION_FALLBACK_CHANNELS`).

    Provider selection rules (in order):

      1. Real provider when the matching channel has credentials configured
         (`feishu_webhook_url` for Feishu; `telegram_bot_token` for
         Telegram). Local dev with real Webhook URLs will hit the real
         API.
      2. Mock provider when `mock_external_services=true` AND no real
         credentials are present. This is the "fully offline" path used
         by tests and offline exploration.
    """
    settings = get_settings()
    requested = (channel or "").lower()

    if requested == "feishu":
        if settings.feishu_webhook_url:
            # — Real Feishu: build via factory with explicit channel.
            from app.services.bots import build_bot_provider

            provider = build_bot_provider(settings, channel="feishu")
        elif getattr(settings, "mock_external_services", False):
            from app.services.bots.feishu_adapter import FeishuBotAdapter
            from app.services.feishu.mock_client import MockFeishuProvider

            provider = FeishuBotAdapter(MockFeishuProvider())
        else:
            # — No URL + no mock mode: fall back to mock so the call
            # doesn't crash; record a config warning in the response.
            from app.services.bots.feishu_adapter import FeishuBotAdapter
            from app.services.feishu.mock_client import MockFeishuProvider

            provider = FeishuBotAdapter(MockFeishuProvider())
    else:
        if settings.telegram_bot_token:
            from app.services.bots import build_bot_provider

            provider = build_bot_provider(settings, channel="telegram")
        elif getattr(settings, "mock_external_services", False):
            from app.services.bots.telegram_adapter import TelegramBotAdapter
            from app.services.notification.mock_telegram import MockTelegramProvider

            provider = TelegramBotAdapter(MockTelegramProvider())
        else:
            from app.services.bots.telegram_adapter import TelegramBotAdapter
            from app.services.notification.mock_telegram import MockTelegramProvider

            provider = TelegramBotAdapter(MockTelegramProvider())

    return NotificationService(
        session, settings=settings, provider=provider, channel=channel
    )


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
          "channel": "feishu" | "telegram",  # Phase 6 — defaults to
                                               # settings.notification_default_channel
          "dry_run": false,
          "max_entries": 5,
          "per_entry_summary_chars": 240,
          "min_score": 70.0
        }
    """
    body = body or {}
    chat_id = body.get("chat_id")
    channel = body.get("channel")
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

    service = _build_notification_service(session, channel=channel)
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
    logger.info(
        "notifications_digest_dispatched",
        requested_channel=service.channel,
        **summary.as_dict(),
    )
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

    **Phase 6 note**: this endpoint remains for the *auto content
    generation* path (Phase 3 `content_generator` + Phase 2 Feishu
    card formatter) that n8n cron triggers. For plain digest sends
    without auto-generation, prefer
    `POST /api/internal/notifications/digest/send` with
    `{"channel": "feishu"}` — it shares the same `BotProvider`
    abstraction as Telegram. The two will be consolidated when n8n
    is migrated to call the unified endpoint.

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


@router.get(
    "/content/by_opportunity",
    summary="List opportunities with their generated content per channel",
)
async def list_content_by_opportunity(
    only_qualified: bool = True,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 3 (v2.0) — Content Center backend.

    Returns one row per opportunity (top `limit` by score), each with the
    most-recent generated content for every sales channel
    (feishu / xianyu / xiaohongshu / wechat_article). The frontend uses
    this to render the Content Center page.
    """
    from app.models import Notification, Opportunity

    stmt = select(Opportunity).order_by(Opportunity.total_score.desc())
    if only_qualified:
        stmt = stmt.where(
            Opportunity.commercial_status.in_(["qualified", "promising"])
        )
    stmt = stmt.limit(limit)
    opportunities = list((await session.execute(stmt)).scalars().all())

    if not opportunities:
        return {"generated_at": _utc_now_iso(), "items": []}

    opp_ids = [o.id for o in opportunities]
    notif_stmt = (
        select(Notification)
        .where(Notification.payload["opportunity_id"].as_integer().in_(opp_ids))
        # `id DESC` is the tie-breaker — `created_at` defaults to
        # `func.now()` server-side and two rows inserted within the
        # same second share a timestamp.
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    )
    notifications = list((await session.execute(notif_stmt)).scalars().all())

    # Group notifications per opportunity per channel — keep only the latest.
    grouped: dict[int, dict[str, dict[str, Any]]] = {oid: {} for oid in opp_ids}
    for n in notifications:
        try:
            oid = int((n.payload or {}).get("opportunity_id") or 0)
        except (TypeError, ValueError):
            continue
        if oid not in grouped:
            continue
        if n.channel in grouped[oid]:
            continue  # keep only the latest per channel
        grouped[oid][n.channel] = {
            "notification_id": n.id,
            "channel": n.channel,
            "title": (n.payload or {}).get("title") or "",
            "body": (n.payload or {}).get("body") or "",
            "metadata": (n.payload or {}).get("metadata") or {},
            "generator": (n.payload or {}).get("generator") or "",
            "format": (n.payload or {}).get("format") or "",
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }

    items: list[dict[str, Any]] = []
    for opp in opportunities:
        items.append(
            {
                "opportunity": {
                    "id": opp.id,
                    "title": opp.title,
                    "slug": opp.slug,
                    "summary": opp.summary,
                    "total_score": float(opp.total_score or 0.0),
                    "content_status": opp.content_status,
                    "commercial_status": opp.commercial_status,
                    "target_customer": opp.target_customer,
                    "market_size": opp.market_size,
                    "mvp_days": int(opp.mvp_days or 0),
                    "difficulty": opp.difficulty,
                    "monetization_model": opp.monetization_model,
                    "china_gap": opp.china_gap,
                },
                "content": grouped.get(opp.id, {}),
            }
        )

    return {"generated_at": _utc_now_iso(), "items": items}


@router.post(
    "/content/{opportunity_id}/mark_published",
    summary="Mark an opportunity's content as published (manual operator action)",
)
async def mark_content_published(
    opportunity_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 3 (v2.0) — flip an Opportunity's `content_status` to `published`.

    Body (all optional):
        { "commercial_status": "promising" }   # override the next stage

    Returns the new state.
    """
    from app.models import Opportunity

    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"opportunity {opportunity_id} not found",
        )

    opp.content_status = "published"
    body = body or {}
    next_stage = body.get("commercial_status")
    if next_stage in {"promising", "qualified", "unqualified"}:
        opp.commercial_status = next_stage
    await session.commit()

    return {
        "opportunity_id": opportunity_id,
        "content_status": opp.content_status,
        "commercial_status": opp.commercial_status,
    }


@router.post(
    "/content/{opportunity_id}/mark_sold",
    summary="Mark an opportunity's content as sold (revenue attribution)",
)
async def mark_content_sold(
    opportunity_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 3 / 4 (v2.0) — flip `content_status` to `sold`.

    Backwards-compatible body shape: empty / None body → just flips the
    flag, same as Phase 3.

    Phase 4 body (optional, used by the Content Center's Mark Sold
    dialog):
        {
          "order": {
            "customer_name": "张三",
            "customer_contact": "wechat:abc",
            "amount_cny": 49,
            "channel": "xianyu",
            "payment_method": "wechat",
            "notes": "..."
          }
        }
    When `order` is present, an `orders` row is created in the same
    transaction and the response includes the order payload.
    """
    from app.models import Opportunity
    from app.repositories.orders import OrderRepository

    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"opportunity {opportunity_id} not found",
        )

    body = body or {}
    order_payload = body.get("order")
    created_order: dict[str, Any] | None = None

    if order_payload:
        try:
            req = OrderCreateRequest(
                opportunity_id=opportunity_id,
                # Default delivery_status='pending' so the operator can
                # explicitly mark it delivered/confirmed/refunded later.
                mark_opportunity_sold=True,
                **order_payload,
            )
        except ValidationError as exc:
            # `exc.errors()` may surface Decimal / datetime values that
            # aren't JSON-serialisable on their own — round-trip through
            # `default=str` so the 422 response is always JSON-safe.
            import json as _json

            safe_errors = _json.loads(_json.dumps(exc.errors(), default=str))
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"order": safe_errors},
            ) from exc

        repo = OrderRepository(session=session)
        order = await repo.create(
            opportunity_id=opportunity_id,
            customer_name=req.customer_name,
            customer_contact=req.customer_contact,
            amount_cny=Decimal(str(req.amount_cny)),
            channel=req.channel,
            payment_method=req.payment_method,
            payment_reference=req.payment_reference,
            delivery_status=req.delivery_status,
            notes=req.notes,
            commercial_status_snapshot=opp.commercial_status,
        )
        created_order = _serialize_order(order, opportunity_title=opp.title)

    opp.content_status = "sold"
    opp.commercial_status = "promising"
    await session.commit()

    response: dict[str, Any] = {
        "opportunity_id": opportunity_id,
        "content_status": opp.content_status,
        "commercial_status": opp.commercial_status,
    }
    if created_order:
        response["order"] = created_order
    return response


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp — helper for response envelopes."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# v2.0 Phase 4 — commercial orders
# ---------------------------------------------------------------------------
def _serialize_order(order: Any, *, opportunity_title: str | None = None) -> dict[str, Any]:
    """Format an Order row for JSON output.

    `amount_cny` is Numeric(10, 2) in SQL — coerce to float so JSON
    clients don't have to round-trip a Decimal.
    """
    return {
        "id": order.id,
        "opportunity_id": order.opportunity_id,
        "opportunity_title": opportunity_title,
        "customer_name": order.customer_name,
        "customer_contact": order.customer_contact,
        "amount_cny": float(order.amount_cny),
        "channel": order.channel,
        "payment_method": order.payment_method,
        "payment_reference": order.payment_reference,
        "delivery_status": order.delivery_status,
        "commercial_status_snapshot": order.commercial_status_snapshot,
        "notes": order.notes,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
    }


@router.post(
    "/orders",
    summary="Record a commercial order (Phase 4 v2.0)",
)
async def create_order(
    body: OrderCreateRequest,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 4 — record one sale of an Opportunity.

    Body:
        {
          "opportunity_id": 1,
          "customer_name": "张三",
          "customer_contact": "wechat:zx",
          "amount_cny": 49,
          "channel": "xianyu",
          "payment_method": "wechat",
          "delivery_status": "pending",
          "notes": "...",
          "mark_opportunity_sold": true
        }

    Returns the new order. When `mark_opportunity_sold=true` (default),
    also flips the anchor opportunity's `content_status` to `'sold'`
    and bumps `commercial_status` to `'promising'`.
    """
    from app.models import Opportunity
    from app.repositories.orders import OrderRepository

    opp = await session.get(Opportunity, body.opportunity_id)
    if opp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"opportunity {body.opportunity_id} not found",
        )

    repo = OrderRepository(session=session)
    order = await repo.create(
        opportunity_id=body.opportunity_id,
        customer_name=body.customer_name,
        customer_contact=body.customer_contact,
        amount_cny=Decimal(str(body.amount_cny)),
        channel=body.channel,
        payment_method=body.payment_method,
        payment_reference=body.payment_reference,
        delivery_status=body.delivery_status,
        notes=body.notes,
        commercial_status_snapshot=opp.commercial_status,
    )

    if body.mark_opportunity_sold:
        opp.content_status = "sold"
        opp.commercial_status = "promising"

    await session.commit()
    return _serialize_order(order, opportunity_title=opp.title)


@router.get(
    "/orders",
    summary="List orders (newest first)",
)
async def list_orders(
    channel: str | None = None,
    delivery_status: str | None = None,
    opportunity_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 4 — paginated order list for the /orders dashboard.

    All filters are optional and ANDed together.
    """
    from app.repositories.orders import OrderRepository

    repo = OrderRepository(session=session)
    rows, total = await repo.list_paginated(
        limit=limit,
        offset=offset,
        channel=channel,
        delivery_status=delivery_status,
        opportunity_id=opportunity_id,
    )

    # Resolve opportunity titles in a single batched query so the
    # frontend doesn't have to make N round-trips to render the table.
    opp_ids = {r.opportunity_id for r in rows}
    title_by_id: dict[int, str] = {}
    if opp_ids:
        from app.models import Opportunity

        title_rows = (
            await session.execute(
                select(Opportunity.id, Opportunity.title).where(
                    Opportunity.id.in_(opp_ids)
                )
            )
        ).all()
        title_by_id = {int(r[0]): r[1] for r in title_rows}

    return {
        "generated_at": _utc_now_iso(),
        "items": [
            _serialize_order(r, opportunity_title=title_by_id.get(r.opportunity_id))
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/orders/stats",
    summary="Aggregated sales stats for the /orders dashboard",
)
async def get_order_stats(
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 4 — totals, by-channel breakdown, by-delivery-status counts."""
    from app.repositories.orders import OrderRepository

    repo = OrderRepository(session=session)
    return await repo.stats()


@router.get(
    "/orders/{order_id}",
    summary="Get one order by id",
)
async def get_order(
    order_id: int,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 4 — single-order detail view."""
    from app.models import Opportunity
    from app.repositories.orders import OrderRepository

    repo = OrderRepository(session=session)
    order = await repo.get_by_id(order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"order {order_id} not found",
        )

    opp = await session.get(Opportunity, order.opportunity_id)
    title = opp.title if opp else None
    return _serialize_order(order, opportunity_title=title)


@router.post(
    "/orders/{order_id}/status",
    summary="Update an order's delivery_status (Phase 4)",
)
async def update_order_status(
    order_id: int,
    body: OrderStatusUpdateRequest,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 4 — flip an order's `delivery_status`.

    Allowed transitions (any → any; not strict because the operator
    knows their reality better than the model does):
        pending → delivered → confirmed
        pending → cancelled
        delivered/confirmed → refunded

    Returns the updated order.
    """
    from app.models import Opportunity
    from app.repositories.orders import OrderRepository

    repo = OrderRepository(session=session)
    order = await repo.get_by_id(order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"order {order_id} not found",
        )

    await repo.update_status(order, body.delivery_status)

    opp = await session.get(Opportunity, order.opportunity_id)
    title = opp.title if opp else None
    await session.commit()
    return _serialize_order(order, opportunity_title=title)


# ---------------------------------------------------------------------------
# v2.0 Phase 5 — on-demand research reports
# ---------------------------------------------------------------------------
def _on_demand_slug() -> str:
    """Stable, unique slug for an on-demand opportunity.

    Real opportunities use AI-extracted slugs; the on-demand path
    bypasses that and just needs a unique value to satisfy the DB
    constraint. UUID4 hex is short enough to read in logs and
    guaranteed unique.
    """
    import uuid

    return f"on-demand-{uuid.uuid4().hex[:16]}"


def _on_demand_seed_field(opportunity: Any, body: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Return (seed_url, seed_topic) from the body, falling back to the
    opportunity summary if the body is missing. The endpoint always
    sets one of these on the opportunity's summary field so the
    `/recent` listing can reconstruct the original input without
    needing a parallel column.
    """
    url = (body or {}).get("url")
    topic = (body or {}).get("topic")
    if url:
        return url, None
    if topic:
        return None, topic
    # Fall back: parse summary which the endpoint writes as
    # "(on-demand) url=<...>" or "topic=<...>".
    summary = opportunity.summary or ""
    if summary.startswith("(on-demand) url="):
        return summary.removeprefix("(on-demand) url="), None
    if summary.startswith("(on-demand) topic="):
        return None, summary.removeprefix("(on-demand) topic=")
    return None, None


@router.post(
    "/research/on_demand",
    summary="Run an ad-hoc deep-research report from a URL or topic (Phase 5)",
)
async def run_on_demand_research(
    body: OnDemandResearchRequest,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 5 — pay-per-report service.

    Body:
        {
          "url": "https://example.com/ai-product",     # OR
          "topic": "AI 法律合同审核",
          "customer_name": "李四",                      # optional
          "customer_contact": "wechat:lisi",
          "amount_cny": 299,
          "channel": "wechat",
          ...
        }

    Flow:
      1. Validate exactly one of `url` / `topic`.
      2. Bootstrap a fresh Opportunity (no source linkage — this is
         operator-driven, not pipeline-driven).
      3. Run ResearchService inline with `seed_urls` derived from the
         URL directly OR from a quick `web.search(topic)` call.
      4. If `customer_name` + `amount_cny` were provided, attach an
         Order in the same transaction and flip the opportunity's
         `content_status` to `sold`.
      5. Return a compact preview (job_id + report excerpt).

    Synchronous by design — customers expect a "report ready now"
    experience. If we ever need long-tail async, the existing
    `/research/run` worker pattern already covers it.
    """
    from app.models import Opportunity, ResearchJob, ResearchReport
    from app.repositories import OpportunityRepository
    from app.repositories.orders import OrderRepository
    from app.services.research import ResearchService

    body_dict = body.model_dump()
    seed_url = body_dict.get("url")
    seed_topic = body_dict.get("topic")

    # ---- 1. Build / load seed URLs -----------------------------------------
    seed_urls: list[str] = []
    if seed_url:
        seed_urls = [seed_url]
    else:
        # Topic → quick web search to pick 3-5 anchors.
        from app.services.research.web_data import build_web_data_provider

        web = build_web_data_provider(get_settings())
        try:
            extra = await web.search(seed_topic or "", limit=5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("on_demand_search_failed", error=str(exc))
            extra = []
        seed_urls = [d.url for d in extra if d.url][:5]
        if not seed_urls:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "no search results for the given topic — try a URL "
                    "instead, or refine the topic"
                ),
            )

    # ---- 2. Bootstrap an Opportunity --------------------------------------
    title = (seed_url or seed_topic or "On-demand research")[:255]
    summary_marker = f"(on-demand) url={seed_url}" if seed_url else f"(on-demand) topic={seed_topic}"
    opp_repo = OpportunityRepository(session=session)
    opp = await opp_repo.create(
        title=title,
        slug=_on_demand_slug(),
        summary=summary_marker,
        # Treat on-demand as already "research eligible" so the
        # ResearchService picks it up cleanly.
        status="research_eligible",
        commercial_status="qualified",  # the customer is paying for it
        content_status="new",
    )

    # ---- 3. Create the ResearchJob + run inline ---------------------------
    job = ResearchJob(
        opportunity_id=opp.id,
        status=ResearchService.JOB_PENDING,
    )
    session.add(job)
    await session.flush()

    service = ResearchService(session=session)
    outcome = await service.process_job(job.id, seed_urls=seed_urls)

    # `process_job` commits on its own (success or failure path).
    # We re-load the job + report for the response.
    job = await session.get(ResearchJob, job.id)
    report = (
        await session.execute(
            select(ResearchReport)
            .where(ResearchReport.opportunity_id == opp.id)
            .order_by(ResearchReport.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    # ---- 4. Optionally attach an Order ------------------------------------
    order_id: int | None = None
    has_order_fields = bool(body.customer_name and body.amount_cny is not None)
    if has_order_fields and outcome.status == ResearchService.JOB_COMPLETED:
        order_repo = OrderRepository(session=session)
        order = await order_repo.create(
            opportunity_id=opp.id,
            customer_name=body.customer_name or "",
            customer_contact=body.customer_contact,
            amount_cny=Decimal(str(body.amount_cny)),
            channel=body.channel or "direct",
            payment_method=body.payment_method,
            payment_reference=body.payment_reference,
            delivery_status="delivered",  # report is generated = delivered
            notes=body.notes,
            commercial_status_snapshot=opp.commercial_status,
        )
        await session.refresh(order)
        opp.content_status = "sold"
        opp.commercial_status = "promising"
        await session.commit()
        order_id = order.id
    elif has_order_fields:
        # Order requested but research failed — still record the partial work.
        await session.commit()

    return {
        "opportunity_id": opp.id,
        "opportunity_title": opp.title,
        "opportunity_slug": opp.slug,
        "job_id": job.id if job else None,
        "status": outcome.status,
        "recommendation": outcome.recommendation,
        "confidence": float(outcome.confidence),
        "sources_count": outcome.sources_count,
        "executive_summary": (report.executive_summary if report else None),
        "order_id": order_id,
    }


@router.get(
    "/research/on_demand/recent",
    summary="List recent on-demand research jobs (Phase 5)",
)
async def list_on_demand_research(
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 5 — `/on-demand` dashboard list.

    Returns the most recent on-demand jobs (those whose opportunity was
    tagged with the on-demand summary marker) with a compact report
    preview.
    """
    from app.models import Opportunity, ResearchJob, ResearchReport

    # Two-step load — avoids SQLAlchemy's auto-correlation complaints on
    # the scalar subquery (the inline `order_by().limit(1)` wrapper
    # conflates the FROM clause under some SQLite versions).
    jobs_stmt = (
        select(ResearchJob, Opportunity)
        .join(Opportunity, ResearchJob.opportunity_id == Opportunity.id)
        .where(Opportunity.summary.like("(on-demand)%"))
        .order_by(ResearchJob.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(jobs_stmt)).all()

    # Bulk-load the latest report per opportunity.
    opp_ids = [opp.id for _, opp in rows]
    latest_report_by_opp: dict[int, ResearchReport] = {}
    if opp_ids:
        report_stmt = (
            select(ResearchReport)
            .where(ResearchReport.opportunity_id.in_(opp_ids))
            .order_by(
                ResearchReport.opportunity_id.asc(),
                ResearchReport.id.desc(),
            )
        )
        for r in (await session.execute(report_stmt)).scalars().all():
            latest_report_by_opp.setdefault(r.opportunity_id, r)

    items = []
    for job, opp in rows:
        report = latest_report_by_opp.get(opp.id)
        seed_url, seed_topic = _on_demand_seed_field(opp, None)
        items.append(
            {
                "job_id": job.id,
                "opportunity_id": opp.id,
                "status": job.status,
                "recommendation": report.recommendation if report else None,
                "confidence": float(report.confidence) if report else 0.0,
                "sources_count": (
                    len(report.sources_json.get("items", []))
                    if report and report.sources_json
                    else 0
                ),
                "error": job.error,
                "seed_url": seed_url,
                "seed_topic": seed_topic,
                "executive_summary": (report.executive_summary if report else None),
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            }
        )

    return {
        "generated_at": _utc_now_iso(),
        "items": items,
        "total": len(items),
    }


@router.get(
    "/research/on_demand/{job_id}",
    summary="Get a single on-demand research job with full report (Phase 5)",
)
async def get_on_demand_research(
    job_id: int,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(_check_webhook_secret),
) -> dict[str, Any]:
    """Phase 5 — single-job detail view.

    Returns the full report payload (all 7 sections + sources + raw
    metadata). The frontend uses this to render the report inline on
    the `/on-demand` page after submission.
    """
    from app.models import Opportunity, ResearchJob, ResearchReport

    job = await session.get(ResearchJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"research job {job_id} not found",
        )

    opp = await session.get(Opportunity, job.opportunity_id)
    if opp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"opportunity {job.opportunity_id} not found for job {job_id}",
        )

    report = (
        await session.execute(
            select(ResearchReport)
            .where(ResearchReport.opportunity_id == opp.id)
            .order_by(ResearchReport.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    seed_url, seed_topic = _on_demand_seed_field(opp, None)

    report_payload = None
    if report is not None:
        report_payload = {
            "executive_summary": report.executive_summary,
            "market_analysis": report.market_analysis,
            "competition_analysis": report.competition_analysis,
            "china_analysis": report.china_analysis,
            "monetization_analysis": report.monetization_analysis,
            "mvp_analysis": report.mvp_analysis,
            "risk_analysis": report.risk_analysis,
            "recommendation": report.recommendation,
            "confidence": float(report.confidence),
            "sources": (report.sources_json or {}).get("items", []),
        }

    return {
        "job_id": job.id,
        "opportunity_id": opp.id,
        "opportunity_title": opp.title,
        "status": job.status,
        "recommendation": report.recommendation if report else None,
        "confidence": float(report.confidence) if report else 0.0,
        "sources_count": (
            len((report.sources_json or {}).get("items", []))
            if report
            else 0
        ),
        "error": job.error,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "seed_url": seed_url,
        "seed_topic": seed_topic,
        "report": report_payload,
    }
