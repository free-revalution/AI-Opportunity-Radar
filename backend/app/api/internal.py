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