"""Opportunities API.

Phase 2: backed by PostgreSQL via `OpportunityRepository`. When the
DB has no rows we fall back to demo data so the UI keeps working
during local bring-up. The seed script populates the DB with demo
opportunities on first run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories import OpportunityRepository
from app.schemas import OpportunityListResponse, OpportunityResponse, TriggerResearchResponse
from app.services.scoring import (
    RESEARCH_TRIGGER_THRESHOLD,
    recommendation_for,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Demo fallback — used only when the DB is empty. The seed script writes
# these to the DB; demo entries keep the UI usable before seeding.
# ---------------------------------------------------------------------------
_DEMO_OPPORTUNITIES: list[dict[str, Any]] = [
    {
        "id": "demo-001",
        "slug": "ai-ecommerce-avatar",
        "title": "AI Ecommerce Avatar",
        "summary": "Generate AI product avatars for cross-border ecommerce listings.",
        "category": "AI SaaS",
        "market": "Cross-border ecommerce",
        "target_user": "Shopify / DTC sellers",
        "source_count": 8,
        "score": 86.45,
        "total_score": 86.45,
        "trend_score": 92,
        "demand_score": 88,
        "monetization_score": 85,
        "competition_gap_score": 78,
        "china_gap_score": 87,
        "execution_score": 82,
        "status": "researched",
        "recommendation": "strongly_recommend",
    },
    {
        "id": "demo-002",
        "slug": "ai-browser-agent",
        "title": "AI Browser Agent",
        "summary": "Browser-based autonomous agents for enterprise data extraction.",
        "category": "Agent",
        "market": "Enterprise data extraction",
        "target_user": "Operations teams",
        "source_count": 6,
        "score": 76.5,
        "total_score": 76.5,
        "trend_score": 90,
        "demand_score": 80,
        "monetization_score": 75,
        "competition_gap_score": 65,
        "china_gap_score": 70,
        "execution_score": 65,
        "status": "detected",
        "recommendation": "recommend",
    },
    {
        "id": "demo-003",
        "slug": "ai-video-localization",
        "title": "AI Video Localization",
        "summary": "Translate and dub long-form video into 30+ languages with lip-sync.",
        "category": "AI Media",
        "market": "Creator economy",
        "target_user": "YouTubers / course creators",
        "source_count": 5,
        "score": 76.7,
        "total_score": 76.7,
        "trend_score": 84,
        "demand_score": 82,
        "monetization_score": 78,
        "competition_gap_score": 72,
        "china_gap_score": 60,
        "execution_score": 70,
        "status": "detected",
        "recommendation": "recommend",
    },
]


def _demo_to_response(d: dict[str, Any]) -> OpportunityResponse:
    return OpportunityResponse(**d)


async def _list_from_db(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    category: str | None,
    q: str | None,
    sort: str,
) -> tuple[list[OpportunityResponse], int] | None:
    repo = OpportunityRepository(session)
    try:
        rows, total = await repo.list_paginated(
            limit=limit, offset=offset, category=category, q=q, sort=sort,
        )
    except Exception:
        return None
    if not rows and total == 0 and offset == 0:
        return None  # DB is empty — caller will fall back to demo
    items = [
        OpportunityResponse(
            id=r.id,
            slug=r.slug,
            title=r.title,
            summary=r.summary,
            category=r.category,
            market=r.market,
            target_user=r.target_user,
            source_count=r.source_count,
            score=r.total_score,
            total_score=r.total_score,
            trend_score=r.trend_score,
            demand_score=r.demand_score,
            monetization_score=r.monetization_score,
            competition_gap_score=r.competition_gap_score,
            china_gap_score=r.china_gap_score,
            execution_score=r.execution_score,
            status=r.status,
            recommendation=recommendation_for(r.total_score),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]
    return items, total


async def _list_from_demo(
    *,
    limit: int,
    offset: int,
    category: str | None,
    q: str | None,
    sort: str,
) -> tuple[list[OpportunityResponse], int]:
    # Phase 17 — demo entries have no Signal rows so the
    # signal_score subquery is always NULL for them; the demo path
    # sorts by total_score regardless of the requested sort key. Real
    # DB queries get the proper subquery ordering.
    items = _DEMO_OPPORTUNITIES
    if category:
        items = [o for o in items if (o.get("category") or "").lower() == category.lower()]
    if q:
        pattern = q.strip().lower()
        if pattern:
            items = [
                o for o in items
                if any(
                    pattern in (o.get(field) or "").lower()
                    for field in ("title", "summary", "category", "target_user")
                )
            ]
    page = items[offset : offset + limit]
    return [_demo_to_response(o) for o in page], len(items)


@router.get("/opportunities", response_model=OpportunityListResponse, summary="List opportunities")
async def list_opportunities(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    category: str | None = Query(None, description="Filter by category"),
    q: str | None = Query(
        None,
        description="Phase 16D — keyword filter (title / summary / category / target_user)",
    ),
    sort: str = Query(
        "total_score",
        pattern="^(total_score|signal_score)$",
        description=(
            "Phase 17D — sort key. "
            "'total_score' = classic LLM blend (default). "
            "'signal_score' = AVG(signal.signal_score) across "
            "all signals linked to the opportunity."
        ),
    ),
    session: AsyncSession = Depends(get_session),
) -> OpportunityListResponse:
    db_result = await _list_from_db(
        session,
        limit=limit,
        offset=offset,
        category=category,
        q=q,
        sort=sort,
    )
    if db_result is None:
        items, total = await _list_from_demo(
            limit=limit, offset=offset, category=category, q=q, sort=sort,
        )
    else:
        items, total = db_result

    return OpportunityListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        generated_at=datetime.now(timezone.utc),
    )


@router.get(
    "/opportunities/{opportunity_id}",
    response_model=OpportunityResponse,
    summary="Opportunity detail",
)
async def get_opportunity(
    opportunity_id: str,
    session: AsyncSession = Depends(get_session),
) -> OpportunityResponse:
    repo = OpportunityRepository(session)

    # Try numeric id first.
    try:
        numeric_id = int(opportunity_id)
        opp = await repo.get_by_id(numeric_id)
        if opp is not None:
            return _opp_to_response(opp)
    except (ValueError, TypeError):
        pass
    except Exception:  # noqa: BLE001
        pass  # DB unavailable — fall through to demo + slug fallback.

    # Then slug — also tolerant of DB outages.
    try:
        opp = await repo.get_by_slug(opportunity_id)
        if opp is not None:
            return _opp_to_response(opp)
    except Exception:  # noqa: BLE001
        pass

    # Demo fallback for synthetic ids.
    for demo in _DEMO_OPPORTUNITIES:
        if demo["id"] == opportunity_id or demo["slug"] == opportunity_id:
            return _demo_to_response(demo)

    raise HTTPException(status_code=404, detail="opportunity not found")


def _opp_to_response(opp) -> OpportunityResponse:  # type: ignore[no-untyped-def]
    return OpportunityResponse(
        id=opp.id,
        slug=opp.slug,
        title=opp.title,
        summary=opp.summary,
        category=opp.category,
        market=opp.market,
        target_user=opp.target_user,
        source_count=opp.source_count,
        score=opp.total_score,
        total_score=opp.total_score,
        trend_score=opp.trend_score,
        demand_score=opp.demand_score,
        monetization_score=opp.monetization_score,
        competition_gap_score=opp.competition_gap_score,
        china_gap_score=opp.china_gap_score,
        execution_score=opp.execution_score,
        status=opp.status,
        recommendation=recommendation_for(opp.total_score),
        created_at=opp.created_at,
        updated_at=opp.updated_at,
    )


@router.post(
    "/opportunities/{opportunity_id}/research",
    response_model=TriggerResearchResponse,
    summary="Trigger deep research for an opportunity",
    status_code=202,
)
async def trigger_research(opportunity_id: str) -> TriggerResearchResponse:
    """Phase 2 stub: only signals "queued". The real ResearchJob worker
    lands in Phase 7 (deep research engine)."""
    return TriggerResearchResponse(
        opportunity_id=opportunity_id,
        status="queued",
        job_id=f"job-{opportunity_id}",
    )


# Re-exported for the demo-detail view in `/opportunities/{id}`.
RESEARCH_TRIGGER_THRESHOLD_VALUE = RESEARCH_TRIGGER_THRESHOLD  # noqa: F841