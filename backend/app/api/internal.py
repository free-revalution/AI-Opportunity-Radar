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
from app.services.clustering import ClusteringService
from app.services.ingestion import IngestionService
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
    report = await service.run_once()

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

    report = await service.run_once()
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

    report = await service.run_once()
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
    report = await service.run_once()
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

    service = ResearchService(
        session,
        limit=limit,
        max_urls=int(max_urls) if max_urls is not None else None,
    )
    if use_mock_web:
        from app.services.research.mock_web_data import MockWebDataProvider

        service.web = MockWebDataProvider()
    if use_mock_llm:
        from app.services.research.mock_llm import MockResearchLLMProvider

        service.llm = MockResearchLLMProvider()

    report = await service.run_once()
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

    service = ResearchService(session)
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