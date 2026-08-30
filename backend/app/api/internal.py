"""Internal endpoints used by n8n / cron workers.

These routes are NOT mounted under the public `/api` tree — they live
under `/api/internal` and (in production) are protected by a shared
secret header (`X-Radar-Webhook`).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
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
from app.services.content_generator import ContentGeneratorService, get_registry
from app.services.ingestion import IngestionService
from app.services.llm import build_llm_provider
from app.services.notification import NotificationService
from app.services.research import ResearchService
from app.services.scoring import ScoringService
from app.services.screening import ScreeningService
from app.utils import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.post(
    "/discovery/run",
    summary="Run a one-shot ingestion across enabled source connectors",
)
async def run_discovery(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _actor: str = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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

    # Phase 8 — wire the `generators` field through. Commented in the
    # original Phase 3 schema but never actually read by the endpoint.
    # Validate against the live registry so an upstream caller can't
    # request a generator that doesn't exist (typo, removed channel).
    generators = body.get("generators")
    if generators is not None:
        if not isinstance(generators, list) or not all(
            isinstance(g, str) for g in generators
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="`generators` must be a list[str]",
            )
        allowed = set(get_registry().names())
        unknown = [g for g in generators if g not in allowed]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": "unknown generator(s) requested",
                    "unknown": unknown,
                    "allowed": sorted(allowed),
                },
            )

    if body.get("opportunity_ids"):
        result = await service.run_for_ids(
            [int(x) for x in body["opportunity_ids"]],
            generators=generators,
        )
    else:
        result = await service.run_for_top_opportunities(
            limit=int(body.get("limit", 5)),
            only_qualified=bool(body.get("only_qualified", True)),
            generators=generators,
        )
    # Flush wrote Notification rows mid-run; commit so they survive the
    # request-scoped session teardown (otherwise the implicit rollback at
    # `async with sessionmaker()` close wipes them).
    await session.commit()
    return result.as_dict()


@router.post(
    "/feishu/digest/send",
    summary="Send the daily AI-opportunity digest to a Feishu custom robot",
)
async def send_feishu_digest(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
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
    channel: str | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
) -> dict[str, Any]:
    """Phase 3 + Phase 8 (v2.0) — Content Center backend.

    Returns one row per opportunity (top `limit` by score), each with the
    most-recent generated content for every sales channel
    (feishu / xianyu / xiaohongshu / wechat_article). The frontend uses
    this to render the Content Center page.

    Phase 8 — optional `channel` query param filters the response down
    to a single sales channel (e.g. `?channel=wechat_article`). Frontend
    uses this for the "只看公众号" tab so it doesn't have to ship the
    full payload and slice client-side.
    """
    grouped, opportunities = await _fetch_grouped_content(
        session=session,
        only_qualified=only_qualified,
        limit=limit,
    )

    items: list[dict[str, Any]] = []
    for opp in opportunities:
        opp_channels = grouped.get(opp.id, {})
        if channel is not None:
            opp_channels = (
                {channel: opp_channels[channel]}
                if channel in opp_channels
                else {}
            )
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
                    "channel_published": dict(opp.channel_published or {}),
                    "target_customer": opp.target_customer,
                    "market_size": opp.market_size,
                    "mvp_days": int(opp.mvp_days or 0),
                    "difficulty": opp.difficulty,
                    "monetization_model": opp.monetization_model,
                    "china_gap": opp.china_gap,
                },
                "content": opp_channels,
            }
        )

    return {"generated_at": _utc_now_iso(), "items": items}


async def _fetch_grouped_content(
    *,
    session: AsyncSession,
    only_qualified: bool,
    limit: int,
    opportunity_ids: list[int] | None = None,
) -> tuple[dict[int, dict[str, dict[str, Any]]], list[Any]]:
    """Shared helper for `/content/by_opportunity` and `/content/export`.

    Returns a 2-tuple of (grouped, opportunities):
      * `grouped[opp_id][channel] = payload_dict` — only the latest
        notification per channel per opp
      * `opportunities` — the Opportunity rows in score-DESC order
        (matches `grouped`'s iteration order for the response builder)

    If `opportunity_ids` is provided, `only_qualified` + `limit` are
    ignored — the IDs are used verbatim.
    """
    from app.models import Notification, Opportunity

    if opportunity_ids:
        # Caller-specified subset — bypass score filter and limit.
        rows = (
            await session.execute(
                select(Opportunity).where(Opportunity.id.in_(opportunity_ids))
            )
        ).scalars().all()
        opportunities = list(rows)
    else:
        stmt = select(Opportunity).order_by(Opportunity.total_score.desc())
        if only_qualified:
            stmt = stmt.where(
                Opportunity.commercial_status.in_(["qualified", "promising"])
            )
        stmt = stmt.limit(limit)
        opportunities = list((await session.execute(stmt)).scalars().all())

    if not opportunities:
        return {}, []

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
        payload = n.payload or {}
        # Phase 10 — top-level `quality_score` lives on the payload (set
        # by POST /quality with persist=true). Surface it so the Content
        # Center can render a badge without a second round-trip.
        quality_score = payload.get("quality_score")
        grouped[oid][n.channel] = {
            "notification_id": n.id,
            "channel": n.channel,
            "title": payload.get("title") or "",
            "body": payload.get("body") or "",
            "metadata": payload.get("metadata") or {},
            "generator": payload.get("generator") or "",
            "format": payload.get("format") or "",
            "quality_score": quality_score,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }

    return grouped, opportunities


@router.post(
    "/content/{opportunity_id}/mark_published",
    summary="Mark an opportunity's content as published (manual operator action)",
)
async def mark_content_published(
    opportunity_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
) -> dict[str, Any]:
    """Phase 3 + Phase 8 (v2.0) — flip an Opportunity's `content_status`.

    Phase 8 — per-channel publish tracking via the
    `Opportunity.channel_published` JSON map:

      * No `channel` in body → mark all four sales channels as
        published (legacy Phase 3 behaviour; `content_status` flips to
        `published` once).
      * `channel: "wechat_article"` → stamp just that one channel in
        `channel_published`. `content_status` flips to `published` if
        any channel becomes marked; stays at its previous state if the
        operator is un-marking (we don't support un-mark yet, but the
        shape leaves room for it).

    Body (all optional):
        {
          "channel": "wechat_article",                # Phase 8 — subset
          "commercial_status": "promising"            # optional stage bump
        }

    Returns the new state including the full `channel_published` map.
    """
    from app.models import Opportunity

    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"opportunity {opportunity_id} not found",
        )

    body = body or {}
    next_stage = body.get("commercial_status")
    # — Start from the persisted map (JSONB column → Python dict).
    cp: dict[str, str] = dict(opp.channel_published or {})
    now_iso = _utc_now_iso()
    raw_channel = body.get("channel")
    # Distinguish "not provided" (None → legacy mark-all) from
    # "provided but empty/invalid" (→ 422). The legacy behaviour stays
    # for backwards compatibility with Phase 3 callers.
    if raw_channel is not None:
        if not isinstance(raw_channel, str) or not raw_channel.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="`channel` must be a non-empty string",
            )
        cp[raw_channel] = now_iso
    else:
        # Legacy "mark all published" path — stamp every known channel so
        # the Content Center ✓ badges line up everywhere.
        for ch in ("feishu", "xianyu", "xiaohongshu", "wechat_article"):
            cp[ch] = now_iso

    opp.channel_published = cp
    if cp:
        # High-water mark: any channel flipped → published.
        opp.content_status = "published"
    if next_stage in {"promising", "qualified", "unqualified"}:
        opp.commercial_status = next_stage
    await session.commit()

    return {
        "opportunity_id": opportunity_id,
        "content_status": opp.content_status,
        "commercial_status": opp.commercial_status,
        "channel_published": dict(opp.channel_published or {}),
    }


@router.post(
    "/content/{opportunity_id}/mark_sold",
    summary="Mark an opportunity's content as sold (revenue attribution)",
)
async def mark_content_sold(
    opportunity_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
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


# ---------------------------------------------------------------------------
# Phase 8 (v2.0) — multi-channel content operations
# ---------------------------------------------------------------------------
@router.post(
    "/content/regenerate/{opportunity_id}",
    summary="Regenerate content for a single opportunity (Phase 8 v2.0)",
)
async def regenerate_opportunity_content(
    opportunity_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
) -> dict[str, Any]:
    """Phase 8 (v2.0) — single-opportunity regenerate.

    Body (all optional):
        {
          "generators": ["wechat_article"],   # subset; default = all
          "delete_previous": false            # if true, hard-delete prior
                                             # Notifications on the same
                                             # channels before re-running
        }

    Behaviour:
      * `enrich=False` — the Opportunity is already enriched; no need to
        burn tokens re-extracting the same six commercial fields.
      * Default `delete_previous=False` → APPEND mode. New Notification
        rows are created; old ones remain so the operator can roll back
        via the audit trail.
      * 404 if the opportunity doesn't exist.

    Returns a per-generator summary.
    """
    from sqlalchemy import delete

    from app.models import Notification, Opportunity, ResearchReport

    body = body or {}
    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"opportunity {opportunity_id} not found",
        )

    generators = body.get("generators")
    if generators is not None:
        if not isinstance(generators, list) or not all(
            isinstance(g, str) for g in generators
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="`generators` must be a list[str]",
            )
        allowed = set(get_registry().names())
        unknown = [g for g in generators if g not in allowed]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": "unknown generator(s) requested",
                    "unknown": unknown,
                    "allowed": sorted(allowed),
                },
            )

    delete_previous = bool(body.get("delete_previous", False))
    if delete_previous and generators:
        # Only delete the channels that are about to be re-generated —
        # preserve the operator's history on the others.
        channels = {
            get_registry().get(g).channel
            for g in generators
            if get_registry().get(g).channel
        }
        if channels:
            stmt = delete(Notification).where(
                Notification.channel.in_(channels),
                Notification.payload["opportunity_id"].as_integer() == opportunity_id,
            )
            await session.execute(stmt)
            await session.flush()

    # Pull the latest research report for context.
    report = (
        await session.execute(
            select(ResearchReport)
            .where(ResearchReport.opportunity_id == opportunity_id)
            .order_by(ResearchReport.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    llm = build_llm_provider(settings=get_settings())
    service = ContentGeneratorService(session=session, llm=llm)
    produced = await service.run_for_opportunity(
        opp, report=report, generators=generators, enrich=False
    )
    # Persist any new Notifications (flushed by the service) before the
    # request-scoped session closes — see commit note on
    # `run_content_generation`.
    await session.commit()

    return {
        "opportunity_id": opportunity_id,
        "regenerated_count": len(produced),
        "generators": [p.generator for p in produced],
        "items": [
            {
                "generator": p.generator,
                "channel": p.channel,
                "title": p.title,
                "char_count": len(p.content) if isinstance(p.content, str) else None,
            }
            for p in produced
        ],
    }


@router.post(
    "/content/export",
    summary="Bulk-export generated content as CSV / JSON / bundle (Phase 8 v2.0)",
)
async def export_content(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
) -> Response:
    """Phase 8 (v2.0) — batch export.

    Body (all optional except `format`):
        {
          "opportunity_ids": [12, 34, 56],   # OR
          "limit": 50, "only_qualified": true,
          "channels": ["xianyu", "wechat_article"],  # subset; default = all
          "format": "csv" | "json" | "bundle"        # REQUIRED
        }

    Returns:
      * **csv** — `text/csv; charset=utf-8`. Header row:
          opportunity_id, opportunity_title, channel, title, format,
          body, metadata, generator, created_at. One row per
          opportunity-channel.
      * **json** — JSON envelope
          `{exported_at, items: [{opportunity_id, content: {...}}]}`
      * **bundle** — JSON envelope with file payloads so the frontend
          can wrap them in a Blob and trigger a browser download:
          `{exported_at, files: [{filename, content_type, content}]}`.
          This is the most useful format for "copy-paste into the
          公众号编辑器" workflow.
    """
    import csv as _csv
    import io as _io
    import json as _json

    body = body or {}
    fmt = (body.get("format") or "csv").lower()
    if fmt not in {"csv", "json", "bundle"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"format must be csv|json|bundle, got {fmt!r}",
        )

    opp_ids_in = body.get("opportunity_ids")
    limit = int(body.get("limit", 50))
    only_qualified = bool(body.get("only_qualified", True))
    channels = body.get("channels")

    if opp_ids_in is not None and not (
        isinstance(opp_ids_in, list) and all(isinstance(x, int) for x in opp_ids_in)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`opportunity_ids` must be a list[int]",
        )
    if channels is not None and not (
        isinstance(channels, list) and all(isinstance(x, str) for x in channels)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`channels` must be a list[str]",
        )

    grouped, opportunities = await _fetch_grouped_content(
        session=session,
        only_qualified=only_qualified,
        limit=limit,
        opportunity_ids=opp_ids_in,
    )

    # Build {opp_id: {channel: payload}}, filtered by the requested channel subset.
    filtered: dict[int, dict[str, dict[str, Any]]] = {}
    opp_titles: dict[int, str] = {}
    for opp in opportunities:
        opp_titles[opp.id] = opp.title or f"opp-{opp.id}"
        for ch, payload in grouped.get(opp.id, {}).items():
            if channels is None or ch in channels:
                filtered.setdefault(opp.id, {})[ch] = payload

    if fmt == "csv":
        buf = _io.StringIO()
        writer = _csv.writer(buf, quoting=_csv.QUOTE_ALL)
        writer.writerow(
            [
                "opportunity_id",
                "opportunity_title",
                "channel",
                "title",
                "format",
                "body",
                "metadata",
                "generator",
                "created_at",
            ]
        )
        for oid, ch_dict in filtered.items():
            for ch, payload in ch_dict.items():
                body_str = (
                    payload["body"]
                    if isinstance(payload["body"], str)
                    else _json.dumps(payload["body"], ensure_ascii=False)
                )
                meta_str = _json.dumps(
                    payload.get("metadata") or {}, ensure_ascii=False
                )
                writer.writerow(
                    [
                        oid,
                        opp_titles.get(oid, ""),
                        ch,
                        payload.get("title") or "",
                        payload.get("format") or "",
                        body_str,
                        meta_str,
                        payload.get("generator") or "",
                        payload.get("created_at") or "",
                    ]
                )
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="content_export.csv"'
            },
        )

    if fmt == "json":
        items = [
            {"opportunity_id": oid, "opportunity_title": opp_titles.get(oid, ""), "content": ch_dict}
            for oid, ch_dict in filtered.items()
        ]
        return Response(
            content=_json.dumps(
                {"exported_at": _utc_now_iso(), "items": items},
                ensure_ascii=False,
            ),
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="content_export.json"'
            },
        )

    # bundle — one file per opportunity-channel.
    files: list[dict[str, Any]] = []
    for oid, ch_dict in filtered.items():
        base_title = opp_titles.get(oid, f"opp-{oid}")
        slug = _slugify(base_title) or f"opp-{oid}"
        for ch, payload in ch_dict.items():
            ext = "md" if payload.get("format") == "markdown" else "json"
            content_type = (
                "text/markdown; charset=utf-8"
                if ext == "md"
                else "application/json; charset=utf-8"
            )
            body_value = payload.get("body") or ""
            if not isinstance(body_value, str):
                body_value = _json.dumps(body_value, ensure_ascii=False)
            files.append(
                {
                    "filename": f"{slug}-{ch}.{ext}",
                    "content_type": content_type,
                    "content": body_value,
                }
            )
    return Response(
        content=_json.dumps(
            {"exported_at": _utc_now_iso(), "files": files},
            ensure_ascii=False,
        ),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="content_export_bundle.json"'
        },
    )


# ---------------------------------------------------------------------------
# Phase 9 — content editing + version history
# ---------------------------------------------------------------------------
@router.post(
    "/content/{notification_id}/edit",
    summary="Save operator-edited content as a new version (Phase 9 v2.0)",
)
async def edit_notification_content(
    notification_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
) -> dict[str, Any]:
    """Phase 9 (v2.0) — operator-edited content becomes a new version.

    Body (one of `body` / `title` / `metadata` may be supplied; at
    least one must be present, otherwise it's a no-op):
        {
          "title": "...",       # optional — defaults to source title
          "body": "...",        # optional — defaults to source body
          "metadata": { ... },  # optional — merged with source metadata
          "edit_note": "..."    # optional — operator note for the audit trail
        }

    Behaviour:
      * Loads the source notification row.
      * Creates a NEW notification row on the same channel with the
        edited body / title / metadata. The original row stays
        untouched — full audit trail.
      * Records `edit_note` + the source `notification_id` in the
        new row's payload so the version-history view can show
        "edited from #42 at 2026-08-28 by operator".

    Returns the new notification_id + the persisted payload.
    """
    from app.models import Notification

    src = await session.get(Notification, notification_id)
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"notification {notification_id} not found",
        )

    body = body or {}
    new_body = body.get("body")
    new_title = body.get("title")
    new_metadata = body.get("metadata")
    edit_note = body.get("edit_note")

    if new_body is None and new_title is None and new_metadata is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="supply at least one of `body` / `title` / `metadata`",
        )

    src_payload = dict(src.payload or {})
    persisted_body = (
        new_body if new_body is not None else src_payload.get("body", "")
    )
    persisted_title = (
        new_title if new_title is not None else src_payload.get("title", "")
    )
    persisted_metadata = dict(src_payload.get("metadata") or {})
    if new_metadata:
        persisted_metadata.update(new_metadata)
    if edit_note:
        persisted_metadata["edit_note"] = edit_note

    new_payload = dict(src_payload)
    new_payload.update(
        {
            "title": persisted_title,
            "body": persisted_body,
            "metadata": persisted_metadata,
            # Audit trail fields — surfaced by /content/versions below.
            "edited_from_notification_id": src.id,
            "edit_note": edit_note,
        }
    )

    new_notif = Notification(channel=src.channel, payload=new_payload)
    session.add(new_notif)
    await session.commit()
    await session.refresh(new_notif)

    return {
        "notification_id": new_notif.id,
        "channel": new_notif.channel,
        "title": persisted_title,
        "body": persisted_body,
        "metadata": persisted_metadata,
        "edited_from_notification_id": src.id,
        "created_at": new_notif.created_at.isoformat() if new_notif.created_at else None,
    }


@router.get(
    "/content/{opportunity_id}/versions",
    summary="List all versions of generated content for one opp (Phase 9 v2.0)",
)
async def list_content_versions(
    opportunity_id: int,
    channel: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
) -> dict[str, Any]:
    """Phase 9 (v2.0) — full version history per (opp, channel).

    Returns up to `limit` most-recent Notifications for the given
    opportunity. Frontend renders a sidebar:
      v3 (latest) · 2026-08-28 13:45 · operator-edit (from v2)
      v2         · 2026-08-28 12:30 · wechat_article regenerate
      v1         · 2026-08-28 11:00 · initial generation

    Filters:
      * `channel` (optional) — restrict to one channel
      * `limit`   (optional, default 50) — most-recent N rows

    Each entry includes a tiny preview (first 80 chars of body) so
    the operator can pick the right version to restore without
    loading each one in full.
    """
    from app.models import Notification, Opportunity

    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"opportunity {opportunity_id} not found",
        )

    stmt = (
        select(Notification)
        .where(Notification.payload["opportunity_id"].as_integer() == opportunity_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    )
    if channel is not None:
        stmt = stmt.where(Notification.channel == channel)

    rows = list((await session.execute(stmt)).scalars().all())

    items: list[dict[str, Any]] = []
    for n in rows:
        payload = dict(n.payload or {})
        body_str = payload.get("body") or ""
        if not isinstance(body_str, str):
            body_str = json.dumps(body_str, ensure_ascii=False)
        meta = payload.get("metadata") or {}
        items.append(
            {
                "notification_id": n.id,
                "channel": n.channel,
                "title": payload.get("title") or "",
                "preview": body_str[:80],
                "char_count": len(body_str),
                "metadata": meta,
                "edited_from_notification_id": payload.get(
                    "edited_from_notification_id"
                ),
                "edit_note": payload.get("edit_note"),
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
        )

    return {
        "opportunity_id": opportunity_id,
        "channel": channel,
        "total": len(items),
        "items": items,
    }


@router.post(
    "/content/{notification_id}/quality",
    summary="LLM-as-judge quality score for one generated piece (Phase 10 v2.0)",
)
async def score_content_quality(
    notification_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
) -> dict[str, Any]:
    """Phase 10 (v2.0) — score a single piece of generated content.

    Body (all optional):
        {
          "threshold": 6.0,        # override DEFAULT_THRESHOLD for this call
          "persist":  true         # if true, store the score in the
                                   #   notification's payload under
                                   #   `quality_score` so the next
                                   #   /by_opportunity fetch shows it
                                   #   without a second LLM call
        }

    Returns the score envelope:
        {
          "notification_id": 42,
          "channel": "wechat_article",
          "title": "...",
          "score": {
            "hook_strength": 7.5,
            "cta_naturalness": 8.0,
            "data_accuracy": 5.0,
            "char_count_compliance": 9.0,
            "platform_style_match": 7.0,
            "total": 7.05,
            "rationale": "...",
            "below_threshold": false,
            "threshold_used": 6.0,
            "dimension_floor_used": 4.0
          }
        }

    The LLM call is short (max 400 tokens) and the result is NOT
    cached on the server — every call costs one round trip. Use
    `persist=true` to write the score into the row so subsequent
    Content Center loads don't recompute it.
    """
    from app.services.content_scorer import ContentQualityScorer

    body = body or {}
    persist = bool(body.get("persist", False))
    threshold_override = body.get("threshold")

    from app.models import Notification

    notif = await session.get(Notification, notification_id)
    if notif is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"notification {notification_id} not found",
        )

    payload = dict(notif.payload or {})
    payload["channel"] = notif.channel

    scorer = ContentQualityScorer()
    if threshold_override is not None:
        try:
            scorer.threshold = float(threshold_override)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"`threshold` must be a number, got {threshold_override!r}",
            )

    # We need an LLM provider here — build the same way the rest of the
    # content endpoints do.
    from app.services.llm import build_llm_provider

    llm = build_llm_provider()

    score = await scorer.score(notification_payload=payload, llm=llm)

    if persist:
        new_payload = dict(payload)
        new_payload["quality_score"] = score.as_dict()
        notif.payload = new_payload
        await session.commit()

    return {
        "notification_id": notif.id,
        "channel": notif.channel,
        "title": payload.get("title") or "",
        "score": score.as_dict(),
    }


@router.post(
    "/content/{notification_id}/auto_improve",
    summary="Score + auto-regenerate if below threshold (Phase 10 v2.0)",
)
async def auto_improve_content(
    notification_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
) -> dict[str, Any]:
    """Phase 10 (v2.0) — score → if below threshold, regenerate up to N
    times until the score is acceptable (or attempts run out).

    Body (all optional):
        {
          "threshold":      6.0,
          "max_attempts":   2,   # total LLM attempts, including the first
          "delete_previous": false
        }

    Behaviour:
      * Loads the source notification, identifies which generator /
        opportunity produced it.
      * Scores it with the same `ContentQualityScorer` as /quality.
      * If `score.below_threshold` and attempts < max_attempts, calls
        the generator again to produce a new version (append mode by
        default — historical rows preserved).
      * Returns the final score + how many retries were used + the new
        notification_id (if a retry happened).
    """
    from app.models import Notification, Opportunity
    from app.services.content_generator import (
        ContentGeneratorService,
        get_registry,
    )
    from app.services.content_scorer import ContentQualityScorer
    from app.services.llm import build_llm_provider

    body = body or {}
    try:
        threshold = float(body.get("threshold", 6.0))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"`threshold` must be a number, got {body.get('threshold')!r}",
        )
    try:
        max_attempts = max(1, min(int(body.get("max_attempts", 2)), 4))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"`max_attempts` must be int 1-4, got {body.get('max_attempts')!r}",
        )
    delete_previous = bool(body.get("delete_previous", False))

    notif = await session.get(Notification, notification_id)
    if notif is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"notification {notification_id} not found",
        )
    payload = dict(notif.payload or {})
    payload["channel"] = notif.channel
    opportunity_id = payload.get("opportunity_id")
    generator_name = payload.get("generator")
    if not opportunity_id or not generator_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="notification payload missing `opportunity_id` or `generator`",
        )

    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"opportunity {opportunity_id} not found",
        )

    registry = get_registry()
    if generator_name not in registry.names():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown generator {generator_name!r}",
        )

    llm = build_llm_provider()
    scorer = ContentQualityScorer()
    scorer.threshold = threshold

    # Attempt 1 — score the source notification.
    current_notif_id = notif.id
    score = await scorer.score(notification_payload=payload, llm=llm)
    attempts_used = 1

    service = ContentGeneratorService(session=session, llm=llm, registry=registry)

    while score.below_threshold and attempts_used < max_attempts:
        # Re-run the generator for this opp — produces a new Notification.
        produced = await service.run_for_opportunity(
            opp,
            generators=[generator_name],
            enrich=False,  # already enriched — don't burn tokens re-doing it
        )
        if not produced:
            break
        attempts_used += 1
        current_notif_id = produced[-1].notification_id
        new_notif = await session.get(Notification, current_notif_id)
        if new_notif is None:
            break
        new_payload = dict(new_notif.payload or {})
        new_payload["channel"] = new_notif.channel
        score = await scorer.score(notification_payload=new_payload, llm=llm)

    # Optional cleanup — when the operator opts in, delete the previous
    # low-scoring notifications for this (opp, generator) and keep
    # only the best one. Default off — append-only is safer.
    if delete_previous and attempts_used > 1:
        from sqlalchemy import delete as sa_delete

        await session.execute(
            sa_delete(Notification).where(
                Notification.payload["opportunity_id"].as_integer() == opportunity_id,
                Notification.payload["generator"].as_string() == generator_name,
                Notification.id != current_notif_id,
                Notification.channel == notif.channel,
            )
        )

    await session.commit()

    return {
        "notification_id": current_notif_id,
        "channel": notif.channel,
        "score": score.as_dict(),
        "below_threshold": score.below_threshold,
        "attempts_used": attempts_used,
        "max_attempts": max_attempts,
        "threshold": threshold,
    }


# ---------------------------------------------------------------------------
# Phase 11 — Publisher infrastructure (one-click publish)
# ---------------------------------------------------------------------------
@router.get(
    "/publish/channels",
    summary="List channels with a registered publisher (Phase 11 v2.0)",
)
async def list_publish_channels(
    _secret: None = Depends(require_admin),
) -> dict[str, Any]:
    """Return which channels can be published to right now and which
    publishers are unconfigured (so the frontend can show a friendly
    CTA)."""
    from app.services.publisher import (
        channels as _channels,
        get_publisher,
    )

    configured: list[dict[str, Any]] = []
    unconfigured: list[dict[str, Any]] = []
    for ch in _channels():
        publisher = get_publisher(ch)
        entry = {
            "channel": ch,
            "publisher": publisher.name,
            "configured": publisher.is_configured(),
        }
        if publisher.is_configured():
            configured.append(entry)
        else:
            unconfigured.append(entry)
    return {
        "channels": list(_channels()),
        "configured": configured,
        "unconfigured": unconfigured,
    }


@router.post(
    "/content/{notification_id}/publish",
    summary="Publish a single notification to its target platform (Phase 11 v2.0)",
)
async def publish_notification(
    notification_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
) -> dict[str, Any]:
    """Phase 11 (v2.0) — fan out to the publisher for the notification's
    channel. Body (all optional):

        {
          "channel": "...",    # override (rarely needed — defaults to notif.channel)
          "mark_published": true  # if true, stamp channel_published[ch]
                                 #   + flip content_status when the
                                 #   result is `success=True`
        }

    The publisher may return `success=False, skipped=True` when the
    platform credentials aren't configured — that's not a bug, it's
    a hint to the operator. Real failures (`success=False, error=...`)
    bubble up as a 502 so the operator knows to retry.
    """
    from app.models import Notification, Opportunity
    from app.services.publisher import (
        PublishResult,
        get_publisher,
    )

    body = body or {}
    mark_published = bool(body.get("mark_published", True))
    channel_override = body.get("channel")

    notif = await session.get(Notification, notification_id)
    if notif is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"notification {notification_id} not found",
        )

    channel = channel_override or notif.channel
    payload = dict(notif.payload or {})

    try:
        publisher = get_publisher(channel)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    result: PublishResult = await publisher.publish(payload)

    if result.success and mark_published:
        opportunity_id = payload.get("opportunity_id")
        if opportunity_id:
            opp = await session.get(Opportunity, opportunity_id)
            if opp is not None:
                cp = dict(opp.channel_published or {})
                cp[channel] = _utc_now_iso()
                opp.channel_published = cp
                opp.content_status = "published"
                await session.flush()
        await session.commit()
    elif mark_published:
        await session.commit()

    status_code = (
        status.HTTP_200_OK
        if result.success or result.skipped
        else status.HTTP_502_BAD_GATEWAY
    )
    return {
        "notification_id": notif.id,
        "channel": channel,
        "publisher": result.publisher,
        "success": result.success,
        "skipped": result.skipped,
        "external_id": result.external_id,
        "external_url": result.external_url,
        "error": result.error,
        "marked_published": result.success and mark_published,
    }


@router.post(
    "/content/batch_publish",
    summary="Publish a batch of notifications (Phase 11 v2.0)",
)
async def batch_publish_notifications(
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
    _secret: None = Depends(require_admin),
) -> dict[str, Any]:
    """Phase 11 (v2.0) — publish up to N notifications in one call.

    Body:
        {
          "notification_ids": [42, 43, 44],
          "mark_published": true   # optional, default true
        }

    Returns one PublishResult per input, in the same order. Does NOT
    abort on individual failures — the operator gets a full report.
    """
    from app.models import Notification, Opportunity
    from app.services.publisher import (
        batch_publish as _batch_publish,
    )

    body = body or {}
    raw_ids = body.get("notification_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`notification_ids` must be a non-empty list",
        )
    if not all(isinstance(x, int) for x in raw_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`notification_ids` must contain only integers",
        )
    mark_published = bool(body.get("mark_published", True))

    rows: dict[int, Notification] = {}
    for nid in raw_ids:
        n = await session.get(Notification, nid)
        if n is not None:
            rows[nid] = n

    ordered: list[tuple[str, dict[str, Any]]] = []
    for nid in raw_ids:
        n = rows.get(nid)
        if n is None:
            continue
        p = dict(n.payload or {})
        p["channel"] = n.channel
        ordered.append((n.channel, p))

    results = await _batch_publish(ordered)

    if mark_published:
        now_iso = _utc_now_iso()
        for (channel, _payload), result in zip(ordered, results):
            if not result.success:
                continue
            opportunity_id = _payload.get("opportunity_id")
            if not opportunity_id:
                continue
            opp = await session.get(Opportunity, opportunity_id)
            if opp is None:
                continue
            cp = dict(opp.channel_published or {})
            cp[channel] = now_iso
            opp.channel_published = cp
            opp.content_status = "published"
        await session.commit()

    return {
        "requested": len(raw_ids),
        "results": [r.as_dict() for r in results],
        "marked_published_count": sum(
            1 for r in results if r.success
        ),
    }


def _slugify(text: str) -> str:
    """ASCII-ish slug for export filenames. Falls back to ``"opp"`` when
    the input has no usable characters (e.g. all Chinese — we keep the
    CJK characters as-is so the operator can still recognise them in
    Finder)."""
    import re as _re

    cleaned = _re.sub(r"\s+", "-", text.strip())
    cleaned = _re.sub(r"[^0-9A-Za-z一-鿿\-_]", "", cleaned)
    return cleaned[:60].strip("-")


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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
    _secret: None = Depends(require_admin),
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
